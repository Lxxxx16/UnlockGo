from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable
from pathlib import Path

from app import i18n
from app.models import FileIdentity

if sys.platform == "win32":
    from ctypes import wintypes
else:  # pragma: no cover - the application targets Windows
    wintypes = None  # type: ignore[assignment]


CancelCheck = Callable[[], bool]
PathFilter = Callable[[str], bool]

SYSTEM_EXTENDED_HANDLE_INFORMATION = 64
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
STATUS_BUFFER_OVERFLOW = 0x80000005
PROCESS_DUP_HANDLE = 0x0040
DUPLICATE_SAME_ACCESS = 0x00000002
FILE_READ_ATTRIBUTES = 0x0080
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_TYPE_DISK = 1
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_HANDLE_BUFFER = 512 * 1024 * 1024


class HandleScanError(OSError):
    """Raised when the Windows system handle table cannot be queried."""


class HandleScanCancelledError(RuntimeError):
    """Raised when the user cancels a system handle scan."""


@dataclass(frozen=True, slots=True)
class FileHandleInfo:
    """One open File handle owned by a process."""

    pid: int
    handle_value: int
    file_path: str
    granted_access: int = 0
    file_identity: FileIdentity | None = None


@dataclass(frozen=True, slots=True)
class FileHandleScanResult:
    """The readable File handles and processes that denied access."""

    handles: tuple[FileHandleInfo, ...]
    inaccessible_processes: int = 0
    scan_succeeded: bool = True


@dataclass(frozen=True, slots=True)
class _SystemHandleEntry:
    pid: int
    handle_value: int
    object_type_index: int
    granted_access: int


if sys.platform == "win32":

    class _SystemHandleTableEntryInfoEx(ctypes.Structure):
        _fields_ = [
            ("Object", ctypes.c_void_p),
            ("UniqueProcessId", ctypes.c_size_t),
            ("HandleValue", ctypes.c_size_t),
            ("GrantedAccess", wintypes.ULONG),
            ("CreatorBackTraceIndex", wintypes.USHORT),
            ("ObjectTypeIndex", wintypes.USHORT),
            ("HandleAttributes", wintypes.ULONG),
            ("Reserved", wintypes.ULONG),
        ]


    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]


    @lru_cache(maxsize=1)
    def _ntdll():
        dll = ctypes.WinDLL("ntdll", use_last_error=True)
        dll.NtQuerySystemInformation.argtypes = [
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
        ]
        dll.NtQuerySystemInformation.restype = ctypes.c_long
        return dll


    @lru_cache(maxsize=1)
    def _kernel32():
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.CloseHandle.argtypes = [wintypes.HANDLE]
        dll.CloseHandle.restype = wintypes.BOOL
        dll.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        dll.CreateFileW.restype = wintypes.HANDLE
        dll.DuplicateHandle.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        dll.DuplicateHandle.restype = wintypes.BOOL
        dll.GetCurrentProcess.argtypes = []
        dll.GetCurrentProcess.restype = wintypes.HANDLE
        dll.GetFileType.argtypes = [wintypes.HANDLE]
        dll.GetFileType.restype = wintypes.DWORD
        dll.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        ]
        dll.GetFileInformationByHandle.restype = wintypes.BOOL
        dll.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        dll.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        dll.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        dll.OpenProcess.restype = wintypes.HANDLE
        return dll


