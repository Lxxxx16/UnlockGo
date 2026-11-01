from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import psutil

from app.models import FileIdentity, LockState, ProcessInfo
from app.process_actions import ProcessChangedError
from app.windows_handle_release import (
    HandleReleaseProtectedError,
    HandleSnapshotStaleError,
    release_file_handle,
)
from app.windows_handles import enumerate_file_handles, normalize_handle_path
from app.windows_lock_probe import probe_path_lock


@unittest.skipUnless(sys.platform == "win32", "Windows handle release tests")
class WindowsHandleReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target = Path(self.temp_dir.name) / "locked-target.txt"
        self.target.write_text("locked", encoding="utf-8")
        self.child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import sys, time; "
                    "stream = open(sys.argv[1], 'rb'); "
                    "print('ready', flush=True); "
                    "time.sleep(60)"
                ),
                str(self.target),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        ready = self.child.stdout.readline() if self.child.stdout else ""
        if ready.strip() != "ready":
            self.fail("Test helper process did not open the target file.")

        process = psutil.Process(self.child.pid)
        self.process = ProcessInfo(
            pid=process.pid,
            name=process.name(),
            exe=process.exe(),
            username="",
            create_time=process.create_time(),
        )

        result = enumerate_file_handles(pid_filter={self.child.pid})
        expected = normalize_handle_path(self.target)
        matches = [
            item
            for item in result.handles
            if normalize_handle_path(item.file_path) == expected
        ]
        if not matches:
            self.fail("Could not find the helper process file handle.")
        self.handle = matches[0]
        if self.handle.file_identity is None:
            self.fail("Could not read the helper process file identity.")
        self.file_identity = self.handle.file_identity

    def tearDown(self) -> None:
        if self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=3)
        if self.child.stdout is not None:
            self.child.stdout.close()
        self.temp_dir.cleanup()

    def test_releases_verified_handle_without_terminating_process(self) -> None:
        self.assertEqual(probe_path_lock(self.target).state, LockState.BLOCKING)

        result = release_file_handle(
            self.process,
            self.handle.handle_value,
            str(self.target),
            expected_identity=self.file_identity,
        )

        self.assertTrue(result.unlocked)
        self.assertIsNone(self.child.poll())
        renamed = self.target.with_name("renamed-target.txt")
        self.target.rename(renamed)
        self.assertEqual(renamed.read_text(encoding="utf-8"), "locked")

    def test_rejects_unknown_handle_without_touching_file(self) -> None:
        with self.assertRaises(HandleSnapshotStaleError):
            release_file_handle(
                self.process,
                self.handle.handle_value ^ 0x10,
                str(self.target),
                expected_identity=self.file_identity,
            )

        self.assertIsNone(self.child.poll())
        self.assertEqual(probe_path_lock(self.target).state, LockState.BLOCKING)

    def test_rejects_process_identity_change(self) -> None:
        stale = ProcessInfo(
            pid=self.process.pid,
            name=self.process.name,
            exe=self.process.exe,
            username=self.process.username,
            create_time=(self.process.create_time or 0.0) + 10.0,
        )

        with self.assertRaises(ProcessChangedError):
            release_file_handle(
                stale,
                self.handle.handle_value,
                str(self.target),
                expected_identity=self.file_identity,
            )

        self.assertIsNone(self.child.poll())
        self.assertEqual(probe_path_lock(self.target).state, LockState.BLOCKING)

    def test_rejects_unlockgo_itself(self) -> None:
        process = psutil.Process()
        own_process = ProcessInfo(
            pid=process.pid,
            name=process.name(),
            exe=process.exe(),
            username="",
            create_time=process.create_time(),
        )

        with self.assertRaises(HandleReleaseProtectedError):
            release_file_handle(
                own_process,
                4,
                str(self.target),
                expected_identity=self.file_identity,
            )

    def test_high_risk_process_requires_explicit_opt_in(self) -> None:
        high_risk = ProcessInfo(
            pid=self.process.pid,
            name="explorer.exe",
            exe=self.process.exe,
            username=self.process.username,
            create_time=self.process.create_time,
        )

        with self.assertRaises(HandleReleaseProtectedError):
            release_file_handle(
                high_risk,
                self.handle.handle_value,
                str(self.target),
                expected_identity=self.file_identity,
            )

    def test_rejects_changed_file_identity_without_touching_handle(self) -> None:
        changed_identity = FileIdentity(
            volume_serial=self.file_identity.volume_serial,
            file_index_high=self.file_identity.file_index_high,
            file_index_low=self.file_identity.file_index_low ^ 1,
        )

        with self.assertRaises(HandleSnapshotStaleError):
            release_file_handle(
                self.process,
                self.handle.handle_value,
                str(self.target),
                expected_identity=changed_identity,
            )

        self.assertIsNone(self.child.poll())
        self.assertEqual(probe_path_lock(self.target).state, LockState.BLOCKING)


if __name__ == "__main__":
    unittest.main()
