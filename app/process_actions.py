from __future__ import annotations

import ctypes
import math
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import psutil

from app import i18n
from app.models import ProcessInfo

if sys.platform == "win32":
    from ctypes import wintypes
else:  # pragma: no cover - the application targets Windows
    wintypes = None  # type: ignore[assignment]


PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
TERMINATION_TIMEOUT_MS = 4000

_WINDOWS_FILETIME_EPOCH_OFFSET = 11644473600.0
_CREATE_TIME_TOLERANCE_SECONDS = 0.02


@dataclass(frozen=True, slots=True)
class BatchTerminationFailure:
    process: ProcessInfo
    message: str


@dataclass(frozen=True, slots=True)
class BatchTerminationResult:
    terminated: tuple[ProcessInfo, ...]
    failures: tuple[BatchTerminationFailure, ...]


class ProcessActionError(RuntimeError):
    """A process action could not be completed safely."""


class ProtectedProcessError(ProcessActionError):
    """The selected process is protected by UnlockGo."""


class ProcessChangedError(ProcessActionError):
    """The selected PID no longer refers to the scanned process."""


CRITICAL_PROCESS_NAMES = frozenset(
    {
        "system",
        "system idle process",
        "idle",
        "registry",
        "memory compression",
        "secure system",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "services.exe",
        "lsass.exe",
        "lsaiso.exe",
        "winlogon.exe",
        "svchost.exe",
        "fontdrvhost.exe",
    }
)

HIGH_RISK_PROCESS_NAMES = frozenset(
    {
        "explorer.exe",
        "dwm.exe",
        "spoolsv.exe",
        "sihost.exe",
        "taskhostw.exe",
        "ctfmon.exe",
        "startmenuexperiencehost.exe",
        "shellexperiencehost.exe",
        "searchhost.exe",
    }
)


if sys.platform == "win32":

    @lru_cache(maxsize=1)
    def _kernel32():
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.CloseHandle.argtypes = [wintypes.HANDLE]
        dll.CloseHandle.restype = wintypes.BOOL
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
        dll.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        dll.TerminateProcess.restype = wintypes.BOOL
        dll.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        dll.WaitForSingleObject.restype = wintypes.DWORD
        return dll


def _normalized_name(name: str) -> str:
    return Path(name).name.strip().casefold()


def process_risk(process: ProcessInfo) -> str:
    """Return ``self``, ``critical``, ``high``, or ``normal`` for a process."""

    if process.pid == os.getpid():
        return "self"
    name = _normalized_name(process.name)
    if name in CRITICAL_PROCESS_NAMES:
        return "critical"
    if name in HIGH_RISK_PROCESS_NAMES:
        return "high"
    return "normal"


def _ensure_process_is_actionable(process_info: ProcessInfo) -> None:
    if process_info.pid <= 0:
        raise ProcessActionError(i18n.tr("进程 PID 无效。"))

    if process_info.pid == os.getpid():
        raise ProtectedProcessError(i18n.tr("不能结束 UnlockGo 自身。"))

    name = _normalized_name(process_info.name)
    if name in CRITICAL_PROCESS_NAMES:
        raise ProtectedProcessError(
            i18n.tr(
                "{name} 是 Windows 关键进程，UnlockGo 不会结束它。",
                name=process_info.name,
            )
        )


def _verify_process_identity(
    process: psutil.Process,
    expected: ProcessInfo,
) -> str:
    try:
        actual_name = process.name()
    except psutil.AccessDenied:
        actual_name = ""

    expected_name = _normalized_name(expected.name)
    if (
        actual_name
        and not expected_name.startswith("pid ")
        and _normalized_name(actual_name) != expected_name
    ):
        raise ProcessChangedError(
            i18n.tr("PID 已被其他进程使用，请重新扫描后再操作。")
        )

    if expected.create_time is None:
        raise ProcessChangedError(
            i18n.tr(
                "无法确认该进程的启动时间。请以管理员身份重新扫描后再操作。"
            )
        )

    try:
        actual_create_time = process.create_time()
    except (psutil.AccessDenied, OSError) as exc:
        raise ProcessChangedError(
            i18n.tr(
                "无法验证该进程的启动时间，请以管理员身份重新扫描后再操作。"
            )
        ) from exc
    if not math.isclose(
        actual_create_time,
        expected.create_time,
        rel_tol=0.0,
        abs_tol=0.01,
    ):
        raise ProcessChangedError(
            i18n.tr("PID 已被其他进程使用，请重新扫描后再操作。")
        )

    return actual_name or expected.name


