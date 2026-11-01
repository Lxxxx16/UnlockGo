from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    from ctypes import wintypes
else:  # pragma: no cover - the application targets Windows
    wintypes = None  # type: ignore[assignment]


ERROR_SUCCESS = 0
ERROR_MORE_DATA = 234
CCH_RM_SESSION_KEY = 32
CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63


class RestartManagerError(OSError):
    """Raised when a Windows Restart Manager operation fails."""


@dataclass(frozen=True, slots=True)
class RestartManagerProcess:
    pid: int
    app_name: str
    application_type: int
    restartable: bool


if sys.platform == "win32":

    class _RmUniqueProcess(ctypes.Structure):
        _fields_ = [
            ("dwProcessId", wintypes.DWORD),
            ("ProcessStartTime", wintypes.FILETIME),
        ]


    class _RmProcessInfo(ctypes.Structure):
        _fields_ = [
            ("Process", _RmUniqueProcess),
            ("strAppName", wintypes.WCHAR * (CCH_RM_MAX_APP_NAME + 1)),
            ("strServiceShortName", wintypes.WCHAR * (CCH_RM_MAX_SVC_NAME + 1)),
            ("ApplicationType", wintypes.ULONG),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]


    @lru_cache(maxsize=1)
    def _api() -> Any:
        dll = ctypes.WinDLL("Rstrtmgr.dll")

        dll.RmStartSession.argtypes = [
            ctypes.POINTER(wintypes.DWORD),
            wintypes.DWORD,
            wintypes.LPWSTR,
        ]
        dll.RmStartSession.restype = wintypes.DWORD

        dll.RmRegisterResources.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            ctypes.POINTER(wintypes.LPCWSTR),
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.POINTER(wintypes.LPCWSTR),
        ]
        dll.RmRegisterResources.restype = wintypes.DWORD

        dll.RmGetList.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(_RmProcessInfo),
            ctypes.POINTER(wintypes.DWORD),
        ]
        dll.RmGetList.restype = wintypes.DWORD

        dll.RmEndSession.argtypes = [wintypes.DWORD]
        dll.RmEndSession.restype = wintypes.DWORD
        return dll


def _raise_winerror(operation: str, code: int) -> None:
    error = ctypes.WinError(code)
    raise RestartManagerError(error.errno, f"{operation} failed: {error.strerror}") from error


def find_restart_manager_processes(
    file_path: str | os.PathLike[str],
) -> tuple[RestartManagerProcess, ...]:
    """Return processes Windows Restart Manager associates with *file_path*.

    The function is intentionally non-destructive: it starts a Restart Manager
    session, registers one file, reads the affected process list, and closes the
    session. No shutdown or restart operation is requested.
    """

    if sys.platform != "win32":
        return ()

    absolute_path = str(Path(file_path).resolve(strict=False))
    api = _api()
    session = wintypes.DWORD()
    session_key = ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY + 1)
    code = api.RmStartSession(ctypes.byref(session), 0, session_key)
    if code != ERROR_SUCCESS:
        _raise_winerror("RmStartSession", code)

    try:
        file_names = (wintypes.LPCWSTR * 1)(absolute_path)
        code = api.RmRegisterResources(
            session.value,
            1,
            file_names,
            0,
            None,
            0,
            None,
        )
        if code != ERROR_SUCCESS:
            _raise_winerror("RmRegisterResources", code)

        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reboot_reasons = wintypes.DWORD(0)
        code = api.RmGetList(
            session.value,
            ctypes.byref(needed),
            ctypes.byref(count),
            None,
            ctypes.byref(reboot_reasons),
        )
        if code == ERROR_SUCCESS and needed.value == 0:
            return ()
        if code not in (ERROR_SUCCESS, ERROR_MORE_DATA):
            _raise_winerror("RmGetList", code)

        for _ in range(3):
            count = wintypes.UINT(needed.value)
            entries = (_RmProcessInfo * count.value)()
            code = api.RmGetList(
                session.value,
                ctypes.byref(needed),
                ctypes.byref(count),
                entries,
                ctypes.byref(reboot_reasons),
            )
            if code == ERROR_SUCCESS:
                return tuple(
                    RestartManagerProcess(
                        pid=int(entry.Process.dwProcessId),
                        app_name=str(entry.strAppName),
                        application_type=int(entry.ApplicationType),
                        restartable=bool(entry.bRestartable),
                    )
                    for entry in entries[: count.value]
                    if int(entry.Process.dwProcessId) > 0
                )
            if code != ERROR_MORE_DATA:
                _raise_winerror("RmGetList", code)

        raise RestartManagerError(ERROR_MORE_DATA, "RmGetList did not stabilize")
    finally:
        api.RmEndSession(session.value)
