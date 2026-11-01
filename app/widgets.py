from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from app import i18n
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class DropZone(QFrame):
    targetDropped = Signal(list)
    browseRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = "file"
        self._admin_mode = False
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("dragging", False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 18, 28, 18)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon = QLabel("＋")
        self._icon.setObjectName("dropIcon")
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._title = QLabel(i18n.tr("拖入文件或文件夹添加到列表"))
        self._title.setObjectName("dropTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._detail = QLabel(i18n.tr("可连续拖入多个目标，再点击“开始扫描”"))
        self._detail.setObjectName("dropDetail")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)
        self._detail.setMinimumWidth(600)

        self._choose_button = QPushButton(i18n.tr("添加文件"))
        self._choose_button.setObjectName("primaryButton")
        self._choose_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._choose_button.clicked.connect(self.browseRequested)

        layout.addWidget(self._icon)
        layout.addWidget(self._title)
        layout.addWidget(self._detail)
        layout.addSpacing(4)
        layout.addWidget(self._choose_button, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_mode(self, mode: str) -> None:
        if mode not in {"file", "folder"}:
            raise ValueError(f"Unsupported drop-zone mode: {mode}")
        self._mode = mode
        self.retranslate()

    def set_admin_mode(self, enabled: bool) -> None:
        self._admin_mode = enabled
        self.setAcceptDrops(not enabled)
        self._set_dragging(False)
        self._update_copy()

    def retranslate(self) -> None:
        self._choose_button.setText(
            i18n.tr("添加文件夹") if self._mode == "folder" else i18n.tr("添加文件")
        )
        self._update_copy()

    def _update_copy(self) -> None:
        if self._admin_mode:
            self._title.setText(i18n.tr("管理员模式下请使用按钮添加目标"))
            self._detail.setText(
                i18n.tr(
                    "Windows 阻止普通权限资源管理器向管理员窗口拖放；"
                    "可先普通模式添加目标后重启"
                )
            )
            return

        self._title.setText(i18n.tr("拖入文件或文件夹添加到列表"))
        if self._mode == "folder":
            self._detail.setText(
                i18n.tr("可连续拖入多个目标；也可以继续添加文件夹")
            )
        else:
            self._detail.setText(i18n.tr("可连续拖入多个目标；也可以继续添加文件"))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._has_valid_targets(event):
            event.acceptProposedAction()
            self._set_dragging(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._has_valid_targets(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._set_dragging(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_dragging(False)
        paths = [
            str(Path(url.toLocalFile()))
            for url in event.mimeData().urls()
            if url.isLocalFile() and self._is_valid_target(Path(url.toLocalFile()))
        ]
        if paths:
            self.targetDropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.browseRequested.emit()
        super().mouseReleaseEvent(event)

    def _has_valid_targets(
        self,
        event: QDragEnterEvent | QDragMoveEvent | QDropEvent,
    ) -> bool:
        urls = event.mimeData().urls()
        return bool(urls) and all(
            url.isLocalFile() and self._is_valid_target(Path(url.toLocalFile()))
            for url in urls
        )

    def _is_valid_target(self, path: Path) -> bool:
        return path.is_file() or path.is_dir()

    def _set_dragging(self, dragging: bool) -> None:
        if self.property("dragging") == dragging:
            return
        self.setProperty("dragging", dragging)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class ResultPlaceholder(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("resultPlaceholder")
        self._message_icon = "\u25cb"
        self._message_title = "\u5c1a\u672a\u9009\u62e9\u6587\u4ef6"
        self._message_detail = (
            "\u6dfb\u52a0\u76ee\u6807\u540e\u70b9\u51fb\u201c"
            "\u5f00\u59cb\u626b\u63cf\u201d\u68c0\u67e5\u5360"
            "\u7528\u8fdb\u7a0b\u3002"
        )
        self._message_busy = False
        self._message_tone = "neutral"
        self._message_title_values: dict[str, object] = {}
        self._message_detail_values: dict[str, object] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 36, 32, 36)
        layout.setSpacing(9)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon = QLabel("○")
        self._icon.setObjectName("placeholderIcon")
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._title = QLabel(i18n.tr("尚未选择文件"))
        self._title.setObjectName("placeholderTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._detail = QLabel(i18n.tr("添加目标后点击“开始扫描”检查占用进程。"))
        self._detail.setObjectName("placeholderDetail")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)
        self._detail.setMinimumWidth(420)
        self._detail.setMaximumWidth(620)

        self._progress = QProgressBar()
        self._progress.setObjectName("scanProgress")
        self._progress.setRange(0, 0)
        self._progress.setFixedWidth(220)
        self._progress.setTextVisible(False)
        self._progress.hide()

        layout.addStretch()
        layout.addWidget(self._icon)
        layout.addWidget(self._title)
        layout.addWidget(self._detail)
        layout.addSpacing(8)
        layout.addWidget(self._progress, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

    def show_message(
        self,
        icon: str,
        title: str,
        detail: str,
        *,
        busy: bool = False,
        tone: str = "neutral",
        title_values: dict[str, object] | None = None,
        detail_values: dict[str, object] | None = None,
    ) -> None:
        self._message_icon = icon
        self._message_title = title
        self._message_detail = detail
        self._message_busy = busy
        self._message_tone = tone
        self._message_title_values = dict(title_values or {})
        self._message_detail_values = dict(detail_values or {})
        self.retranslate()

    def retranslate(self) -> None:
        spec = getattr(self, "_message_title", None)
        if spec is None:
            return
        self._icon.setText(self._message_icon)
        self._title.setText(i18n.tr(spec, **self._message_title_values))
        self._detail.setText(
            i18n.tr(self._message_detail, **self._message_detail_values)
        )
        self._progress.setVisible(self._message_busy)
        self.setProperty("tone", self._message_tone)
        self.style().unpolish(self)
        self.style().polish(self)


class TargetListDialog(QDialog):
    """Shows staged targets and actions for fixing a misplaced file operation."""

    openRequested = Signal(str)
    recycleRequested = Signal(str)
    undoMoveRequested = Signal(str)

    def __init__(
        self,
        targets: tuple[Path, ...] | list[Path],
        *,
        last_move: tuple[Path, Path] | None = None,
        highlight: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._targets = tuple(Path(path) for path in targets)
        self._last_move = last_move
        self.setObjectName("targetDialog")
        self.setWindowTitle(i18n.tr("目标列表"))
        self.resize(780, 460)
        self.setMinimumSize(640, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        title = QLabel(i18n.tr("目标列表"))
        title.setObjectName("dialogTitle")
        detail = QLabel(
            i18n.tr(
                "共 {count} 个文件或文件夹。移动后这里显示新位置；"
                "选错位置时可撤销移动或删除到回收站。",
                count=len(self._targets),
            )
        )
        detail.setObjectName("dialogHint")
        detail.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(detail)
        if self._last_move is not None:
            source, destination = self._last_move
            recent = QLabel(
                i18n.tr("最近移动：") + f"{source}  →  {destination}"
            )
            recent.setObjectName("dialogNotice")
            recent.setWordWrap(True)
            recent.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(recent)

        self.table = QTableWidget(len(self._targets), 3)
        self.table.setObjectName("targetTable")
        self.table.setHorizontalHeaderLabels(
            [i18n.tr("类型"), i18n.tr("名称"), i18n.tr("完整路径")]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, path in enumerate(self._targets):
            kind = (
                i18n.tr("文件夹")
                if path.is_dir()
                else i18n.tr("文件")
                if path.is_file()
                else i18n.tr("已不存在")
            )
            kind_item = QTableWidgetItem(kind)
            name_item = QTableWidgetItem(path.name or str(path))
            path_item = QTableWidgetItem(str(path))
            path_item.setData(Qt.ItemDataRole.UserRole, str(path))
            path_item.setToolTip(str(path))
            self.table.setItem(row, 0, kind_item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, path_item)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.open_button = QPushButton(i18n.tr("打开位置"))
        self.undo_button = QPushButton(i18n.tr("撤销移动"))
        self.recycle_button = QPushButton(i18n.tr("删除到回收站"))
        close_button = QPushButton(i18n.tr("关闭"))
        for button in (self.open_button, self.undo_button):
            button.setObjectName("secondaryButton")
            button.setMinimumWidth(88)
        self.recycle_button.setObjectName("recycleButton")
        self.recycle_button.setMinimumWidth(110)
        close_button.setObjectName("primaryButton")
        close_button.setMinimumWidth(76)
        actions.addWidget(self.open_button)
        actions.addWidget(self.undo_button)
        actions.addStretch()
        actions.addWidget(self.recycle_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        self.open_button.clicked.connect(self._emit_open)
        self.undo_button.clicked.connect(self._emit_undo)
        self.recycle_button.clicked.connect(self._emit_recycle)
        close_button.clicked.connect(self.accept)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self._emit_open())

        target_row = 0
        if highlight is not None:
            highlight_key = self._path_key(highlight)
            for row, path in enumerate(self._targets):
                if self._path_key(path) == highlight_key:
                    target_row = row
                    break
        if self._targets:
            self.table.selectRow(target_row)
        self._update_buttons()

    @staticmethod
    def _path_key(path: Path) -> str:
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            resolved = path.absolute()
        return os.path.normcase(os.path.normpath(str(resolved)))

    def _selected_path(self) -> Path | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 2)
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return Path(value) if isinstance(value, str) and value else None

    def _update_buttons(self) -> None:
        path = self._selected_path()
        can_operate = path is not None and path.exists()
        self.open_button.setEnabled(can_operate)
        self.recycle_button.setEnabled(can_operate)
        self.undo_button.setEnabled(
            can_operate
            and self._last_move is not None
            and self._path_key(path) == self._path_key(self._last_move[1])
        )

    def _emit_open(self) -> None:
        path = self._selected_path()
        if path is not None:
            self.openRequested.emit(str(path))

    def _emit_recycle(self) -> None:
        path = self._selected_path()
        if path is not None:
            self.accept()
            self.recycleRequested.emit(str(path))

    def _emit_undo(self) -> None:
        path = self._selected_path()
        if path is not None:
            self.accept()
            self.undoMoveRequested.emit(str(path))

class NumericItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return int(self.text()) < int(other.text())
        except ValueError:
            return super().__lt__(other)
