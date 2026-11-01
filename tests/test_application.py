from __future__ import annotations

import unittest

from app.application import _split_launch_arguments
from app.icon import APP_ICO_PATH, APP_ICON_PATH


class LaunchArgumentTests(unittest.TestCase):
    def test_extracts_repeated_targets_and_preserves_qt_arguments(self) -> None:
        qt_argv, targets = _split_launch_arguments(
            [
                "main.py",
                "--target",
                r"D:\Data\first folder",
                "--style",
                "Fusion",
                r"--target=D:\Data\second.txt",
            ]
        )

        self.assertEqual(qt_argv, ["main.py", "--style", "Fusion"])
        self.assertEqual(
            targets,
            (r"D:\Data\first folder", r"D:\Data\second.txt"),
        )

    def test_ignores_target_without_value(self) -> None:
        qt_argv, targets = _split_launch_arguments(["main.py", "--target"])
        self.assertEqual(qt_argv, ["main.py"])
        self.assertEqual(targets, ())

    def test_application_icon_assets_exist(self) -> None:
        self.assertTrue(APP_ICON_PATH.is_file())
        self.assertTrue(APP_ICO_PATH.is_file())


if __name__ == "__main__":
    unittest.main()
