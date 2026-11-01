from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import psutil

from app.file_actions import FileActionError, terminate_and_recycle
from app.models import BatchScanResult, FileLockInfo, LockState, ProcessInfo
from app.process_actions import ProcessActionError


class TerminateAndRecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = ProcessInfo(
            pid=4301,
            name="sample.exe",
            exe=r"C:\Apps\sample.exe",
            username="tester",
            create_time=123.0,
        )

    def test_recycles_when_no_locks_remain_after_termination(self) -> None:
        expected = Path(r"C:\Temp\target.txt")
        with (
            patch(
                "app.file_actions.preflight_recycle_target",
                return_value=expected,
            ),
            patch("app.file_actions.terminate_process") as terminate_mock,
            patch("app.file_actions.find_targets_locks") as scan_mock,
            patch("app.file_actions.recycle_file", return_value=expected) as recycle_mock,
        ):
            scan_mock.return_value = BatchScanResult(
                targets=(),
                locks=(),
                scanned_files=1,
                inaccessible_processes=2,
            )
            result = terminate_and_recycle(self.target, str(expected))

        self.assertEqual(result, expected)
        terminate_mock.assert_called_once_with(self.target)
        scan_mock.assert_called_once_with([str(expected)], recursive=True)
        recycle_mock.assert_called_once_with(str(expected))

    def test_rejects_recycle_when_rescan_finds_other_process(self) -> None:
        other = ProcessInfo(
            pid=4302,
            name="writer.exe",
            exe=r"C:\Apps\writer.exe",
            username="tester",
            create_time=200.0,
        )
        file_path = r"C:\Temp\target.txt"
        with (
            patch(
                "app.file_actions.preflight_recycle_target",
                return_value=Path(file_path),
            ),
            patch("app.file_actions.terminate_process") as terminate_mock,
            patch("app.file_actions.find_targets_locks") as scan_mock,
            patch("app.file_actions.recycle_file") as recycle_mock,
        ):
            scan_mock.return_value = BatchScanResult(
                targets=(),
                locks=(
                    FileLockInfo(
                        file_path,
                        other,
                        lock_state=LockState.BLOCKING,
                    ),
                ),
                scanned_files=1,
            )
            with self.assertRaises(FileActionError) as raised:
                terminate_and_recycle(self.target, file_path)

        terminate_mock.assert_called_once_with(self.target)
        recycle_mock.assert_not_called()
        self.assertIn("writer.exe", str(raised.exception))
        self.assertIn("4302", str(raised.exception))

    def test_recycles_when_remaining_handles_are_non_blocking(self) -> None:
        reader = ProcessInfo(
            pid=4302,
            name="reader.exe",
            exe=r"C:\Apps\reader.exe",
            username="tester",
            create_time=200.0,
        )
        expected = Path(r"C:\Temp\target.txt")
        with (
            patch(
                "app.file_actions.preflight_recycle_target",
                return_value=expected,
            ),
            patch("app.file_actions.terminate_process"),
            patch("app.file_actions.find_targets_locks") as scan_mock,
            patch("app.file_actions.recycle_file", return_value=expected) as recycle_mock,
        ):
            scan_mock.return_value = BatchScanResult(
                targets=(),
                locks=(
                    FileLockInfo(
                        str(expected),
                        reader,
                        lock_state=LockState.NON_BLOCKING,
                    ),
                ),
                scanned_files=1,
            )
            result = terminate_and_recycle(self.target, str(expected))

        self.assertEqual(result, expected)
        recycle_mock.assert_called_once_with(str(expected))

    def test_propagates_process_termination_error(self) -> None:
        file_path = r"C:\Temp\target.txt"
        with (
            patch(
                "app.file_actions.preflight_recycle_target",
                return_value=Path(file_path),
            ),
            patch(
                "app.file_actions.terminate_process",
                side_effect=ProcessActionError("permission denied"),
            ),
            patch("app.file_actions.find_targets_locks") as scan_mock,
            patch("app.file_actions.recycle_file") as recycle_mock,
        ):
            with self.assertRaisesRegex(ProcessActionError, "permission denied"):
                terminate_and_recycle(self.target, file_path)

        scan_mock.assert_not_called()
        recycle_mock.assert_not_called()

    def test_wraps_rescan_error_after_termination(self) -> None:
        file_path = r"C:\Temp\target.txt"
        with (
            patch(
                "app.file_actions.preflight_recycle_target",
                return_value=Path(file_path),
            ),
            patch("app.file_actions.terminate_process"),
            patch(
                "app.file_actions.find_targets_locks",
                side_effect=OSError("scan failed"),
            ),
            patch("app.file_actions.recycle_file") as recycle_mock,
        ):
            with self.assertRaisesRegex(FileActionError, "scan failed"):
                terminate_and_recycle(self.target, file_path)

        recycle_mock.assert_not_called()

    def test_rejects_other_processes_before_termination(self) -> None:
        other = ProcessInfo(
            pid=4302,
            name="writer.exe",
            exe=r"C:\Apps\writer.exe",
            username="tester",
            create_time=200.0,
        )
        with (
            patch("app.file_actions.preflight_recycle_target") as preflight_mock,
            patch("app.file_actions.terminate_process") as terminate_mock,
            patch("app.file_actions.find_targets_locks") as scan_mock,
            patch("app.file_actions.recycle_file") as recycle_mock,
        ):
            with self.assertRaisesRegex(FileActionError, "writer.exe"):
                terminate_and_recycle(
                    self.target,
                    r"C:\Temp\target.txt",
                    (other,),
                )

        preflight_mock.assert_not_called()
        terminate_mock.assert_not_called()
        scan_mock.assert_not_called()
        recycle_mock.assert_not_called()

    def test_rejects_invalid_recycle_target_before_termination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.txt"
            with (
                patch("app.file_actions.terminate_process") as terminate_mock,
                patch("app.file_actions.find_targets_locks") as scan_mock,
                patch("app.file_actions.recycle_file") as recycle_mock,
            ):
                with self.assertRaisesRegex(FileActionError, "不存在或无法访问"):
                    terminate_and_recycle(self.target, str(missing))

        terminate_mock.assert_not_called()
        scan_mock.assert_not_called()
        recycle_mock.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows Recycle Bin integration")
    def test_terminates_locking_process_and_moves_file_to_recycle_bin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "locked.txt"
            file_path.write_text("locked", encoding="utf-8")
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys,time; "
                        "handle=open(sys.argv[1], 'r'); "
                        "print('ready', flush=True); "
                        "time.sleep(30)"
                    ),
                    str(file_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                ready = child.stdout.readline().strip() if child.stdout else ""
                self.assertEqual(ready, "ready")

                process = psutil.Process(child.pid)
                info = ProcessInfo(
                    pid=process.pid,
                    name=process.name(),
                    exe=process.exe(),
                    username=process.username(),
                    create_time=process.create_time(),
                )

                terminate_and_recycle(info, str(file_path))

                self.assertFalse(file_path.exists())
                self.assertIsNotNone(child.wait(timeout=3))
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=3)
                if child.stdout is not None:
                    child.stdout.close()
                if child.stderr is not None:
                    child.stderr.close()


if __name__ == "__main__":
    unittest.main()