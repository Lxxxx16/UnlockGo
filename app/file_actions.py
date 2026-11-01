from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from app import i18n
from app.file_operations import FileOperationError, linked_path_component
from app.models import LockState, ProcessInfo
from app.process_actions import terminate_process
from app.scanner import find_targets_locks


class FileActionError(RuntimeError):
    """A file operation could not be completed safely."""


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


_FO_DELETE = 3
_FOF_ALLOWUNDO = 0x0040
_FOF_SILENT = 0x0004
_FOF_NOERRORUI = 0x0400
_FOF_WANTNUKEWARNING = 0x4000
_COINIT_APARTMENTTHREADED = 0x2

_DE_OPCANCELLED = 0x75
_DE_ACCESSDENIEDSRC = 0x78
_DE_INVALIDFILES = 0x7C
_ERROR_ACCESS_DENIED = 5
_ERROR_SHARING_VIOLATION = 32


def _initialize_com() -> bool:
    try:
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        ole32.CoInitializeEx.restype = ctypes.c_long
        result = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    except (AttributeError, OSError):
        return False
    return int(result) >= 0


def _uninitialize_com() -> None:
    try:
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        ole32.CoUninitialize.argtypes = []
        ole32.CoUninitialize.restype = None
        ole32.CoUninitialize()
    except (AttributeError, OSError):
        pass


def preflight_recycle_target(
    file_path: str | os.PathLike[str],
    *,
    allow_directory: bool,
) -> Path:
    """Validate a recycle target without changing the filesystem."""

    if os.name != "nt":
        raise FileActionError(i18n.tr("仅支持在 Windows 上将目标移入回收站。"))

    supplied = Path(file_path)
    try:
        linked = linked_path_component(supplied)
    except FileOperationError as exc:
        raise FileActionError(str(exc)) from exc
    if linked is not None:
        raise FileActionError(
            i18n.tr(
                "为安全起见，不支持回收符号链接或目录联接目标：{path}",
                path=linked,
            )
        )
    try:
        target = supplied.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FileActionError(
            i18n.tr("目标不存在或无法访问：{path}", path=supplied)
        ) from exc
    if target.is_dir():
        if not allow_directory:
            raise FileActionError(i18n.tr("目标不是普通文件，不能执行回收操作。"))
        if target.parent == target:
            raise FileActionError(i18n.tr("为安全起见，不能回收驱动器根目录。"))
    elif not target.is_file():
        raise FileActionError(
            i18n.tr("目标不是普通文件或文件夹，不能执行回收操作。")
        )
    return target


def _recycle_path(file_path: str | os.PathLike[str], *, allow_directory: bool) -> Path:
    target = preflight_recycle_target(
        file_path,
        allow_directory=allow_directory,
    )

    com_initialized = _initialize_com()
    try:
        text = str(target)
        source = ctypes.create_unicode_buffer(len(text) + 2)
        source.value = text

        operation = _SHFILEOPSTRUCTW()
        operation.hwnd = None
        operation.wFunc = _FO_DELETE
        operation.pFrom = ctypes.cast(source, wintypes.LPCWSTR)
        operation.pTo = None
        operation.fFlags = (
            _FOF_ALLOWUNDO
            | _FOF_SILENT
            | _FOF_NOERRORUI
            | _FOF_WANTNUKEWARNING
        )

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.SHFileOperationW.argtypes = [ctypes.POINTER(_SHFILEOPSTRUCTW)]
        shell32.SHFileOperationW.restype = ctypes.c_int
        result = int(shell32.SHFileOperationW(ctypes.byref(operation)))

        if result == 0 and operation.fAnyOperationsAborted:
            raise FileActionError(i18n.tr("回收操作已取消。"))
        if result == _DE_OPCANCELLED:
            raise FileActionError(i18n.tr("回收操作已取消。"))
        if result in (_ERROR_SHARING_VIOLATION, 0x20):
            raise FileActionError(
                i18n.tr("目标仍被其他程序占用，无法移入回收站。")
            )
        if result in (_ERROR_ACCESS_DENIED, _DE_ACCESSDENIEDSRC):
            raise FileActionError(i18n.tr("权限不足，无法将目标移入回收站。"))
        if result == _DE_INVALIDFILES:
            raise FileActionError(i18n.tr("目标路径无效或目标已被移动。"))
        if result != 0:
            raise FileActionError(
                i18n.tr("Windows 回收目标失败，错误代码：{code}。", code=result)
            )
    except FileActionError:
        raise
    except (AttributeError, OSError) as exc:
        raise FileActionError(
            i18n.tr("调用 Windows 回收接口失败：{message}", message=exc)
        ) from exc
    finally:
        if com_initialized:
            _uninitialize_com()

    if target.exists():
        raise FileActionError(
            i18n.tr("Windows 未完成回收操作，目标仍然存在。")
        )
    return target


def recycle_file(file_path: str | os.PathLike[str]) -> Path:
    """Move one regular file to the Windows Recycle Bin."""

    return _recycle_path(file_path, allow_directory=False)


def recycle_target(file_path: str | os.PathLike[str]) -> Path:
    """Move a regular file or folder to the Windows Recycle Bin."""

    return _recycle_path(file_path, allow_directory=True)

def terminate_and_recycle(
    process: ProcessInfo,
    file_path: str,
    other_processes: tuple[ProcessInfo, ...] = (),
) -> Path:
    """Terminate one verified process and recycle its target file if unlocked."""

    if other_processes:
        names: list[str] = []
        for item in other_processes:
            label = f"{item.name} (PID {item.pid})"
            if label not in names:
                names.append(label)
        details = ", ".join(names[:5])
        if len(names) > 5:
            details += i18n.tr(" 等")
        raise FileActionError(
            i18n.tr("仍有其他进程占用该文件，已拒绝删除：{details}", details=details)
        )

    preflight_recycle_target(file_path, allow_directory=False)
    terminate_process(process)

    try:
        remaining = find_targets_locks([file_path], recursive=True)
    except Exception as exc:  # noqa: BLE001 - converted to a user-facing error
        raise FileActionError(
            i18n.tr("结束进程后无法重新检查占用状态：{message}", message=exc)
        ) from exc

    blocking_locks = [
        lock
        for lock in remaining.locks
        if lock.lock_state == LockState.BLOCKING
    ]
    if blocking_locks:
        names: list[str] = []
        for lock in blocking_locks:
            label = f"{lock.process.name} (PID {lock.process.pid})"
            if label not in names:
                names.append(label)
        details = ", ".join(names[:5])
        if len(names) > 5:
            details += i18n.tr(" 等")
        raise FileActionError(
            i18n.tr("仍有其他进程占用该文件，已拒绝删除：{details}", details=details)
        )

    # Non-blocking and unknown results are not proof that the target cannot
    # be modified. SHFileOperationW performs the final authoritative check.
    return recycle_file(file_path)
