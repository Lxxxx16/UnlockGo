from __future__ import annotations

import ctypes
import math
import os
import sys
from dataclasses import dataclass
from functools import lru_cache

import psutil

from app import i18n
from app.models import FileIdentity, LockState, ProcessInfo
from app.process_actions import (
    ProcessChangedError,
    ProcessActionError,
    _verify_process_identity,
    process_risk,
)
from app.windows_handles import (
    DUPLICATE_SAME_ACCESS,
    PROCESS_DUP_HANDLE,
    FileHandleInfo,
    enumerate_file_handles,
    file_identity_from_handle,
    file_identity_from_path,
    normalize_handle_path,
)
from app.windows_lock_probe import LockProbeResult, probe_path_lock

if sys.platform == "win32":
    from ctypes import wintypes
else:  # pragma: no cover - the application targets Windows
    wintypes = None  # type: ignore[assignment]


DUPLICATE_CLOSE_SOURCE = 0x00000001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87

_WINDOWS_FILETIME_EPOCH_OFFSET = 11644473600.0
_CREATE_TIME_TOLERANCE_SECONDS = 0.02


class HandleReleaseError(RuntimeError):
    """A source process handle could not be released safely."""


class HandleReleaseProtectedError(HandleReleaseError):
    """The source process is excluded by UnlockGo's safety policy."""


class HandleSnapshotStaleError(HandleReleaseError):
    """The scanned PID, handle value, path, or file identity has changed."""


@dataclass(frozen=True, slots=True)
class HandleReleaseResult:
    """The verified result of releasing one source process handle."""

    process: ProcessInfo
    handle_value: int
    target_path: str
    lock_probe: LockProbeResult

    @property
    def unlocked(self) -> bool:
        return self.lock_probe.state == LockState.NON_BLOCKING


if sys.platform == "win32":

    @lru_cache(maxsize=1)
    def _kernel32():
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.CloseHandle.argtypes = [wintypes.HANDLE]
        dll.CloseHandle.restype = wintypes.BOOL
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
        dll.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        dll.GetProcessTimes.restype = wintypes.BOOL
        dll.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        dll.OpenProcess.restype = wintypes.HANDLE
        return dll