def _filetime_to_unix_seconds(value: wintypes.FILETIME) -> float:
    ticks = (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
    return ticks / 10_000_000.0 - _WINDOWS_FILETIME_EPOCH_OFFSET


def _verify_open_process_identity(
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
        raise ProcessChangedError(
            i18n.tr(
                "无法验证该进程的启动时间，请重新扫描后再操作。"
                "Windows 错误码：{code}",
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
        raise ProcessChangedError(
            i18n.tr("PID 已被其他进程使用，请重新扫描后再操作。")
        )


def _open_process_for_termination(pid: int) -> int:
    """Open the original process object with terminate and wait rights."""

    api = _kernel32()
    handle = api.OpenProcess(
        PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
        False,
        pid,
    )
    if handle:
        return int(handle)

    error_code = ctypes.get_last_error()
    if error_code == ERROR_INVALID_PARAMETER:
        # The process disappeared between identity verification and OpenProcess.
        return 0
    if error_code == ERROR_ACCESS_DENIED:
        raise ProcessActionError(
            i18n.tr("没有权限结束该进程。请尝试以管理员身份运行 UnlockGo。")
        )
    raise ProcessActionError(
        i18n.tr(
            "无法打开目标进程。Windows 错误码：{code}",
            code=error_code,
        )
    )


def _process_has_exited(process_handle: int) -> bool:
    result = int(
        _kernel32().WaitForSingleObject(
            wintypes.HANDLE(process_handle),
            0,
        )
    )
    if result == WAIT_OBJECT_0:
        return True
    if result == WAIT_TIMEOUT:
        return False
    error_code = ctypes.get_last_error()
    raise ProcessActionError(
        i18n.tr(
            "无法确认进程退出状态。Windows 错误码：{code}",
            code=error_code,
        )
    )


def _terminate_process_handle(process_handle: int) -> bool:
    return bool(
        _kernel32().TerminateProcess(
            wintypes.HANDLE(process_handle),
            1,
        )
    )


def _wait_for_process_exit(process_handle: int) -> None:
    result = int(
        _kernel32().WaitForSingleObject(
            wintypes.HANDLE(process_handle),
            TERMINATION_TIMEOUT_MS,
        )
    )
    if result == WAIT_OBJECT_0:
        return
    if result == WAIT_TIMEOUT:
        raise ProcessActionError(
            i18n.tr("已发送结束请求，但进程仍在运行。请重新扫描后再试。")
        )
    error_code = ctypes.get_last_error()
    raise ProcessActionError(
        i18n.tr(
            "等待进程退出失败。Windows 错误码：{code}",
            code=error_code,
        )
    )


def terminate_process(process_info: ProcessInfo) -> None:
    """Terminate one verified process using its original Windows handle."""

    _ensure_process_is_actionable(process_info)
    if sys.platform != "win32":
        raise ProcessActionError(i18n.tr("结束进程仅支持 Windows。"))

    try:
        process = psutil.Process(process_info.pid)
        _verify_process_identity(process, process_info)
    except ProcessActionError:
        raise
    except psutil.NoSuchProcess:
        # The requested target is already gone, so the desired end state holds.
        return
    except psutil.AccessDenied as exc:
        raise ProcessActionError(
            i18n.tr("没有权限结束该进程。请尝试以管理员身份运行 UnlockGo。")
        ) from exc
    except OSError as exc:
        raise ProcessActionError(
            i18n.tr("无法验证该进程，请尝试以管理员身份运行 UnlockGo。")
        ) from exc

    expected_create_time = process_info.create_time
    if expected_create_time is None:  # Guarded by _verify_process_identity.
        raise ProcessChangedError(
            i18n.tr("无法确认该进程的启动时间，请重新扫描后再操作。")
        )

    api = _kernel32()
    process_handle = _open_process_for_termination(process_info.pid)
    if not process_handle:
        return

    try:
        _verify_open_process_identity(process_handle, expected_create_time)
        if _process_has_exited(process_handle):
            return

        if not _terminate_process_handle(process_handle):
            error_code = ctypes.get_last_error()
            # The process may have exited between the pre-check and the call.
            if _process_has_exited(process_handle):
                return
            if error_code == ERROR_ACCESS_DENIED:
                raise ProcessActionError(
                    i18n.tr("没有权限结束该进程。请尝试以管理员身份运行 UnlockGo。")
                )
            raise ProcessActionError(
                i18n.tr(
                    "结束进程失败。Windows 错误码：{code}",
                    code=error_code,
                )
            )

        _wait_for_process_exit(process_handle)
    finally:
        api.CloseHandle(wintypes.HANDLE(process_handle))


def terminate_processes(processes: Iterable[ProcessInfo]) -> BatchTerminationResult:
    """Terminate unique selected processes after validating every target first."""

    unique: list[ProcessInfo] = []
    seen_pids: set[int] = set()
    for process_info in processes:
        if process_info.pid in seen_pids:
            continue
        seen_pids.add(process_info.pid)
        unique.append(process_info)

    if not unique:
        raise ProcessActionError(i18n.tr("没有可结束的关联进程。"))

    for process_info in unique:
        _ensure_process_is_actionable(process_info)

    # Refuse the whole batch before touching any process when an identity is stale.
    for process_info in unique:
        try:
            process = psutil.Process(process_info.pid)
            _verify_process_identity(process, process_info)
        except psutil.NoSuchProcess as exc:
            raise ProcessChangedError(
                i18n.tr(
                    "{name} (PID {pid}) 已经退出，请重新扫描。",
                    name=process_info.name,
                    pid=process_info.pid,
                )
            ) from exc
        except ProcessActionError:
            raise
        except (psutil.AccessDenied, OSError) as exc:
            raise ProcessActionError(
                i18n.tr(
                    "无法验证 {name} (PID {pid})，" 
                    "请尝试以管理员身份运行 UnlockGo。",
                    name=process_info.name,
                    pid=process_info.pid,
                )
            ) from exc

    terminated: list[ProcessInfo] = []
    failures: list[BatchTerminationFailure] = []
    for process_info in unique:
        try:
            terminate_process(process_info)
        except ProcessActionError as exc:
            failures.append(BatchTerminationFailure(process_info, str(exc)))
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            failures.append(
                BatchTerminationFailure(
                    process_info,
                    i18n.tr(
                        "结束进程时发生未知错误：{message}",
                        message=exc,
                    ),
                )
            )
        else:
            terminated.append(process_info)

    return BatchTerminationResult(
        terminated=tuple(terminated),
        failures=tuple(failures),
    )
