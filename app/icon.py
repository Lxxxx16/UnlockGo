"""Application icon resource helpers."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon


def _assets_directory() -> Path:
    if getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root) / "app" / "assets"
    return Path(__file__).resolve().parent / "assets"


ASSETS_DIR = _assets_directory()
APP_ICON_PATH = ASSETS_DIR / "unlockgo.png"
APP_ICO_PATH = ASSETS_DIR / "unlockgo.ico"


def app_icon() -> QIcon:
    """Return the shared application icon."""

    return QIcon(str(APP_ICON_PATH))
