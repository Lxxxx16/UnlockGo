from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from functools import lru_cache

from app import i18n
from app.models import LockState

if sys.platform == "win32":
    from ctypes import wintypes
else:  # pragma: no cover - the application targets Windows
    wintypes = None  # type: ignore[assignment]


DELETE = 0x00010000
FILE_READ_ATTRIBUTES = 0x0080
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
ERROR_USER_MAPPED_FILE = 1224


@dataclass(frozen=True, slots=True)
class LockProbeResult:
    """The result of a non-destructive DELETE access probe."""

    state: LockState
    detail: str
    error_code: int | None = None
    detail_source: str = ""
    detail_values: tuple[tuple[str, object], ...] = ()


if sys.platform == "win32":

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
        return dll


def _extended_path(path: str | os.PathLike[str]) -> str:
    value = os.fspath(path)
    if value.startswith("\\\\?\\") or value.startswith("\\\\.\\"):
        return value
    absolute = os.path.abspath(value)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _probe_result(
    state: LockState,
    detail_source: str,
    *,
    error_code: int | None = None,
    **values: object,
) -> LockProbeResult:
    return LockProbeResult(
        state=state,
        detail=i18n.tr(detail_source, **values),
        error_code=error_code,
        detail_source=detail_source,
        detail_values=tuple(values.items()),
    )


def probe_path_lock(path: str | os.PathLike[str]) -> LockProbeResult:
    """Check whether existing handles block DELETE access.

    Opening with DELETE does not delete the target. The temporary handle is
    closed immediately and no file disposition is changed.
    """

    if sys.platform != "win32":
        return _probe_result(
            LockState.UNKNOWN,
            "锁定探测仅支持 Windows。",
        )

    api = _kernel32()
    handle = api.CreateFileW(
        _extended_path(path),
        DELETE | FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    handle_value = int(handle)
    if handle_value == INVALID_HANDLE_VALUE:
        error_code = ctypes.get_last_error()
        if error_code in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
            return _probe_result(
                LockState.BLOCKING,
                "目标存在未共享删除权限的打开句柄或文件锁。",
                error_code=error_code,
            )
        if error_code == ERROR_USER_MAPPED_FILE:
            return _probe_result(
                LockState.BLOCKING,
                "目标仍被内存映射，无法删除。",
                error_code=error_code,
            )
        if error_code in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
            return _probe_result(
                LockState.UNKNOWN,
                "目标已不存在或路径不可用。",
                error_code=error_code,
            )
        if error_code == ERROR_ACCESS_DENIED:
            return _probe_result(
                LockState.UNKNOWN,
                "当前权限不足，无法确认目标是否存在访问锁定。",
                error_code=error_code,
            )
        return _probe_result(
            LockState.UNKNOWN,
            "锁定探测失败，Windows 错误码：{code}",
            error_code=error_code,
            code=error_code,
        )

    api.CloseHandle(handle)
    return _probe_result(
        LockState.NON_BLOCKING,
        "目标可以获取 DELETE 权限，本次探测未发现访问锁定。",
    )