def _raise_if_cancelled(should_cancel: CancelCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise HandleScanCancelledError(i18n.tr("系统句柄扫描已取消。"))


def _strip_extended_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def normalize_handle_path(path: str | os.PathLike[str]) -> str:
    """Normalize a path returned by Windows for a duplicated handle."""

    cleaned = _strip_extended_prefix(os.fspath(path))
    try:
        resolved = Path(cleaned).resolve(strict=False)
    except OSError:
        resolved = Path(cleaned).absolute()
    return os.path.normcase(os.path.normpath(str(resolved)))


def _query_system_handles() -> tuple[_SystemHandleEntry, ...]:
    api = _ntdll()
    buffer_size = 1024 * 1024
    for _ in range(12):
        buffer = ctypes.create_string_buffer(buffer_size)
        return_length = wintypes.ULONG()
        status = api.NtQuerySystemInformation(
            SYSTEM_EXTENDED_HANDLE_INFORMATION,
            buffer,
            buffer_size,
            ctypes.byref(return_length),
        )
        unsigned_status = int(status) & 0xFFFFFFFF
        if unsigned_status == 0:
            return _parse_system_handles(buffer)

        if unsigned_status not in (
            STATUS_INFO_LENGTH_MISMATCH,
            STATUS_BUFFER_OVERFLOW,
        ):
            raise HandleScanError(
                i18n.tr(
                    "NtQuerySystemInformation 失败：{status}",
                    status=f"0x{unsigned_status:08X}",
                )
            )

        required = max(buffer_size * 2, int(return_length.value) + 64 * 1024)
        if required > MAX_HANDLE_BUFFER:
            raise HandleScanError(i18n.tr("系统句柄表过大，已停止扫描。"))
        buffer_size = required

    raise HandleScanError(
        i18n.tr("系统句柄表大小持续变化，无法稳定读取。")
    )


def _parse_system_handles(buffer: ctypes.Array[ctypes.c_char]) -> tuple[_SystemHandleEntry, ...]:
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    header_size = pointer_size * 2
    entry_size = ctypes.sizeof(_SystemHandleTableEntryInfoEx)
    if len(buffer) < header_size or entry_size == 0:
        return ()

    declared_count = int(ctypes.c_size_t.from_buffer(buffer, 0).value)
    available_count = max(0, (len(buffer) - header_size) // entry_size)
    count = min(declared_count, available_count)

    entries: list[_SystemHandleEntry] = []
    for index in range(count):
        raw = _SystemHandleTableEntryInfoEx.from_buffer(
            buffer,
            header_size + index * entry_size,
        )
        pid = int(raw.UniqueProcessId)
        handle_value = int(raw.HandleValue)
        if pid <= 0 or handle_value <= 0:
            continue
        entries.append(
            _SystemHandleEntry(
                pid=pid,
                handle_value=handle_value,
                object_type_index=int(raw.ObjectTypeIndex),
                granted_access=int(raw.GrantedAccess),
            )
        )
    return tuple(entries)


def _open_probe_file_handle() -> int:
    api = _kernel32()
    candidates = [sys.executable, os.path.abspath(__file__)]
    for candidate in candidates:
        handle = api.CreateFileW(
            candidate,
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle and int(handle) != INVALID_HANDLE_VALUE:
            return int(handle)
    raise HandleScanError(i18n.tr("无法创建 File 类型探针句柄。"))


def _find_file_type_index(
    entries: tuple[_SystemHandleEntry, ...],
    probe_handle: int,
) -> int:
    current_pid = os.getpid()
    for entry in entries:
        if entry.pid == current_pid and entry.handle_value == probe_handle:
            return entry.object_type_index
    raise HandleScanError(
        i18n.tr("无法在系统句柄表中定位 File 类型探针。")
    )


def _path_from_handle(handle: int) -> str:
    api = _kernel32()
    if int(api.GetFileType(wintypes.HANDLE(handle))) != FILE_TYPE_DISK:
        return ""

    size = 1024
    for _ in range(4):
        buffer = ctypes.create_unicode_buffer(size)
        length = int(
            api.GetFinalPathNameByHandleW(
                wintypes.HANDLE(handle),
                buffer,
                len(buffer),
                0,
            )
        )
        if length == 0:
            return ""
        if length < len(buffer):
            return _strip_extended_prefix(buffer.value)
        size = length + 1
    return ""


def file_identity_from_handle(handle: int) -> FileIdentity | None:
    """Return the volume serial and File ID for an open Windows handle."""

    if sys.platform != "win32":
        return None

    information = _ByHandleFileInformation()
    if not _kernel32().GetFileInformationByHandle(
        wintypes.HANDLE(handle),
        ctypes.byref(information),
    ):
        return None
    return FileIdentity(
        volume_serial=int(information.dwVolumeSerialNumber),
        file_index_high=int(information.nFileIndexHigh),
        file_index_low=int(information.nFileIndexLow),
    )


def file_identity_from_path(path: str | os.PathLike[str]) -> FileIdentity | None:
    """Open a file or directory without modifying it and read its File ID."""

    if sys.platform != "win32":
        return None

    api = _kernel32()
    handle = api.CreateFileW(
        os.fspath(path),
        FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if not handle or int(handle) == INVALID_HANDLE_VALUE:
        return None
    try:
        return file_identity_from_handle(int(handle))
    finally:
        api.CloseHandle(handle)


def enumerate_file_handles(
    *,
    pid_filter: set[int] | None = None,
    identity_filter: PathFilter | None = None,
    should_cancel: CancelCheck | None = None,
) -> FileHandleScanResult:
    """Enumerate readable File handles without closing source handles."""

    if sys.platform != "win32":
        return FileHandleScanResult(handles=(), scan_succeeded=False)
    if pid_filter is not None and not pid_filter:
        return FileHandleScanResult(handles=())

    _raise_if_cancelled(should_cancel)
    api = _kernel32()
    probe_handle = _open_probe_file_handle()
    try:
        entries = _query_system_handles()
        file_type_index = _find_file_type_index(entries, probe_handle)
    finally:
        api.CloseHandle(wintypes.HANDLE(probe_handle))

    file_entries = [
        entry
        for entry in entries
        if entry.object_type_index == file_type_index
        and (pid_filter is None or entry.pid in pid_filter)
    ]
    by_pid: dict[int, list[_SystemHandleEntry]] = {}
    for entry in file_entries:
        by_pid.setdefault(entry.pid, []).append(entry)

    handles: list[FileHandleInfo] = []
    inaccessible_pids: set[int] = set()
    processed = 0
    current_process = api.GetCurrentProcess()

    for pid, process_entries in by_pid.items():
        _raise_if_cancelled(should_cancel)
        source_process = api.OpenProcess(PROCESS_DUP_HANDLE, False, pid)
        if not source_process:
            inaccessible_pids.add(pid)
            continue

        try:
            for entry in process_entries:
                processed += 1
                if processed % 256 == 0:
                    _raise_if_cancelled(should_cancel)

                duplicated = wintypes.HANDLE()
                if not api.DuplicateHandle(
                    source_process,
                    wintypes.HANDLE(entry.handle_value),
                    current_process,
                    ctypes.byref(duplicated),
                    0,
                    False,
                    DUPLICATE_SAME_ACCESS,
                ):
                    continue

                try:
                    path = _path_from_handle(int(duplicated.value))
                    identity = (
                        file_identity_from_handle(int(duplicated.value))
                        if path
                        and (identity_filter is None or identity_filter(path))
                        else None
                    )
                finally:
                    api.CloseHandle(duplicated)

                if not path:
                    continue
                handles.append(
                    FileHandleInfo(
                        pid=pid,
                        handle_value=entry.handle_value,
                        file_path=path,
                        granted_access=entry.granted_access,
                        file_identity=identity,
                    )
                )
        finally:
            api.CloseHandle(source_process)

    handles.sort(
        key=lambda item: (
            normalize_handle_path(item.file_path),
            item.pid,
            item.handle_value,
        )
    )
    return FileHandleScanResult(
        handles=tuple(handles),
        inaccessible_processes=len(inaccessible_pids),
    )
