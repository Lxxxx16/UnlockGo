from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any


def is_running_as_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _prefer_windowed_python(executable: str) -> str:
    """Use pythonw.exe for source launches so UAC does not open a console."""
    if os.name != "nt":
        return executable

    python_path = Path(executable)
    if python_path.name.lower() != "python.exe":
        return executable

    windowed_python = python_path.with_name("pythonw.exe")
    if windowed_python.is_file():
        return str(windowed_python)
    return executable


def build_admin_relaunch_command(
    extra_args: Iterable[str] = (),
    *,
    executable: str | None = None,
    argv: Sequence[str] | None = None,
    frozen: bool | None = None,
) -> tuple[str, str, str]:
    """Return executable, parameters, and working directory for UAC relaunch."""

    active_executable = executable or sys.executable
    active_argv = list(argv if argv is not None else sys.argv)
    is_frozen = (
        bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    )
    pass_through = active_argv[1:] + list(extra_args)

    if is_frozen:
        parameters = subprocess.list2cmdline(pass_through)
        working_directory = str(Path(active_executable).resolve().parent)
    else:
        active_executable = _prefer_windowed_python(active_executable)
        script = Path(active_argv[0]).resolve(strict=False)
        parameters = subprocess.list2cmdline([str(script), *pass_through])
        working_directory = str(script.parent)
    return active_executable, parameters, working_directory


def request_admin_relaunch(extra_args: Iterable[str] = ()) -> bool:
    """Start a new elevated instance through the Windows UAC prompt."""

    if os.name != "nt" or is_running_as_admin():
        return False

    executable, parameters, working_directory = build_admin_relaunch_command(
        extra_args
    )
    try:
        shell32: Any = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        result = shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            parameters,
            working_directory,
            1,
        )
        return bool(result and int(result) > 32)
    except (AttributeError, OSError):
        return False
