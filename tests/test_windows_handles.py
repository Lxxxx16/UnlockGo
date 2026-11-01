from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import FileIdentity
from app.windows_handles import (
    FileHandleInfo,
    HandleScanCancelledError,
    _SystemHandleEntry,
    enumerate_file_handles,
    file_identity_from_path,
    normalize_handle_path,
)

FILE_IDENTITY = FileIdentity(
    volume_serial=0x12345678,
    file_index_high=0x11223344,
    file_index_low=0x55667788,
)


class _FakeKernel32:
    def __init__(self) -> None:
        self.closed_handles: list[int] = []
        self.duplicated_handles: list[int] = []

    @staticmethod
    def GetCurrentProcess() -> int:
        return 100

    @staticmethod
    def OpenProcess(access: int, inherit: bool, pid: int) -> int:
        return 200 + pid

    def DuplicateHandle(
        self,
        source_process: int,
        source_handle: int,
        target_process: int,
        target_handle: object,
        access: int,
        inherit: bool,
        options: int,
    ) -> bool:
        del source_process, target_process, access, inherit, options
        duplicated = 0x500 + len(self.duplicated_handles)
        self.duplicated_handles.append(duplicated)
        ctypes.cast(
            target_handle,
            ctypes.POINTER(ctypes.c_size_t),
        ).contents.value = duplicated
        return True

    def CloseHandle(self, handle: object) -> bool:
        value = getattr(handle, "value", handle)
        self.closed_handles.append(int(value))
        return True


class WindowsHandleTests(unittest.TestCase):
    def test_normalize_handle_path_removes_extended_prefix(self) -> None:
        normalized = normalize_handle_path(r"\\?\C:\Temp\Target.TXT")

        self.assertNotIn("\\\\?\\", normalized)
        self.assertTrue(normalized.endswith(os.path.normcase(r"\temp\target.txt")))

    @unittest.skipUnless(
        sys.platform == "win32",
        "Windows File handle enumeration is Windows-only.",
    )
    def test_enumerates_open_file_handle_for_current_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "open-target.txt"
            target.write_text("target", encoding="utf-8")
            expected = normalize_handle_path(target)

            with target.open("rb") as stream:
                result = enumerate_file_handles(pid_filter={os.getpid()})

            self.assertTrue(result.scan_succeeded)
            self.assertIn(
                (os.getpid(), expected),
                {
                    (item.pid, normalize_handle_path(item.file_path))
                    for item in result.handles
                },
            )
            self.assertTrue(stream.closed)

    def test_closes_probe_source_and_duplicated_handles(self) -> None:
        api = _FakeKernel32()
        entries = (
            _SystemHandleEntry(
                pid=7654,
                handle_value=0x120,
                object_type_index=9,
                granted_access=0,
            ),
        )

        with (
            patch("app.windows_handles._kernel32", return_value=api),
            patch("app.windows_handles._open_probe_file_handle", return_value=42),
            patch("app.windows_handles._query_system_handles", return_value=entries),
            patch("app.windows_handles._find_file_type_index", return_value=9),
            patch(
                "app.windows_handles._path_from_handle",
                return_value=r"C:\Temp\target.txt",
            ),
            patch(
                "app.windows_handles.file_identity_from_handle",
                return_value=FILE_IDENTITY,
            ),
        ):
            result = enumerate_file_handles()

        self.assertEqual(
            result.handles,
            (
                FileHandleInfo(
                    pid=7654,
                    handle_value=0x120,
                    file_path=r"C:\Temp\target.txt",
                    granted_access=0,
                    file_identity=FILE_IDENTITY,
                ),
            ),
        )
        self.assertEqual(api.duplicated_handles, [0x500])
        self.assertCountEqual(api.closed_handles, [42, 200 + 7654, 0x500])

    def test_skips_file_identity_for_unrelated_paths(self) -> None:
        api = _FakeKernel32()
        entries = (
            _SystemHandleEntry(
                pid=7654,
                handle_value=0x120,
                object_type_index=9,
                granted_access=0,
            ),
        )

        with (
            patch("app.windows_handles._kernel32", return_value=api),
            patch("app.windows_handles._open_probe_file_handle", return_value=42),
            patch("app.windows_handles._query_system_handles", return_value=entries),
            patch("app.windows_handles._find_file_type_index", return_value=9),
            patch(
                "app.windows_handles._path_from_handle",
                return_value=r"C:\Temp\unrelated.txt",
            ),
            patch("app.windows_handles.file_identity_from_handle") as identity_mock,
        ):
            result = enumerate_file_handles(
                identity_filter=lambda path: path.endswith("target.txt")
            )

        identity_mock.assert_not_called()
        self.assertIsNone(result.handles[0].file_identity)

    def test_cancelled_scan_closes_duplicated_and_source_handles(self) -> None:
        api = _FakeKernel32()
        entries = tuple(
            _SystemHandleEntry(
                pid=7654,
                handle_value=handle_value,
                object_type_index=9,
                granted_access=0,
            )
            for handle_value in (0x120, 0x8A4)
        )

        with (
            patch("app.windows_handles._kernel32", return_value=api),
            patch("app.windows_handles._open_probe_file_handle", return_value=42),
            patch("app.windows_handles._query_system_handles", return_value=entries),
            patch("app.windows_handles._find_file_type_index", return_value=9),
            patch(
                "app.windows_handles._path_from_handle",
                side_effect=[
                    r"C:\Temp\target.txt",
                    HandleScanCancelledError(),
                ],
            ),
            patch(
                "app.windows_handles.file_identity_from_handle",
                return_value=FILE_IDENTITY,
            ),
            self.assertRaises(HandleScanCancelledError),
        ):
            enumerate_file_handles()

        self.assertEqual(api.duplicated_handles, [0x500, 0x501])
        self.assertCountEqual(
            api.closed_handles,
            [42, 200 + 7654, 0x500, 0x501],
        )

    @unittest.skipUnless(
        sys.platform == "win32",
        "Windows File identity tests are Windows-only.",
    )
    def test_reads_file_identity_for_file_and_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "identity-target.txt"
            target.write_text("target", encoding="utf-8")

            file_identity = file_identity_from_path(target)
            folder_identity = file_identity_from_path(temp_dir)

        self.assertIsNotNone(file_identity)
        self.assertIsNotNone(folder_identity)
        self.assertNotEqual(file_identity, folder_identity)


if __name__ == "__main__":
    unittest.main()
