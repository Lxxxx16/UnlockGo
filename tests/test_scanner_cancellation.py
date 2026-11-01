from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.scanner import ScanCancelledError, find_folder_locks


class ScannerCancellationTests(unittest.TestCase):
    def test_folder_scan_stops_when_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("test", encoding="utf-8")
            calls = 0

            def should_cancel() -> bool:
                nonlocal calls
                calls += 1
                return calls >= 2

            with self.assertRaises(ScanCancelledError):
                find_folder_locks(
                    temp_dir,
                    recursive=True,
                    should_cancel=should_cancel,
                )
            self.assertGreaterEqual(calls, 2)

    def test_folder_scan_reports_inaccessible_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "app.scanner.os.scandir",
                side_effect=PermissionError("access denied"),
            ):
                result = find_folder_locks(temp_dir, recursive=True)

        self.assertEqual(result.scanned_files, 0)
        self.assertEqual(result.inaccessible_paths, 1)
        self.assertEqual(result.inaccessible_processes, 0)

    def test_folder_scan_reports_error_while_iterating_entries(self) -> None:
        class BrokenScandir:
            def __enter__(self) -> "BrokenScandir":
                return self

            def __exit__(self, *args: object) -> bool:
                return False

            def __iter__(self):
                raise PermissionError("access denied during iteration")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "app.scanner.os.scandir",
                return_value=BrokenScandir(),
            ):
                result = find_folder_locks(temp_dir, recursive=True)

        self.assertEqual(result.scanned_files, 0)
        self.assertEqual(result.inaccessible_paths, 1)
        self.assertEqual(result.inaccessible_processes, 0)


if __name__ == "__main__":
    unittest.main()