def _filetime_to_unix_seconds(value: wintypes.FILETIME) -> float:
    ticks = (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
    return ticks / 10_000_000.0 - _WINDOWS_FILETIME_EPOCH_OFFSET


def _verify_opened_process_identity(
    process_handle: int,
    expected_create_time: float,
) -> None:
    api = _kernel32()
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    if not api.GetProcessTimes(
        wintypes.HANDLE(process_handle),
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        error_code = ctypes.get_last_error()
        raise HandleReleaseError(
            i18n.tr(
                "无法读取进程启动时间，不能安全释放句柄。Windows 错误码：{code}",
                code=error_code,
            )
        )

    actual_create_time = _filetime_to_unix_seconds(creation)
    if not math.isclose(
        actual_create_time,
        expected_create_time,
        rel_tol=0.0,
        abs_tol=_CREATE_TIME_TOLERANCE_SECONDS,
    ):
        raise HandleSnapshotStaleError(
            i18n.tr("PID 已被其他进程使用，请重新扫描后再释放句柄。")
        )


def _find_snapshot_handle(
    process: ProcessInfo,
    handle_value: int,
    target_path: str,
    expected_identity: FileIdentity,
) -> FileHandleInfo:
    result = enumerate_file_handles(pid_filter={process.pid})
    if not result.scan_succeeded:
        raise HandleReleaseError(
            i18n.tr("无法枚举系统句柄表，请以管理员身份重新扫描后再尝试。")
        )

    expected_path = normalize_handle_path(target_path)
    matches = [
        item
        for item in result.handles
        if item.pid == process.pid and item.handle_value == handle_value
    ]
    if not matches:
        raise HandleSnapshotStaleError(
            i18n.tr("目标句柄已关闭或句柄号已变化，请重新扫描后再尝试。")
        )
    identity_matches = [
        item
        for item in matches
        if item.file_identity == expected_identity
    ]
    if not identity_matches:
        raise HandleSnapshotStaleError(
            i18n.tr("该句柄现在指向其他文件对象，请重新扫描后再尝试。")
        )
    if not any(
        normalize_handle_path(item.file_path) == expected_path for item in matches
    ):
        raise HandleSnapshotStaleError(
            i18n.tr("该句柄现在指向其他文件，请重新扫描后再尝试。")
        )
    return identity_matches[0]


def _verify_target_identity(
    target_path: str,
    expected_identity: FileIdentity,
) -> None:
    actual_identity = file_identity_from_path(target_path)
    if actual_identity is None:
        raise HandleReleaseError(
            i18n.tr("无法读取目标文件身份，请以管理员身份重新扫描后再尝试。")
        )
    if actual_identity != expected_identity:
        raise HandleSnapshotStaleError(
            i18n.tr("目标文件已被替换或移动，请重新扫描后再尝试。")
        )


def _preflight_duplicate_identity(
    source_process: int,
    handle_value: int,
) -> FileIdentity:
    api = _kernel32()
    duplicated = wintypes.HANDLE()
    try:
        if not api.DuplicateHandle(
            source_process,
            wintypes.HANDLE(handle_value),
            api.GetCurrentProcess(),
            ctypes.byref(duplicated),
            0,
            False,
            DUPLICATE_SAME_ACCESS,
        ):
            error_code = ctypes.get_last_error()
            raise HandleReleaseError(
                i18n.tr(
                    "系统拒绝预检该句柄。Windows 错误码：{code}",
                    code=error_code,
                )
            )
        identity = file_identity_from_handle(int(duplicated.value))
        if identity is None:
            raise HandleReleaseError(
                i18n.tr("无法读取待释放句柄的文件身份，不能安全释放。")
            )
        return identity
    finally:
        if duplicated.value:
            api.CloseHandle(duplicated)


def _validate_request(process: ProcessInfo, handle_value: int) -> None:
    if process.pid <= 0:
        raise HandleReleaseError(i18n.tr("进程 PID 无效。"))
    if handle_value <= 0:
        raise HandleSnapshotStaleError(
            i18n.tr("句柄值无效，请重新扫描后再尝试。")
        )

    risk = process_risk(process)
    if risk == "self":
        raise HandleReleaseProtectedError(
            i18n.tr("不能释放 UnlockGo 自身的句柄。")
        )
    if risk == "critical":
        raise HandleReleaseProtectedError(
            i18n.tr(
                "{name} 是 Windows 关键进程，UnlockGo 不会释放它的句柄。",
                name=process.name,
            )
        )
    if process.create_time is None:
        raise ProcessChangedError(
            i18n.tr(
                "无法确认该进程的启动时间，请以管理员身份重新扫描后再尝试。"
            )
        )


def release_file_handle(
    process: ProcessInfo,
    handle_value: int,
    target_path: str,
    *,
    expected_identity: FileIdentity,
    allow_high_risk: bool = False,
) -> HandleReleaseResult:
    """Close one verified source-process handle without terminating the process.

    This is a best-effort user-mode operation. Windows does not provide an
    atomic "close this handle if it still names this file" operation. The
    caller's scan-time FileIdentity must match both the current target path and
    the source handle before and during the close. A driver is still required
    for stronger synchronization, protected processes, and kernel locks.
    """

    if sys.platform != "win32":
        raise HandleReleaseError(i18n.tr("句柄释放仅支持 Windows。"))

    _validate_request(process, handle_value)
    if process_risk(process) == "high" and not allow_high_risk:
        raise HandleReleaseProtectedError(
            i18n.tr(
                "{name} 属于高风险进程。释放句柄需要额外确认。",
                name=process.name,
            )
        )

    try:
        live_process = psutil.Process(process.pid)
        _verify_process_identity(live_process, process)
    except ProcessActionError:
        raise
    except psutil.NoSuchProcess as exc:
        raise HandleSnapshotStaleError(
            i18n.tr("该进程已经退出，请重新扫描。")
        ) from exc
    except (psutil.AccessDenied, OSError) as exc:
        raise HandleReleaseError(
            i18n.tr("没有权限验证该进程，请以管理员身份重新扫描后再尝试。")
        ) from exc

    _verify_target_identity(target_path, expected_identity)
    snapshot = _find_snapshot_handle(
        process,
        handle_value,
        target_path,
        expected_identity,
    )
    expected_create_time = process.create_time
    if expected_create_time is None:  # Guarded by _validate_request.
        raise HandleSnapshotStaleError(
            i18n.tr("缺少进程启动时间，无法安全释放句柄。")
        )

    api = _kernel32()
    source_process = api.OpenProcess(
        PROCESS_DUP_HANDLE | PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        process.pid,
    )
    if not source_process:
        error_code = ctypes.get_last_error()
        if error_code == ERROR_ACCESS_DENIED:
            message = i18n.tr("没有权限打开该进程，请以管理员身份运行 UnlockGo。")
        elif error_code == ERROR_INVALID_PARAMETER:
            message = i18n.tr("该进程已经退出，请重新扫描。")
        else:
            message = i18n.tr(
                "无法打开目标进程。Windows 错误码：{code}",
                code=error_code,
            )
        raise HandleReleaseError(message)

    duplicated = wintypes.HANDLE()
    try:
        _verify_opened_process_identity(
            int(source_process),
            expected_create_time,
        )
        _verify_target_identity(target_path, expected_identity)
        preflight_identity = _preflight_duplicate_identity(
            source_process,
            snapshot.handle_value,
        )
        if preflight_identity != expected_identity:
            raise HandleSnapshotStaleError(
                i18n.tr("句柄在释放前指向了其他文件对象，请重新扫描后再尝试。")
            )
        _verify_target_identity(target_path, expected_identity)

        if not api.DuplicateHandle(
            source_process,
            wintypes.HANDLE(snapshot.handle_value),
            api.GetCurrentProcess(),
            ctypes.byref(duplicated),
            0,
            False,
            DUPLICATE_SAME_ACCESS | DUPLICATE_CLOSE_SOURCE,
        ):
            error_code = ctypes.get_last_error()
            raise HandleReleaseError(
                i18n.tr(
                    "系统拒绝释放该句柄。Windows 错误码：{code}",
                    code=error_code,
                )
            )

        duplicated_identity = file_identity_from_handle(int(duplicated.value))
        if duplicated_identity != expected_identity:
            raise HandleSnapshotStaleError(
                i18n.tr("句柄在释放过程中发生变化，源句柄已关闭，请重新扫描确认。")
            )
    except OSError as exc:
        raise HandleReleaseError(
            i18n.tr("释放句柄失败：{message}", message=exc)
        ) from exc
    finally:
        if duplicated.value:
            api.CloseHandle(duplicated)
        api.CloseHandle(source_process)

    probe = probe_path_lock(target_path)
    if probe.state == LockState.BLOCKING:
        detail_source = "目标句柄已关闭，但仍有其他进程或文件映射占用，目标尚未解锁。"
    elif probe.state == LockState.UNKNOWN:
        detail_source = "目标句柄已关闭，但当前权限无法确认目标是否已经完全解锁。"
    else:
        detail_source = "目标句柄已关闭，目标当前可获取 DELETE 权限。"
    lock_probe = LockProbeResult(
        state=probe.state,
        detail=i18n.tr(detail_source),
        error_code=probe.error_code,
        detail_source=detail_source,
    )
    return HandleReleaseResult(
        process=process,
        handle_value=handle_value,
        target_path=target_path,
        lock_probe=lock_probe,
    )
