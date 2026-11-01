from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QApplication

from app import APP_DISPLAY_NAME, APP_NAME, ORGANIZATION_NAME, i18n
from app.icon import app_icon
from app.main_window import MainWindow


def _split_launch_arguments(argv: list[str]) -> tuple[list[str], tuple[str, ...]]:
    """Remove custom target arguments before Qt parses the command line."""

    qt_argv: list[str] = []
    targets: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--target":
            if index + 1 < len(argv):
                targets.append(argv[index + 1])
                index += 2
                continue
            index += 1
            continue
        if argument.startswith("--target="):
            targets.append(argument.partition("=")[2])
            index += 1
            continue
        qt_argv.append(argument)
        index += 1
    return qt_argv, tuple(targets)


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("UnlockGo.Desktop")
    except (AttributeError, OSError):
        pass


def run(argv: list[str] | None = None) -> int:
    command_line = list(sys.argv if argv is None else argv)
    qt_argv, initial_targets = _split_launch_arguments(command_line)

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    _set_windows_app_id()

    app = QApplication(qt_argv)
    app.setWindowIcon(app_icon())
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORGANIZATION_NAME)
    i18n.load_language()
    app.setApplicationDisplayName(i18n.tr(APP_DISPLAY_NAME))
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))

    window = MainWindow(
        initial_targets=initial_targets,
        language=i18n.get_language(),
    )
    window.show()
    return app.exec()
