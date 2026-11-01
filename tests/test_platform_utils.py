from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from app.platform_utils import build_admin_relaunch_command


class AdminRelaunchCommandTests(unittest.TestCase):
    def test_source_launch_prepends_script_path(self) -> None:
        executable, parameters, working_directory = build_admin_relaunch_command(
            ["--target", r"D:\Data\folder with space"],
            executable=r"C:\Python\python.exe",
            argv=[r"D:\Code\UnlockGo\main.py"],
            frozen=False,
        )

        self.assertEqual(executable, r"C:\Python\python.exe")
        self.assertEqual(
            parameters,
            subprocess.list2cmdline(
                [
                    r"D:\Code\UnlockGo\main.py",
                    "--target",
                    r"D:\Data\folder with space",
                ]
            ),
        )
        self.assertEqual(working_directory, r"D:\Code\UnlockGo")

    def test_source_launch_prefers_pythonw_for_console_python(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            python_dir = Path(temp_dir)
            console_python = python_dir / "python.exe"
            windowed_python = python_dir / "pythonw.exe"
            console_python.write_bytes(b"")
            windowed_python.write_bytes(b"")

            executable, _, _ = build_admin_relaunch_command(
                argv=[r"D:\Code\UnlockGo\main.py"],
                executable=str(console_python),
                frozen=False,
            )

        self.assertEqual(executable, str(windowed_python))

    def test_frozen_launch_passes_arguments_to_executable(self) -> None:
        executable, parameters, working_directory = build_admin_relaunch_command(
            ["--target", r"D:\Data\target.txt"],
            executable=r"D:\Apps\UnlockGo.exe",
            argv=[r"D:\Apps\UnlockGo.exe"],
            frozen=True,
        )

        self.assertEqual(executable, r"D:\Apps\UnlockGo.exe")
        self.assertEqual(
            parameters,
            subprocess.list2cmdline(
                ["--target", r"D:\Data\target.txt"]
            ),
        )
        self.assertEqual(working_directory, r"D:\Apps")


if __name__ == "__main__":
    unittest.main()
