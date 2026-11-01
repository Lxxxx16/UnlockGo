from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.file_operations import (
    FileOperationError,
    copy_target,
    linked_path_component,
    move_target,
    preflight_file_operation,
    rename_target,
)


class FileOperationTests(unittest.TestCase):
    def test_rename_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "old.txt"
            source.write_text("data", encoding="utf-8")
            result = rename_target(source, "new.txt")
            self.assertEqual(result.resolve(), source.with_name("new.txt").resolve())
            self.assertTrue(result.exists())
            self.assertFalse(source.exists())

    def test_move_target_to_existing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.txt"
            destination = root / "destination"
            source.write_text("data", encoding="utf-8")
            destination.mkdir()
            result = move_target(source, destination)
            self.assertEqual(result.resolve(), (destination / "source.txt").resolve())
            self.assertTrue(result.exists())
            self.assertFalse(source.exists())

    def test_copy_folder_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "child.txt").write_text("data", encoding="utf-8")
            result = copy_target(source, destination)
            self.assertEqual(result.resolve(), (destination / "source").resolve())
            self.assertEqual(
                (result / "child.txt").read_text(encoding="utf-8"),
                "data",
            )

    def test_rejects_moving_folder_into_itself(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            child = source / "child"
            child.mkdir(parents=True)
            with self.assertRaises(FileOperationError):
                move_target(source, child)

    def test_preflight_rejects_invalid_rename_name_without_changing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.txt"
            source.write_text("data", encoding="utf-8")

            with self.assertRaisesRegex(FileOperationError, "Windows"):
                preflight_file_operation(
                    source,
                    "rename",
                    new_name="bad:name",
                )

            self.assertTrue(source.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "data")

    def test_preflight_rejects_control_character_in_rename_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.txt"
            source.write_text("data", encoding="utf-8")

            with self.assertRaisesRegex(
                FileOperationError,
                "控制字符",
            ):
                preflight_file_operation(
                    source,
                    "rename",
                    new_name="bad\x01name",
                )

            self.assertTrue(source.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "data")

    def test_preflight_rejects_missing_move_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.txt"
            source.write_text("data", encoding="utf-8")

            with self.assertRaisesRegex(FileOperationError, "必须是一个已存在的文件夹"):
                preflight_file_operation(
                    source,
                    "move",
                    destination_directory=root / "missing",
                )

    def test_copy_folder_rejects_internal_link_without_creating_destination(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            destination = root / "destination"
            nested = source / "nested"
            nested.mkdir(parents=True)
            destination.mkdir()
            linked = nested / "linked.txt"
            linked.write_text("outside", encoding="utf-8")

            with patch(
                "app.file_operations._is_link_like",
                side_effect=lambda path: Path(path) == linked.resolve(),
            ):
                with self.assertRaisesRegex(
                    FileOperationError,
                    "符号链接",
                ):
                    copy_target(source, destination)

            self.assertFalse((destination / "source").exists())

    def test_linked_path_component_is_detected_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            source = root / "nested" / "source.txt"
            source.parent.mkdir()
            source.write_text("data", encoding="utf-8")

            with patch(
                "app.file_operations._is_link_like",
                side_effect=lambda path: path == root,
            ):
                self.assertEqual(linked_path_component(source), root)
                with self.assertRaisesRegex(FileOperationError, "符号链接或目录联接"):
                    preflight_file_operation(
                        source,
                        "rename",
                        new_name="renamed.txt",
                    )


if __name__ == "__main__":
    unittest.main()