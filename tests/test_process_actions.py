from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import psutil

from app.models import ProcessInfo
from app.process_actions import (
    ProcessActionError,
    ProcessChangedError,
    ProtectedProcessError,
    process_risk,
    terminate_process,
    terminate_processes,
)


class ProcessActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.process = psutil.Process(self.child.pid)
        self.info = ProcessInfo(
            pid=self.process.pid,
            name=self.process.name(),
            exe=self.process.exe(),
            username="",
            create_time=self.process.create_time(),
        )

    def tearDown(self) -> None:
        if self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=3)

    def test_process_risk_levels(self) -> None:
        self.assertEqual(
            process_risk(ProcessInfo(os.getpid(), "python.exe", "", "")),
            "self",
        )
        self.assertEqual(process_risk(ProcessInfo(1, "System", "", "")), "critical")
        self.assertEqual(
            process_risk(ProcessInfo(2, "explorer.exe", "", "")),
            "high",
        )
        self.assertEqual(
            process_risk(ProcessInfo(3, "notepad.exe", "", "")),
            "normal",
        )

    def test_rejects_unlockgo_itself(self) -> None:
        info = ProcessInfo(os.getpid(), "python.exe", "", "")
        with self.assertRaises(ProtectedProcessError):
            terminate_process(info)

    def test_rejects_reused_pid_identity(self) -> None:
        stale = ProcessInfo(
            pid=self.info.pid,
            name=self.info.name,
            exe=self.info.exe,
            username=self.info.username,
            create_time=(self.info.create_time or 0.0) + 10.0,
        )
        with self.assertRaises(ProcessChangedError):
            terminate_process(stale)
        self.assertIsNone(self.child.poll())

    def test_requires_start_time_verification(self) -> None:
        unverified = ProcessInfo(
            pid=self.info.pid,
            name=self.info.name,
            exe=self.info.exe,
            username=self.info.username,
            create_time=None,
        )
        with self.assertRaises(ProcessChangedError):
            terminate_process(unverified)
        self.assertIsNone(self.child.poll())

    def test_batch_refuses_every_target_when_one_is_critical(self) -> None:
        critical = ProcessInfo(1, "System", "", "", 100.0)
        with self.assertRaises(ProtectedProcessError):
            terminate_processes((self.info, critical))
        self.assertIsNone(self.child.poll())

    def test_batch_terminates_deduplicated_processes(self) -> None:
        result = terminate_processes((self.info, self.info))
        self.assertEqual(result.terminated, (self.info,))
        self.assertEqual(result.failures, ())
        self.assertIsNotNone(self.child.wait(timeout=3))

    def test_terminates_only_the_verified_process(self) -> None:
        terminate_process(self.info)
        self.assertIsNotNone(self.child.wait(timeout=3))

    def test_treats_an_already_exited_target_as_finished(self) -> None:
        self.child.terminate()
        self.assertIsNotNone(self.child.wait(timeout=3))

        terminate_process(self.info)

    def test_does_not_depend_on_psutil_wait_after_termination(self) -> None:
        denied = psutil.AccessDenied(self.info.pid)
        with patch.object(psutil.Process, "wait", side_effect=denied):
            terminate_process(self.info)

        self.assertIsNotNone(self.child.wait(timeout=3))

    def test_access_denied_after_exit_is_not_reported_as_failure(self) -> None:
        def terminate_then_report_denied(process_handle: int) -> bool:
            del process_handle
            self.child.kill()
            self.assertIsNotNone(self.child.wait(timeout=3))
            ctypes.set_last_error(5)
            return False

        with patch(
            "app.process_actions._terminate_process_handle",
            side_effect=terminate_then_report_denied,
        ):
            terminate_process(self.info)

    def test_access_denied_while_still_running_is_a_real_failure(self) -> None:
        def deny_access(process_handle: int) -> bool:
            del process_handle
            ctypes.set_last_error(5)
            return False

        with patch(
            "app.process_actions._terminate_process_handle",
            side_effect=deny_access,
        ):
            with self.assertRaisesRegex(ProcessActionError, "没有权限结束"):
                terminate_process(self.info)

        self.assertIsNone(self.child.poll())


if __name__ == "__main__":
    unittest.main()
