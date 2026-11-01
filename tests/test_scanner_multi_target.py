from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.scanner import find_targets_locks
from app.windows_handles import FileHandleScanResult


class _FakeProcess:
    def __init__(self, pid: int, paths: tuple[str, ...]) -> None:
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": "sample.exe",
            "exe": r"C:\Apps\sample.exe",
            "username": "tester",
            "create_time": 123.0,
        }
        self._paths = paths

    def open_files(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(path=path) for path in self._paths]

    def memory_maps(self) -> list[SimpleNamespace]:
        return []


class MultiTargetScannerTests(unittest.TestCase):
    def test_scans_file_and_recursive_folder_in_one_process_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            direct = root / "direct.txt"
            folder = root / "folder"
            nested = folder / "nested"
            nested.mkdir(parents=True)
            child_file = nested / "child.txt"
            direct.write_text("direct", encoding="utf-8")
            child_file.write_text("child", encoding="utf-8")
            process = _FakeProcess(7654, (str(direct), str(child_file)))

            with (
                patch("app.scanner.psutil.process_iter", return_value=[process]),
                patch(
                    "app.scanner.find_restart_manager_processes",
                    return_value=(),
                ),
                patch(
                    "app.scanner.enumerate_file_handles",
                    return_value=FileHandleScanResult(
                        handles=(),
                        scan_succeeded=False,
                    ),
                ),
            ):
                result = find_targets_locks((direct, folder), recursive=True)

        self.assertEqual(len(result.targets), 2)
        self.assertEqual(result.scanned_files, 2)
        self.assertEqual({lock.file_path for lock in result.locks}, {str(direct), str(child_file)})
        self.assertEqual({lock.process.pid for lock in result.locks}, {7654})

    def test_non_recursive_folder_ignores_nested_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = root / "folder"
            nested = folder / "nested"
            nested.mkdir(parents=True)
            child_file = nested / "child.txt"
            child_file.write_text("child", encoding="utf-8")
            process = _FakeProcess(7654, (str(child_file),))

            with (
                patch("app.scanner.psutil.process_iter", return_value=[process]),
                patch(
                    "app.scanner.find_restart_manager_processes",
                    return_value=(),
                ),
                patch(
                    "app.scanner.enumerate_file_handles",
                    return_value=FileHandleScanResult(
                        handles=(),
                        scan_succeeded=False,
                    ),
                ),
            ):
                result = find_targets_locks((folder,), recursive=False)

        self.assertEqual(result.scanned_files, 0)
        self.assertEqual(result.locks, ())



    def test_batch_result_counts_inaccessible_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "folder"
            folder.mkdir()

            with (
                patch(
                    "app.scanner.os.scandir",
                    side_effect=PermissionError("access denied"),
                ),
                patch(
                    "app.scanner.enumerate_file_handles",
                    return_value=FileHandleScanResult(
                        handles=(),
                        scan_succeeded=True,
                    ),
                ),
                patch(
                    "app.scanner.find_restart_manager_processes",
                    return_value=(),
                ),
            ):
                result = find_targets_locks((folder,), recursive=True)

        self.assertEqual(len(result.targets), 1)
        self.assertEqual(result.targets[0].inaccessible_paths, 1)
        self.assertEqual(result.inaccessible_paths, 1)


if __name__ == "__main__":
    unittest.main()
