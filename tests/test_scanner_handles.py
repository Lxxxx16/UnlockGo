from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.models import (
    CandidateSource,
    FileIdentity,
    HandleIdentity,
    LockState,
)
from app.scanner import ScanCancelledError, find_targets_locks
from app.windows_handles import (
    FileHandleInfo,
    FileHandleScanResult,
    HandleScanCancelledError,
)
from app.windows_lock_probe import LockProbeResult

FILE_IDENTITY = FileIdentity(
    volume_serial=0x12345678,
    file_index_high=0x11223344,
    file_index_low=0x55667788,
)


class _FakeProcess:
    def __init__(
        self,
        pid: int,
        *,
        open_paths: tuple[str, ...] = (),
        mapped_paths: tuple[str, ...] = (),
    ) -> None:
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": "sample.exe",
            "exe": r"C:\Apps\sample.exe",
            "username": "tester",
            "create_time": 123.0,
        }
        self._open_paths = open_paths
        self._mapped_paths = mapped_paths
        self.open_files_calls = 0

    def open_files(self) -> list[SimpleNamespace]:
        self.open_files_calls += 1
        return [SimpleNamespace(path=path) for path in self._open_paths]

    def memory_maps(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(path=path) for path in self._mapped_paths]


class ScannerHandleTests(unittest.TestCase):
    def test_successful_handle_scan_skips_full_psutil_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")
            process = _FakeProcess(7654)

            with (
                patch(
                    "app.scanner.psutil.process_iter",
                    return_value=[process],
                ) as process_iter_mock,
                patch("app.scanner.psutil.Process", return_value=process),
                patch(
                    "app.scanner.enumerate_file_handles",
                    return_value=FileHandleScanResult(
                        handles=(
                            FileHandleInfo(
                                pid=7654,
                                handle_value=0x120,
                                file_path=str(target),
                                file_identity=FILE_IDENTITY,
                            ),
                        )
                    ),
                ),
                patch(
                    "app.scanner.find_restart_manager_processes",
                    return_value=(),
                ),
            ):
                result = find_targets_locks((target,))

        self.assertEqual(len(result.locks), 1)
        self.assertEqual(result.locks[0].process.pid, 7654)
        self.assertEqual(result.locks[0].handles, (0x120,))
        self.assertEqual(
            result.locks[0].handle_identities,
            (
                HandleIdentity(
                    handle_value=0x120,
                    file_identity=FILE_IDENTITY,
                ),
            ),
        )
        self.assertEqual(
            result.locks[0].sources,
            (CandidateSource.SYSTEM_HANDLE,),
        )
        self.assertEqual(process.open_files_calls, 0)
        process_iter_mock.assert_not_called()

    def test_handle_and_restart_manager_sources_are_merged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")
            process = _FakeProcess(7654)

            with (
                patch("app.scanner.psutil.process_iter", return_value=[process]),
                patch("app.scanner.psutil.Process", return_value=process),
                patch(
                    "app.scanner.enumerate_file_handles",
                    return_value=FileHandleScanResult(
                        handles=(
                            FileHandleInfo(
                                pid=7654,
                                handle_value=0x120,
                                file_path=str(target),
                            ),
                        )
                    ),
                ),
                patch(
                    "app.scanner.find_restart_manager_processes",
                    return_value=(
                        SimpleNamespace(pid=7654, app_name="sample.exe"),
                    ),
                ),
                patch(
                    "app.scanner.probe_path_lock",
                    return_value=LockProbeResult(
                        state=LockState.NON_BLOCKING,
                        detail="目标可以获取 DELETE 权限。",
                    ),
                ),
            ):
                result = find_targets_locks((target,))

        self.assertEqual(len(result.locks), 1)
        self.assertEqual(
            result.locks[0].sources,
            (
                CandidateSource.SYSTEM_HANDLE,
                CandidateSource.RESTART_MANAGER,
            ),
        )

    def test_multiple_handles_for_same_pid_share_process_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")
            process = _FakeProcess(7654, mapped_paths=(str(target),))

            with (
                patch("app.scanner.psutil.process_iter", return_value=[process]),
                patch(
                    "app.scanner.psutil.Process",
                    return_value=process,
                ) as process_mock,
                patch(
                    "app.scanner.enumerate_file_handles",
                    return_value=FileHandleScanResult(
                        handles=(
                            FileHandleInfo(
                                pid=7654,
                                handle_value=0x120,
                                file_path=str(target),
                                file_identity=FILE_IDENTITY,
                            ),
                            FileHandleInfo(
                                pid=7654,
                                handle_value=0x8A4,
                                file_path=str(target),
                                file_identity=FILE_IDENTITY,
                            ),
                        )
                    ),
                ),
                patch(
                    "app.scanner.find_restart_manager_processes",
                    return_value=(),
                ),
            ):
                result = find_targets_locks((target,))

        self.assertEqual(len(result.locks), 1)
        self.assertEqual(result.locks[0].process.pid, 7654)
        self.assertEqual(result.locks[0].handles, (0x120, 0x8A4))
        self.assertEqual(
            {item.handle_value for item in result.locks[0].handle_identities},
            {0x120, 0x8A4},
        )
        process_mock.assert_called_once_with(7654)

    def test_failed_handle_scan_falls_back_to_open_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")
            process = _FakeProcess(7654, open_paths=(str(target),))

            with (
                patch(
                    "app.scanner.psutil.process_iter",
                    return_value=[process],
                ) as process_iter_mock,
                patch("app.scanner.psutil.Process", return_value=process),
                patch(
                    "app.scanner.enumerate_file_handles",
                    return_value=FileHandleScanResult(
                        handles=(),
                        scan_succeeded=False,
                    ),
                ),
                patch(
                    "app.scanner.find_restart_manager_processes",
                    return_value=(),
                ),
            ):
                result = find_targets_locks((target,))

        self.assertEqual(len(result.locks), 1)
        self.assertEqual(result.locks[0].process.pid, 7654)
        self.assertEqual(
            result.locks[0].sources,
            (CandidateSource.PROCESS_RESOURCES,),
        )
        self.assertEqual(process.open_files_calls, 1)
        process_iter_mock.assert_called_once()

    def test_delete_probe_classifies_candidates_once_per_target_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")

            def make_process(pid: int) -> _FakeProcess:
                return _FakeProcess(pid)

            handles = FileHandleScanResult(
                handles=(
                    FileHandleInfo(
                        pid=7654,
                        handle_value=0x120,
                        file_path=str(target),
                    ),
                    FileHandleInfo(
                        pid=7655,
                        handle_value=0x8A4,
                        file_path=str(target),
                    ),
                )
            )
            probe = LockProbeResult(
                state=LockState.BLOCKING,
                detail="目标被独占句柄阻止删除。",
                error_code=32,
            )

            with (
                patch("app.scanner.psutil.process_iter") as process_iter_mock,
                patch("app.scanner.psutil.Process", side_effect=make_process),
                patch(
                    "app.scanner.enumerate_file_handles",
                    return_value=handles,
                ),
                patch(
                    "app.scanner.find_restart_manager_processes",
                    return_value=(),
                ),
                patch(
                    "app.scanner.probe_path_lock",
                    return_value=probe,
                ) as probe_mock,
            ):
                result = find_targets_locks((target,))

        self.assertEqual(len(result.locks), 2)
        self.assertTrue(
            all(lock.lock_state == LockState.BLOCKING for lock in result.locks)
        )
        self.assertTrue(
            all(lock.lock_detail == probe.detail for lock in result.locks)
        )
        probe_mock.assert_called_once_with(str(target))
        process_iter_mock.assert_not_called()

    def test_folder_handle_matching_respects_recursive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "folder"
            nested = folder / "nested"
            nested.mkdir(parents=True)
            direct_file = folder / "direct.txt"
            nested_file = nested / "nested.txt"
            direct_file.write_text("direct", encoding="utf-8")
            nested_file.write_text("nested", encoding="utf-8")
            process = _FakeProcess(7654)
            handles = FileHandleScanResult(
                handles=(
                    FileHandleInfo(
                        pid=7654,
                        handle_value=0x120,
                        file_path=str(direct_file),
                    ),
                    FileHandleInfo(
                        pid=7654,
                        handle_value=0x8A4,
                        file_path=str(nested_file),
                    ),
                )
            )

            with (
                patch("app.scanner.psutil.process_iter", return_value=[process]),
                patch("app.scanner.psutil.Process", return_value=process),
                patch(
                    "app.scanner.enumerate_file_handles",
                    return_value=handles,
                ),
                patch(
                    "app.scanner.find_restart_manager_processes",
                    return_value=(),
                ),
            ):
                non_recursive = find_targets_locks((folder,), recursive=False)
                recursive = find_targets_locks((folder,), recursive=True)

        self.assertEqual(
            {lock.file_path for lock in non_recursive.locks},
            {str(direct_file)},
        )
        self.assertEqual(
            {lock.file_path for lock in recursive.locks},
            {str(direct_file), str(nested_file)},
        )

    def test_cancelled_handle_scan_propagates_scan_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")

            with (
                patch(
                    "app.scanner.enumerate_file_handles",
                    side_effect=HandleScanCancelledError(),
                ),
                self.assertRaises(ScanCancelledError),
            ):
                find_targets_locks((target,))


if __name__ == "__main__":
    unittest.main()
