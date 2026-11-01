from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from app import windows_lock_probe
from app.models import LockState
from app.windows_lock_probe import (
    DELETE,
    FILE_READ_ATTRIBUTES,
    FILE_SHARE_DELETE,
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    _extended_path,
    probe_path_lock,
)


class ExtendedPathTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows path semantics")
    def test_local_unc_and_already_extended_paths(self) -> None:
        self.assertEqual(
            _extended_path(r"C:\Temp\sample.txt"),
            r"\\?\C:\Temp\sample.txt",
        )
        self.assertEqual(
            _extended_path(r"\\server\share\sample.txt"),
            r"\\?\UNC\server\share\sample.txt",
        )
        self.assertEqual(
            _extended_path(r"\\?\C:\Temp\sample.txt"),
            r"\\?\C:\Temp\sample.txt",
        )
        self.assertEqual(
            _extended_path(r"\\.\pipe\sample"),
            r"\\.\pipe\sample",
        )


@unittest.skipUnless(sys.platform == "win32", "Windows CreateFileW probe")
class WindowsLockProbeTests(unittest.TestCase):
    def _probe_with_result(
        self,
        handle: int,
        *,
        error_code: int = 0,
    ):
        api = MagicMock()
        api.CreateFileW.return_value = handle
        api.CloseHandle.return_value = 1
        with (
            patch.object(windows_lock_probe, "_kernel32", return_value=api),
            patch.object(
                windows_lock_probe.ctypes,
                "get_last_error",
                return_value=error_code,
            ),
        ):
            result = probe_path_lock(r"C:\Temp\sample.txt")
        return result, api

    def test_successful_delete_probe_is_non_blocking_and_closes_handle(self) -> None:
        result, api = self._probe_with_result(123)

        self.assertEqual(result.state, LockState.NON_BLOCKING)
        api.CreateFileW.assert_called_once()
        create_args = api.CreateFileW.call_args.args
        self.assertEqual(create_args[1], DELETE | FILE_READ_ATTRIBUTES)
        self.assertEqual(
            create_args[2],
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        )
        api.CloseHandle.assert_called_once_with(123)

    def test_sharing_lock_and_mapped_file_errors_are_blocking(self) -> None:
        for error_code in (32, 33, 1224):
            with self.subTest(error_code=error_code):
                result, api = self._probe_with_result(
                    windows_lock_probe.INVALID_HANDLE_VALUE,
                    error_code=error_code,
                )

                self.assertEqual(result.state, LockState.BLOCKING)
                self.assertEqual(result.error_code, error_code)
                api.CloseHandle.assert_not_called()

    def test_permission_and_missing_path_errors_are_unknown(self) -> None:
        for error_code in (5, 2, 3):
            with self.subTest(error_code=error_code):
                result, api = self._probe_with_result(
                    windows_lock_probe.INVALID_HANDLE_VALUE,
                    error_code=error_code,
                )

                self.assertEqual(result.state, LockState.UNKNOWN)
                self.assertEqual(result.error_code, error_code)
                api.CloseHandle.assert_not_called()

    def test_other_windows_errors_are_unknown(self) -> None:
        result, _ = self._probe_with_result(
            windows_lock_probe.INVALID_HANDLE_VALUE,
            error_code=87,
        )

        self.assertEqual(result.state, LockState.UNKNOWN)
        self.assertEqual(result.error_code, 87)
        self.assertIn("87", result.detail)


if __name__ == "__main__":
    unittest.main()
