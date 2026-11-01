from __future__ import annotations

import os
from datetime import datetime
from functools import partial
from pathlib import Path

from PySide6.QtCore import QSize, QThread, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import APP_DISPLAY_NAME, i18n
from app.file_operations import FileOperationError, linked_path_component
from app.icon import app_icon
from app.models import (
    BatchScanResult,
    CandidateSource,
    FileLockInfo,
    HandleIdentity,
    LockState,
    ProcessInfo,
)
from app.process_actions import BatchTerminationResult, process_risk
from app.platform_utils import is_running_as_admin, request_admin_relaunch
from app.scanner import validate_file, validate_folder
from app.theme import STYLESHEET
from app.widgets import DropZone, NumericItem, ResultPlaceholder, TargetListDialog
from app.windows_handle_release import HandleReleaseResult
from app.workers import (
    FileOperationWorker,
    ReleaseHandleWorker,
    TargetScanWorker,
    TerminateAllProcessesWorker,
    TerminateAndRecycleWorker,
    TerminateProcessWorker,
)




class MainWindow(QMainWindow):
    def __init__(
        self,
        initial_targets: tuple[str, ...] = (),
        *,
        language: str | None = None,
    ) -> None:
        super().__init__()
        if language is not None:
            i18n.set_language(language)
        self._mode = "file"
        self._target_paths: tuple[Path, ...] = ()
        self._selected_path: Path | None = None
        self._thread: QThread | None = None
        self._worker: TargetScanWorker | None = None
        self._scan_in_progress = False
        self._scan_cancel_requested = False
        self._terminate_thread: QThread | None = None
        self._terminate_worker: (
            ReleaseHandleWorker
            | TerminateProcessWorker
            | TerminateAllProcessesWorker
            | TerminateAndRecycleWorker
            | FileOperationWorker
            | None
        ) = None
        self._termination_in_progress = False
        self._rescan_after_termination = False
        self._locks_by_path: dict[str, tuple[ProcessInfo, ...]] = {}
        self._scan_result: BatchScanResult | None = None
        self._last_move: tuple[Path, Path] | None = None
        self._running_as_admin = is_running_as_admin()
        self._status_source = ("等待文件", {})
        self._result_hint_parts: tuple[
            tuple[str, dict[str, object]], ...
        ] = (("未选择文件", {}),)
        self._empty_file_name_source = "尚未选择文件"
        self._empty_file_meta_source = "连续拖入多个文件或文件夹，再点击“开始扫描”"
        self._result_title_source = "占用进程"

        self._app_icon = app_icon()
        self.setWindowIcon(self._app_icon)
        self.setWindowTitle(i18n.tr(APP_DISPLAY_NAME))
        self.resize(1080, 760)
        self.setMinimumSize(900, 650)
        self.setAcceptDrops(not self._running_as_admin)

        self._build_ui()
        self._apply_styles()
        self._install_shortcuts()

        if initial_targets:
            self._select_paths(list(initial_targets))

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        outer = QGridLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.content = QWidget()
        self.content.setObjectName("content")
        self.content.setMaximumWidth(1500)
        self.content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        outer.addWidget(self.content, 0, 1)
        outer.setColumnStretch(0, 1)
        outer.setColumnStretch(1, 100)
        outer.setColumnStretch(2, 1)
        outer.setRowStretch(0, 1)

        page = QVBoxLayout(self.content)
        page.setContentsMargins(32, 28, 32, 24)
        page.setSpacing(14)

        page.addLayout(self._build_header())

        self.drop_zone = DropZone()
        self.drop_zone.setMaximumHeight(180)
        self.drop_zone.set_admin_mode(self._running_as_admin)
        self.drop_zone.targetDropped.connect(self._select_dropped_paths)
        self.drop_zone.browseRequested.connect(self._browse_target)
        page.addWidget(self.drop_zone)

        page.addWidget(self._build_file_card())
        page.addWidget(self._build_results_card(), 1)
        page.addLayout(self._build_footer())

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(13)

        self.app_logo = QLabel()
        self.app_logo.setObjectName("logo")
        self.app_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.app_logo.setFixedSize(44, 44)
        self.app_logo.setPixmap(self._app_icon.pixmap(QSize(40, 40)))
        self.app_logo.setAccessibleName("UnlockGo")

        title_column = QVBoxLayout()
        title_column.setSpacing(2)
        title = QLabel("UnlockGo")
        title.setObjectName("appTitle")
        self.app_subtitle = QLabel(i18n.tr("轻松找到并处理占用文件的进程"))
        self.app_subtitle.setObjectName("appSubtitle")
        title_column.addWidget(title)
        title_column.addWidget(self.app_subtitle)

        mode_switch = QFrame()
        mode_switch.setObjectName("modeSwitch")
        mode_layout = QHBoxLayout(mode_switch)
        mode_layout.setContentsMargins(2, 2, 2, 2)
        mode_layout.setSpacing(2)

        self.file_mode_button = QPushButton(i18n.tr("文件"))
        self.folder_mode_button = QPushButton(i18n.tr("文件夹"))
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)

        for button in (self.file_mode_button, self.folder_mode_button):
            button.setObjectName("modeButton")
            button.setToolTip(i18n.tr("可连续添加多个目标，确认后再统一扫描"))
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.mode_group.addButton(button)
            mode_layout.addWidget(button)

        self.file_mode_button.setChecked(True)
        self.file_mode_button.clicked.connect(lambda: self._set_mode("file"))
        self.folder_mode_button.clicked.connect(lambda: self._set_mode("folder"))

        self.status_chip = QLabel(i18n.tr("等待文件"))
        self.status_chip.setObjectName("statusChip")
        self.status_chip.setProperty("state", "idle")
        self.status_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.language_combo = QComboBox()
        self.language_combo.setObjectName("languageCombo")
        self.language_combo.setFixedWidth(92)
        for language, name in i18n.LANGUAGE_NAMES.items():
            self.language_combo.addItem(name, language)
        self._sync_language_combo()
        self.language_combo.setToolTip(i18n.tr("界面语言"))
        self.language_combo.setAccessibleName(i18n.tr("界面语言"))
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)

        header.addWidget(self.app_logo)
        header.addLayout(title_column)
        header.addStretch()
        header.addWidget(mode_switch)
        header.addSpacing(5)
        header.addWidget(self.language_combo)
        header.addSpacing(5)
        header.addWidget(self.status_chip)
        return header

    def _sync_language_combo(self) -> None:
        index = self.language_combo.findData(i18n.get_language())
        if index < 0:
            return
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(index)
        self.language_combo.blockSignals(False)

    def _on_language_changed(self, index: int) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            self._sync_language_combo()
            return

        language = self.language_combo.itemData(index)
        if not language or language == i18n.get_language():
            return
        i18n.set_language(str(language))
        i18n.save_language(str(language))
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(i18n.tr(APP_DISPLAY_NAME))
        app = QApplication.instance()
        if app is not None:
            app.setApplicationDisplayName(i18n.tr(APP_DISPLAY_NAME))

        self._sync_language_combo()
        self.language_combo.setToolTip(i18n.tr("界面语言"))
        self.language_combo.setAccessibleName(i18n.tr("界面语言"))
        self.app_subtitle.setText(i18n.tr("轻松找到并处理占用文件的进程"))
        self.file_mode_button.setText(i18n.tr("文件"))
        self.folder_mode_button.setText(i18n.tr("文件夹"))
        for button in (self.file_mode_button, self.folder_mode_button):
            button.setToolTip(i18n.tr("可连续添加多个目标，确认后再统一扫描"))
        self.drop_zone.retranslate()

        self.recursive_checkbox.setText(i18n.tr("包含子文件夹"))
        self.rename_button.setText(i18n.tr("重命名"))
        self.move_button.setText(i18n.tr("移动"))
        self.copy_button.setText(i18n.tr("复制"))
        self.rescan_button.setText(
            i18n.tr("重新扫描" if self._scan_result is not None else "开始扫描")
        )
        self.clear_targets_button.setText(i18n.tr("清空"))
        self.clear_targets_button.setToolTip(
            i18n.tr("清空当前待扫描或已扫描的目标")
        )
        self.cancel_scan_button.setText(
            i18n.tr("正在取消…" if self._scan_cancel_requested else "取消扫描")
        )
        self.result_title.setText(i18n.tr(self._result_title_source))
        self.target_list_button.setToolTip(
            i18n.tr("查看所有文件或文件夹；移动后可撤销移动或删除到回收站。")
        )
        self.batch_terminate_button.setText(i18n.tr("一键结束全部关联进程"))
        self.batch_terminate_button.setToolTip(
            i18n.tr("结束当前扫描结果中的全部关联进程；包含关键进程时会整批拒绝。")
        )
        self.table.setHorizontalHeaderLabels(
            [
                i18n.tr("文件"),
                i18n.tr("进程名"),
                i18n.tr("PID"),
                i18n.tr("句柄"),
                i18n.tr("候选来源"),
                i18n.tr("目标状态"),
                i18n.tr("程序路径"),
                i18n.tr("运行账户"),
                i18n.tr("操作"),
            ]
        )
        self.privacy_label.setText(i18n.tr("本地运行 · 文件不会上传"))
        self.permission_label.setText(
            i18n.tr("管理员权限")
            if self._running_as_admin
            else i18n.tr("普通权限 · 部分系统进程不可见")
        )
        self.admin_restart_button.setText(i18n.tr("以管理员身份重启"))
        self.admin_restart_button.setToolTip(
            i18n.tr("通过 Windows UAC 重新启动；当前目标列表会保留。")
        )

        if self._target_paths:
            self._update_target_summary(self._target_paths)
        else:
            self.file_name_label.setText(i18n.tr(self._empty_file_name_source))
            self.file_meta_label.setText(i18n.tr(self._empty_file_meta_source))
            self.file_name_label.setToolTip("")
            self.file_meta_label.setToolTip("")

        status_source, status_values = self._status_source
        status_state = str(self.status_chip.property("state") or "idle")
        hint_parts = self._result_hint_parts

        if self._scan_result is not None:
            self._show_batch_result(self._scan_result)
        else:
            self._update_action_buttons()
            self.placeholder.retranslate()

        # Refreshing a scan result also refreshes its summary. Restore the
        # transient status and hint so switching language does not clobber them.
        self._set_status(status_source, status_state, **status_values)
        self._set_result_hint_parts(hint_parts)

        self.language_combo.setEnabled(
            not self._scan_in_progress and not self._termination_in_progress
        )

    def _build_file_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("fileCard")

        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 13, 18, 13)
        layout.setSpacing(13)

        text_column = QVBoxLayout()
        text_column.setSpacing(3)
        self.file_name_label = QLabel(i18n.tr("尚未选择文件"))
        self.file_name_label.setObjectName("fileName")
        self.file_meta_label = QLabel(
            i18n.tr("连续拖入多个文件或文件夹，再点击“开始扫描”")
        )
        self.file_meta_label.setObjectName("fileMeta")
        self.file_meta_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_column.addWidget(self.file_name_label)
        text_column.addWidget(self.file_meta_label)

        self.recursive_checkbox = QCheckBox(i18n.tr("包含子文件夹"))
        self.recursive_checkbox.setObjectName("recursiveCheckbox")
        self.recursive_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.recursive_checkbox.hide()
        self.recursive_checkbox.toggled.connect(self._on_recursive_changed)

        self.rename_button = QPushButton(i18n.tr("重命名"))
        self.rename_button.setObjectName("secondaryButton")
        self.rename_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rename_button.setEnabled(False)
        self.rename_button.clicked.connect(self._request_rename)

        self.move_button = QPushButton(i18n.tr("移动"))
        self.move_button.setObjectName("secondaryButton")
        self.move_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.move_button.setEnabled(False)
        self.move_button.clicked.connect(self._request_move)

        self.copy_button = QPushButton(i18n.tr("复制"))
        self.copy_button.setObjectName("secondaryButton")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._request_copy)

        self.rescan_button = QPushButton(i18n.tr("开始扫描"))
        self.rescan_button.setObjectName("secondaryButton")
        self.rescan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rescan_button.setEnabled(False)
        self.rescan_button.clicked.connect(self._rescan)

        self.clear_targets_button = QPushButton(i18n.tr("清空"))
        self.clear_targets_button.setObjectName("secondaryButton")
        self.clear_targets_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_targets_button.setMinimumWidth(58)
        self.clear_targets_button.setToolTip(
            i18n.tr("清空当前待扫描或已扫描的目标")
        )
        self.clear_targets_button.clicked.connect(self._request_clear_targets)
        self.clear_targets_button.hide()

        self.cancel_scan_button = QPushButton(i18n.tr("取消扫描"))
        self.cancel_scan_button.setObjectName("secondaryButton")
        self.cancel_scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_scan_button.hide()
        self.cancel_scan_button.clicked.connect(self._cancel_scan)

        layout.addLayout(text_column, 1)
        layout.addWidget(self.recursive_checkbox)
        layout.addWidget(self.clear_targets_button)
        layout.addWidget(self.rename_button)
        layout.addWidget(self.move_button)
        layout.addWidget(self.copy_button)
        layout.addWidget(self.rescan_button)
        layout.addWidget(self.cancel_scan_button)
        return card

    def _build_results_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("resultsCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 18)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        self.result_title = QLabel(i18n.tr("占用进程"))
        self.result_title.setObjectName("sectionTitle")
        self.result_hint = QLabel(i18n.tr("未选择文件"))
        self.result_hint.setObjectName("resultHint")
        self.result_hint.setWordWrap(True)
        heading.addWidget(self.result_title)
        heading.addStretch()
        self.target_list_button = QPushButton(i18n.tr("目标列表"))
        self.target_list_button.setObjectName("compactButton")
        self.target_list_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.target_list_button.setEnabled(False)
        self.target_list_button.setToolTip(
            i18n.tr("查看所有文件或文件夹；移动后可撤销移动或删除到回收站。")
        )
        self.target_list_button.clicked.connect(self._show_target_list)
        heading.addWidget(self.target_list_button)
        heading.addSpacing(8)
        self.batch_terminate_button = QPushButton(
            i18n.tr("一键结束全部关联进程")
        )
        self.batch_terminate_button.setObjectName("batchButton")
        self.batch_terminate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.batch_terminate_button.setEnabled(False)
        self.batch_terminate_button.setToolTip(
            i18n.tr("结束当前扫描结果中的全部关联进程；包含关键进程时会整批拒绝。")
        )
        self.batch_terminate_button.clicked.connect(
            self._request_terminate_all_targets
        )
        heading.addWidget(self.batch_terminate_button)
        layout.addLayout(heading)
        layout.addWidget(self.result_hint)

        self.result_stack = QStackedWidget()
        self.placeholder = ResultPlaceholder()
        self.table = self._build_table()
        self.result_stack.addWidget(self.placeholder)
        self.result_stack.addWidget(self.table)
        layout.addWidget(self.result_stack, 1)
        return card

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, 9)
        table.setObjectName("processTable")
        table.setHorizontalHeaderLabels(
            [
                i18n.tr("文件"),
                i18n.tr("进程名"),
                i18n.tr("PID"),
                i18n.tr("句柄"),
                i18n.tr("候选来源"),
                i18n.tr("目标状态"),
                i18n.tr("程序路径"),
                i18n.tr("运行账户"),
                i18n.tr("操作"),
            ]
        )
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setSortingEnabled(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(44)
        table.setColumnHidden(0, True)

        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 150)
        table.setColumnWidth(1, 130)
        table.setColumnWidth(2, 64)
        table.setColumnWidth(3, 64)
        table.setColumnWidth(4, 147)
        table.setColumnWidth(5, 86)
        table.setColumnWidth(7, 215)
        table.setColumnWidth(8, 168)
        return table

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setSpacing(8)

        self.privacy_label = QLabel(i18n.tr("本地运行 · 文件不会上传"))
        self.privacy_label.setObjectName("footerText")

        self.permission_label = QLabel()
        self.permission_label.setObjectName("permissionChip")
        if self._running_as_admin:
            self.permission_label.setText(i18n.tr("管理员权限"))
            self.permission_label.setProperty("state", "ok")
        else:
            self.permission_label.setText(i18n.tr("普通权限 · 部分系统进程不可见"))
            self.permission_label.setProperty("state", "limited")

        self.admin_restart_button = QPushButton(i18n.tr("以管理员身份重启"))
        self.admin_restart_button.setObjectName("permissionButton")
        self.admin_restart_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.admin_restart_button.setToolTip(
            i18n.tr("通过 Windows UAC 重新启动；当前目标列表会保留。")
        )
        self.admin_restart_button.clicked.connect(self._request_admin_restart)
        self.admin_restart_button.setVisible(not self._running_as_admin)

        footer.addWidget(self.privacy_label)
        footer.addStretch()
        footer.addWidget(self.permission_label)
        footer.addWidget(self.admin_restart_button)
        return footer

    def _request_admin_restart(self) -> None:
        if (
            self._running_as_admin
            or self._scan_in_progress
            or self._termination_in_progress
        ):
            return

        target_count = len(self._target_paths)
        target_note = (
            i18n.tr(
                "当前 {count} 个目标会保留，扫描结果需要重新生成。",
                count=target_count,
            )
            if target_count
            else i18n.tr("重启后可继续添加文件或文件夹。")
        )
        answer = QMessageBox.question(
            self,
            i18n.tr("以管理员身份重启？"),
            i18n.tr("将通过 Windows UAC 关闭当前窗口并重新启动 UnlockGo。\n\n")
            + target_note,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        extra_args: list[str] = []
        for target in self._target_paths:
            extra_args.extend(("--target", str(target)))

        self.admin_restart_button.setEnabled(False)
        if request_admin_relaunch(extra_args):
            self.close()
            return

        self.admin_restart_button.setEnabled(True)
        QMessageBox.warning(
            self,
            i18n.tr("未能获得管理员权限"),
            i18n.tr(
                "用户取消了 UAC 提示，或 Windows 无法启动新实例。\n"
                "当前窗口保持不变。"
            ),
        )

    def _install_shortcuts(self) -> None:
        open_shortcut = QShortcut(QKeySequence.StandardKey.Open, self)
        open_shortcut.activated.connect(self._browse_target)
        refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        refresh_shortcut.activated.connect(self._rescan)

    def _set_mode(self, mode: str) -> None:
        if mode == self._mode or self._scan_in_progress or self._termination_in_progress:
            if mode == self._mode:
                self.file_mode_button.setChecked(mode == "file")
                self.folder_mode_button.setChecked(mode == "folder")
            return

        self._mode = mode
        self.file_mode_button.setChecked(mode == "file")
        self.folder_mode_button.setChecked(mode == "folder")
        self.drop_zone.set_mode(mode)
        self.recursive_checkbox.setVisible(mode == "folder")
        self._result_title_source = "占用情况"
        self.result_title.setText(i18n.tr(self._result_title_source))
        self._clear_selection()

    def _request_clear_targets(self) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return
        self._clear_selection()

    def _clear_selection(self) -> None:
        self._target_paths = ()
        self._selected_path = None
        self._scan_result = None
        self._last_move = None
        self._locks_by_path = {}
        self.table.clearContents()
        self.table.setRowCount(0)
        self.target_list_button.setText(i18n.tr("目标列表"))
        self.target_list_button.setEnabled(False)
        self.clear_targets_button.hide()
        self.rescan_button.setText(i18n.tr("开始扫描"))
        self.rescan_button.setEnabled(False)
        self.rename_button.setEnabled(False)
        self.move_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.batch_terminate_button.setEnabled(False)
        self.cancel_scan_button.hide()
        self.result_stack.setCurrentWidget(self.placeholder)

        if self._mode == "folder":
            self._empty_file_name_source = "尚未选择文件夹"
            self._empty_file_meta_source = "可连续拖入或选择多个文件夹，再点击“开始扫描”"
            self.file_name_label.setText(i18n.tr(self._empty_file_name_source))
            self.file_meta_label.setText(i18n.tr(self._empty_file_meta_source))
            self._set_status("等待文件夹", "idle")
            self._set_result_hint("未选择文件夹")
            self.placeholder.show_message(
                "◇",
                "尚未选择文件夹",
                "连续添加一个或多个文件夹，然后点击“开始扫描”。",
            )
        else:
            self._empty_file_name_source = "尚未选择文件"
            self._empty_file_meta_source = "可连续拖入或选择多个文件，再点击“开始扫描”"
            self.file_name_label.setText(i18n.tr(self._empty_file_name_source))
            self.file_meta_label.setText(i18n.tr(self._empty_file_meta_source))
            self._set_status("等待文件", "idle")
            self._set_result_hint("未选择文件")
            self.placeholder.show_message(
                "◇",
                "尚未选择文件",
                "连续添加一个或多个文件，然后点击“开始扫描”。",
            )

    def _browse_target(self) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return

        first_target = self._target_paths[0] if self._target_paths else None
        if first_target is None:
            start_dir = str(Path.home())
        elif first_target.is_dir():
            start_dir = str(first_target)
        else:
            start_dir = str(first_target.parent)

        if self._mode == "folder":
            selected = QFileDialog.getExistingDirectory(
                self,
                i18n.tr("选择要检查的文件夹"),
                start_dir,
            )
            selected_paths = [selected] if selected else []
        else:
            selected_paths, _ = QFileDialog.getOpenFileNames(
                self,
                i18n.tr("选择要检查的文件"),
                start_dir,
                i18n.tr("所有文件 (*.*)"),
            )

        if selected_paths:
            self._select_paths(selected_paths)

    def _select_dropped_paths(self, target_paths: list[str]) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return
        if not target_paths:
            return
        self._select_paths(target_paths)

    def _select_dropped_path(self, target_path: str) -> None:
        self._select_dropped_paths([target_path])

    def _select_path(self, target_path: str) -> None:
        self._select_paths([target_path])

    def _select_paths(self, target_paths: list[str] | tuple[str, ...]) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return

        validated: list[Path] = []
        seen: set[str] = {self._path_key(path) for path in self._target_paths}
        for raw_path in target_paths:
            try:
                path = Path(raw_path).expanduser()
                linked = linked_path_component(path)
                if linked is not None:
                    raise FileOperationError(
                        i18n.tr(
                            "为安全起见，不支持操作符号链接或目录联接目标：{path}",
                            path=linked,
                        )
                    )
                if not path.exists():
                    raise FileNotFoundError(
                        i18n.tr("目标不存在：{path}", path=path)
                    )
                if not path.is_file() and not path.is_dir():
                    raise ValueError(
                        i18n.tr("目标不是文件或文件夹：{path}", path=path)
                    )
                path = path.resolve()
            except (FileNotFoundError, FileOperationError, ValueError, OSError) as exc:
                QMessageBox.warning(self, i18n.tr("无法打开目标"), str(exc))
                return
            key = self._path_key(path)
            if key not in seen:
                seen.add(key)
                validated.append(path)

        if not validated:
            self._set_status("目标已在列表中", "idle")
            self._set_result_hint("当前目标已添加，无需重复添加")
            return

        combined = list(self._target_paths)
        combined.extend(validated)
        targets = tuple(combined)
        new_mode = "folder" if all(path.is_dir() for path in targets) else "file"
        if new_mode != self._mode:
            self._mode = new_mode
            self.file_mode_button.setChecked(new_mode == "file")
            self.folder_mode_button.setChecked(new_mode == "folder")
            self.drop_zone.set_mode(new_mode)
        self.recursive_checkbox.setVisible(any(path.is_dir() for path in targets))
        self._result_title_source = "待扫描目标"
        self.result_title.setText(i18n.tr(self._result_title_source))

        self._target_paths = targets
        self._selected_path = targets[0] if len(targets) == 1 else None
        self._scan_result = None
        self._locks_by_path = {}
        self.table.clearContents()
        self.table.setRowCount(0)
        self._update_target_summary(targets)
        self.clear_targets_button.show()
        self.clear_targets_button.setEnabled(True)
        self.rescan_button.setText(i18n.tr("开始扫描"))
        self.rescan_button.setEnabled(True)
        self._update_action_buttons()

        self._set_status("等待开始扫描", "idle")
        self._set_result_hint(
            "已添加 {count} 个目标，尚未扫描",
            count=len(targets),
        )
        self.placeholder.show_message(
            "+",
            "已添加 {count} 个目标",
            "可以继续拖入文件或文件夹；确认无误后点击“开始扫描”。",
            title_values={"count": len(targets)},
        )
        self.result_stack.setCurrentWidget(self.placeholder)

    def _rescan(self) -> None:
        if (
            not self._target_paths
            or self._scan_in_progress
            or self._termination_in_progress
        ):
            return

        existing = tuple(path for path in self._target_paths if path.exists())
        missing = tuple(path for path in self._target_paths if not path.exists())
        if not existing:
            self.rescan_button.setEnabled(False)
            self._set_status("目标已不存在", "idle")
            self._set_result_hint("文件已移动或删除")
            self.placeholder.show_message(
                "!",
                "目标已不存在",
                "目标可能已被其他程序移动或删除，请重新选择。",
            )
            self.result_stack.setCurrentWidget(self.placeholder)
            return

        if missing:
            QMessageBox.information(
                self,
                i18n.tr("部分目标已不存在"),
                i18n.tr(
                    "有 {count} 个目标已被移动或删除，将扫描剩余目标。",
                    count=len(missing),
                ),
            )
            self._target_paths = existing
            self._selected_path = existing[0] if len(existing) == 1 else None
            self._update_target_summary(existing)
        self._begin_scan(existing)
    def _cancel_scan(self) -> None:
        if (
            not self._scan_in_progress
            or self._scan_cancel_requested
            or self._thread is None
        ):
            return

        self._scan_cancel_requested = True
        self._thread.requestInterruption()
        self.cancel_scan_button.setEnabled(False)
        self.admin_restart_button.setEnabled(False)
        self.cancel_scan_button.setText(i18n.tr("正在取消…"))
        self._set_status("正在取消", "working")
        self._set_result_hint("正在停止扫描…")

    @staticmethod
    def _format_process_start_time(process: ProcessInfo) -> str:
        if process.create_time is None:
            return i18n.tr("无法读取")
        try:
            started_at = datetime.fromtimestamp(process.create_time)
            return started_at.strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return i18n.tr("无法读取")

    def _request_terminate_process(self, process: ProcessInfo) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return

        risk = process_risk(process)
        if risk == "self":
            QMessageBox.warning(
                self,
                i18n.tr("当前程序"),
                i18n.tr("不能结束正在运行 UnlockGo 的当前进程。"),
            )
            return
        if risk == "critical":
            QMessageBox.warning(
                self,
                i18n.tr("受保护的进程"),
                i18n.tr(
                    "{name} 是 Windows 关键进程，不能结束。",
                    name=process.name,
                ),
            )
            return

        newline = chr(10)
        details = newline.join(
            [
                i18n.tr("进程：{name}", name=process.name),
                i18n.tr("PID：{pid}", pid=process.pid),
                i18n.tr("路径：{path}", path=process.exe or i18n.tr("无法读取")),
                i18n.tr(
                    "启动时间：{time}",
                    time=self._format_process_start_time(process),
                ),
                "",
                i18n.tr("结束后，该进程未保存的数据可能丢失。"),
            ]
        )
        if risk == "high":
            details += newline + i18n.tr(
                "这是 Windows 高风险进程，可能导致桌面、任务栏或系统功能暂时中断。"
            )
            answer = QMessageBox.warning(
                self,
                i18n.tr("结束高风险进程？"),
                details + newline * 2 + i18n.tr("确定要继续吗？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
        else:
            answer = QMessageBox.question(
                self,
                i18n.tr("结束进程？"),
                details + newline * 2 + i18n.tr("确定要结束这个进程吗？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )

        if answer == QMessageBox.StandardButton.Yes:
            self._begin_terminate(process)

    def _request_release_handle(
        self,
        process: ProcessInfo,
        file_path: str,
        handle_identity: HandleIdentity,
    ) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return

        risk = process_risk(process)
        if risk == "self":
            QMessageBox.warning(
                self,
                i18n.tr("当前程序"),
                i18n.tr("不能释放正在运行 UnlockGo 的当前进程句柄。"),
            )
            return
        if risk == "critical":
            QMessageBox.warning(
                self,
                i18n.tr("受保护的进程"),
                i18n.tr(
                    "{name} 是 Windows 关键进程，不能释放它的句柄。",
                    name=process.name,
                ),
            )
            return

        details = "\n".join(
            [
                i18n.tr("文件：{path}", path=file_path),
                i18n.tr("进程：{name}", name=process.name),
                i18n.tr("PID：{pid}", pid=process.pid),
                i18n.tr("程序路径：{path}", path=process.exe or i18n.tr("无法读取")),
                i18n.tr(
                    "启动时间：{time}",
                    time=self._format_process_start_time(process),
                ),
                i18n.tr(
                    "句柄：0x{handle}",
                    handle=f"{handle_identity.handle_value:X}",
                ),
                "",
                i18n.tr("此操作只关闭该进程持有的一个目标句柄，不会结束进程。"),
                i18n.tr(
                    "如果程序没有处理句柄失效，后续读写可能报错、重新占用文件或崩溃。"
                ),
            ]
        )
        if risk == "high":
            details += i18n.tr(
                "\n\n这是 Windows 高风险进程，释放句柄可能影响桌面或系统功能。"
            )
            answer = QMessageBox.warning(
                self,
                i18n.tr("释放高风险进程句柄？"),
                details + i18n.tr("\n\n确定要继续吗？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
        else:
            answer = QMessageBox.question(
                self,
                i18n.tr("释放文件句柄？"),
                details + i18n.tr("\n\n确定要继续吗？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )

        if answer == QMessageBox.StandardButton.Yes:
            self._begin_release_handle(
                process,
                file_path,
                handle_identity,
                allow_high_risk=risk == "high",
            )

    def _unique_processes_for_path(self, file_path: str) -> tuple[ProcessInfo, ...]:
        processes = self._locks_by_path.get(self._path_key(file_path), ())
        unique: list[ProcessInfo] = []
        seen_pids: set[int] = set()
        for process in processes:
            if process.pid in seen_pids:
                continue
            seen_pids.add(process.pid)
            unique.append(process)
        return tuple(unique)

    def _processes_for_all_targets(self) -> tuple[ProcessInfo, ...]:
        if self._scan_result is None:
            return ()
        unique: list[ProcessInfo] = []
        seen_pids: set[int] = set()
        for lock in self._scan_result.locks:
            if lock.lock_state != LockState.BLOCKING:
                continue
            if lock.process.pid in seen_pids:
                continue
            seen_pids.add(lock.process.pid)
            unique.append(lock.process)
        return tuple(unique)

    def _request_terminate_all_targets(self) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return

        processes = self._processes_for_all_targets()
        if not processes:
            QMessageBox.information(
                self,
                i18n.tr("没有关联进程"),
                i18n.tr(
                    "当前扫描结果中没有可结束的关联进程，请重新扫描后再试。"
                ),
            )
            return

        protected = [
            process
            for process in processes
            if process_risk(process) in {"self", "critical"}
        ]
        if protected:
            labels = "\n".join(
                f"{process.name} (PID {process.pid})" for process in protected[:6]
            )
            QMessageBox.warning(
                self,
                i18n.tr("包含受保护进程"),
                i18n.tr(
                    "一键结束不会结束 UnlockGo 自身或 Windows 关键进程，"
                    "因此本次操作已取消，未结束任何进程。"
                )
                + chr(10) * 2
                + labels,
            )
            return

        high_risk = [
            process for process in processes if process_risk(process) == "high"
        ]
        labels = "\n".join(
            f"{index}. {process.name}  PID {process.pid}"
            for index, process in enumerate(processes[:10], start=1)
        )
        if len(processes) > 10:
            labels += "\n" + i18n.tr(
                "…另有 {count} 个进程",
                count=len(processes) - 10,
            )
        target_count = len(self._target_paths)
        details = (
            i18n.tr(
                "当前扫描了 {target_count} 个目标，共发现 {process_count} 个关联进程。",
                target_count=target_count,
                process_count=len(processes),
            )
            + chr(10) * 2
            + labels
            + chr(10) * 2
            + i18n.tr("这会一次结束全部关联进程，未保存的数据可能丢失。")
        )
        if high_risk:
            details += chr(10) + i18n.tr(
                "其中包含 Windows 高风险进程，可能导致桌面、任务栏或系统功能暂时中断。"
            )
        answer = QMessageBox.warning(
            self,
            i18n.tr("一键结束全部关联进程？"),
            details + chr(10) * 2 + i18n.tr("确定要继续吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._begin_terminate_all(processes)

    def _request_terminate_all(self, file_path: str) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return

        processes = self._unique_processes_for_path(file_path)
        if not processes:
            QMessageBox.information(
                self,
                i18n.tr("没有关联进程"),
                i18n.tr("当前没有记录到该文件的关联进程，请重新扫描后再试。"),
            )
            return

        protected = [
            process
            for process in processes
            if process_risk(process) in {"self", "critical"}
        ]
        if protected:
            labels = "\n".join(
                f"{process.name} (PID {process.pid})" for process in protected[:6]
            )
            QMessageBox.warning(
                self,
                i18n.tr("包含受保护进程"),
                i18n.tr(
                    "一键结束不会结束 UnlockGo 自身或 Windows 关键进程，"
                    "因此本次操作已取消，未结束任何进程。"
                )
                + chr(10) * 2
                + labels,
            )
            return

        high_risk = [
            process for process in processes if process_risk(process) == "high"
        ]
        labels = "\n".join(
            f"{index}. {process.name}  PID {process.pid}"
            for index, process in enumerate(processes[:10], start=1)
        )
        if len(processes) > 10:
            labels += "\n" + i18n.tr(
                "…另有 {count} 个进程",
                count=len(processes) - 10,
            )
        details = (
            i18n.tr("文件：{path}", path=file_path)
            + chr(10)
            + i18n.tr("关联进程：{count} 个", count=len(processes))
            + chr(10) * 2
            + labels
            + chr(10) * 2
            + i18n.tr("这会结束该文件的全部关联进程，未保存的数据可能丢失。")
        )
        if high_risk:
            details += chr(10) + i18n.tr(
                "其中包含 Windows 高风险进程，可能导致桌面、任务栏或系统功能暂时中断。"
            )
        answer = QMessageBox.warning(
            self,
            i18n.tr("一键结束全部关联进程？"),
            details + chr(10) * 2 + i18n.tr("确定要继续吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._begin_terminate_all(processes)

    def _begin_terminate_all(self, processes: tuple[ProcessInfo, ...]) -> None:
        self._termination_in_progress = True
        self._rescan_after_termination = False
        self.language_combo.setEnabled(False)
        self.clear_targets_button.setEnabled(False)
        self.drop_zone.setEnabled(False)
        self.table.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.rename_button.setEnabled(False)
        self.move_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.batch_terminate_button.setEnabled(False)
        self.target_list_button.setEnabled(False)
        self.cancel_scan_button.setEnabled(False)
        self.admin_restart_button.setEnabled(False)
        self.recursive_checkbox.setEnabled(False)
        self.file_mode_button.setEnabled(False)
        self.folder_mode_button.setEnabled(False)
        self._set_status("正在结束关联进程", "working")
        self._set_result_hint(
            "正在结束 {count} 个关联进程…",
            count=len(processes),
        )

        self._terminate_thread = QThread(self)
        self._terminate_worker = TerminateAllProcessesWorker(processes)
        self._terminate_worker.moveToThread(self._terminate_thread)

        self._terminate_thread.started.connect(self._terminate_worker.run)
        self._terminate_worker.completed.connect(self._on_terminate_all_completed)
        self._terminate_worker.failed.connect(self._on_terminate_all_failed)
        self._terminate_worker.completed.connect(self._terminate_thread.quit)
        self._terminate_worker.failed.connect(self._terminate_thread.quit)
        self._terminate_worker.completed.connect(self._terminate_worker.deleteLater)
        self._terminate_worker.failed.connect(self._terminate_worker.deleteLater)
        self._terminate_thread.finished.connect(self._terminate_thread.deleteLater)
        self._terminate_thread.finished.connect(self._on_terminate_thread_finished)
        self._terminate_thread.start()

    def _on_terminate_all_completed(self, result: object) -> None:
        if not isinstance(result, BatchTerminationResult):
            self._on_terminate_all_failed(
                i18n.tr("批量结束进程返回了无效结果。")
            )
            return
        self._rescan_after_termination = True
        if result.failures:
            failures = "\n".join(
                f"{item.process.name} (PID {item.process.pid})：{item.message}"
                for item in result.failures[:6]
            )
            self._set_status("部分进程未结束", "error")
            self._set_result_hint("正在刷新占用状态…")
            QMessageBox.warning(
                self,
                i18n.tr("部分关联进程未结束"),
                i18n.tr(
                    "已结束 {ended} 个进程，{failed} 个进程未能结束。",
                    ended=len(result.terminated),
                    failed=len(result.failures),
                )
                + chr(10) * 2
                + failures,
            )
            return
        self._set_status("关联进程已结束", "success")
        self._set_result_hint("全部关联进程已结束，正在重新扫描…")

    def _on_terminate_all_failed(self, message: str) -> None:
        self._rescan_after_termination = False
        self._set_status("批量结束失败", "error")
        self._set_result_hint("未结束关联进程")
        QMessageBox.warning(
            self,
            i18n.tr("一键结束失败"),
            message or i18n.tr("未能结束关联进程，请重新扫描后再试。"),
        )
    def _request_recycle(self, file_path: str, process: ProcessInfo) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return

        risk = process_risk(process)
        if risk == "self":
            QMessageBox.warning(
                self,
                i18n.tr("当前程序"),
                i18n.tr("不能结束正在运行 UnlockGo 的当前进程。"),
            )
            return
        if risk == "critical":
            QMessageBox.warning(
                self,
                i18n.tr("受保护的进程"),
                i18n.tr(
                    "{name} 是 Windows 关键进程，不能结束。",
                    name=process.name,
                ),
            )
            return

        target = Path(file_path)
        if not target.is_file():
            QMessageBox.warning(
                self,
                i18n.tr("文件已不存在"),
                i18n.tr("目标文件可能已被移动或删除，请重新扫描后再试。"),
            )
            return

        other_processes = tuple(
            item
            for item in self._locks_by_path.get(self._path_key(file_path), ())
            if item.pid != process.pid
        )
        if other_processes:
            labels = ", ".join(
                f"{item.name} (PID {item.pid})" for item in other_processes[:5]
            )
            if len(other_processes) > 5:
                labels += i18n.tr(" 等")
            QMessageBox.warning(
                self,
                i18n.tr("仍有其他占用者"),
                i18n.tr(
                    "该文件还被其他进程占用，UnlockGo 不会批量结束这些进程。"
                )
                + chr(10) * 2
                + labels
                + chr(10) * 2
                + i18n.tr("请先逐条处理其他占用者，再执行“结束并删除”。"),
            )
            return

        newline = chr(10)
        details = newline.join(
            [
                i18n.tr("目标文件：{path}", path=target),
                i18n.tr("进程：{name}", name=process.name),
                i18n.tr("PID：{pid}", pid=process.pid),
                i18n.tr("路径：{path}", path=process.exe or i18n.tr("无法读取")),
                i18n.tr(
                    "启动时间：{time}",
                    time=self._format_process_start_time(process),
                ),
                "",
                i18n.tr("将先结束所选进程，再重新检查文件占用。"),
                i18n.tr(
                    "仅当没有其他进程占用时，文件才会移入 Windows 回收站。"
                ),
            ]
        )
        if risk == "high":
            details += newline + i18n.tr(
                "这是 Windows 高风险进程，可能导致桌面、任务栏或系统功能暂时中断。"
            )

        answer = QMessageBox.warning(
            self,
            i18n.tr("结束进程并移入回收站？"),
            details + newline * 2 + i18n.tr("确定要继续吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._begin_recycle(process, str(target))

    def _show_target_list(self, *, highlight: Path | None = None) -> None:
        if not self._target_paths:
            return
        dialog = TargetListDialog(
            self._target_paths,
            last_move=self._last_move,
            highlight=highlight,
            parent=self,
        )
        dialog.openRequested.connect(self._open_target_location)
        dialog.recycleRequested.connect(
            lambda value: self._request_recycle_target(Path(value))
        )
        dialog.undoMoveRequested.connect(
            lambda value: self._request_undo_move(Path(value))
        )
        dialog.exec()

    def _open_target_location(self, target_path: str) -> None:
        target = Path(target_path)
        if not target.exists():
            QMessageBox.warning(
                self,
                i18n.tr("目标不存在"),
                i18n.tr("路径已不存在：\n{path}", path=target),
            )
            return
        location = target if target.is_dir() else target.parent
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(location))):
            QMessageBox.warning(
                self,
                i18n.tr("无法打开位置"),
                i18n.tr("未能打开：\n{path}", path=location),
            )

    def _request_recycle_target(self, target: Path) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return
        if not target.exists():
            QMessageBox.warning(
                self,
                i18n.tr("目标不存在"),
                i18n.tr("路径已不存在：\n{path}", path=target),
            )
            return
        item_type = i18n.tr("文件夹") if target.is_dir() else i18n.tr("文件")
        answer = QMessageBox.warning(
            self,
            i18n.tr("删除{type}到回收站？", type=item_type),
            i18n.tr(
                "目标：{path}\n\n目标会先检查可确认的占用；无法读取的受保护进程会由 Windows在回收时再次校验。如需恢复，可从回收站还原。",
                path=target,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._confirm_file_operation(target, "recycle")

    def _request_undo_move(self, target: Path) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return
        if self._last_move is None:
            QMessageBox.information(
                self,
                i18n.tr("没有移动记录"),
                i18n.tr("当前没有可撤销的移动操作。"),
            )
            return
        original, moved = self._last_move
        if self._path_key(target) != self._path_key(moved):
            QMessageBox.information(
                self,
                i18n.tr("无法撤销"),
                i18n.tr("请选择最近一次移动后的目标再执行撤销。"),
            )
            return
        if not moved.exists():
            QMessageBox.warning(
                self,
                i18n.tr("目标不存在"),
                i18n.tr("移动后的目标已不存在：\n{path}", path=moved),
            )
            return
        if not original.parent.exists():
            QMessageBox.warning(
                self,
                i18n.tr("原位置不存在"),
                i18n.tr("原文件夹已不存在：\n{path}", path=original.parent),
            )
            return
        answer = QMessageBox.warning(
            self,
            i18n.tr("撤销移动？"),
            i18n.tr(
                "将把目标移回原位置：\n{source}\n\n→ {destination}",
                source=moved,
                destination=original,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._confirm_file_operation(
                moved,
                "move",
                destination=str(original.parent),
            )
    def _operation_source_path(self) -> Path | None:
        row = self.table.currentRow()
        if row >= 0:
            file_item = self.table.item(row, 0)
            if file_item is not None:
                stored_path = file_item.data(Qt.ItemDataRole.UserRole)
                if isinstance(stored_path, str) and stored_path:
                    path = Path(stored_path)
                    if path.exists():
                        return path
        if len(self._target_paths) == 1:
            return self._target_paths[0]
        QMessageBox.information(
            self,
            i18n.tr("请选择操作对象"),
            i18n.tr(
                "当前扫描了多个目标。请先在结果表中选择一行，"
                "或在只选择一个目标时执行文件操作。"
            ),
        )
        return None

    def _processes_for_source(self, source: Path) -> tuple[ProcessInfo, ...]:
        if source.is_file():
            return self._unique_processes_for_path(str(source))
        if self._scan_result is None:
            return ()

        source_resolved = source.resolve(strict=False)
        unique: list[ProcessInfo] = []
        seen_pids: set[int] = set()
        for lock in self._scan_result.locks:
            if lock.lock_state != LockState.BLOCKING:
                continue
            try:
                Path(lock.file_path).resolve(strict=False).relative_to(source_resolved)
            except (OSError, ValueError):
                continue
            if lock.process.pid not in seen_pids:
                seen_pids.add(lock.process.pid)
                unique.append(lock.process)
        return tuple(unique)

    def _request_rename(self) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return
        source = self._operation_source_path()
        if source is None:
            return
        new_name, accepted = QInputDialog.getText(
            self,
            i18n.tr("重命名"),
            i18n.tr("为“{name}”输入新名称：", name=source.name),
            text=source.name,
        )
        if accepted and new_name.strip() and new_name.strip() != source.name:
            self._confirm_file_operation(source, "rename", new_name=new_name.strip())

    def _request_move(self) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return
        source = self._operation_source_path()
        if source is None:
            return
        destination = QFileDialog.getExistingDirectory(
            self,
            i18n.tr("选择移动到的文件夹"),
            str(source.parent),
        )
        if destination:
            self._confirm_file_operation(source, "move", destination=destination)

    def _request_copy(self) -> None:
        if self._scan_in_progress or self._termination_in_progress:
            return
        source = self._operation_source_path()
        if source is None:
            return
        destination = QFileDialog.getExistingDirectory(
            self,
            i18n.tr("选择复制到的文件夹"),
            str(source.parent),
        )
        if destination:
            self._confirm_file_operation(source, "copy", destination=destination)

    def _confirm_file_operation(
        self,
        source: Path,
        operation: str,
        *,
        destination: str = "",
        new_name: str = "",
    ) -> None:
        operation_labels = {
            "rename": i18n.tr("重命名"),
            "move": i18n.tr("移动"),
            "copy": i18n.tr("复制"),
            "recycle": i18n.tr("删除到回收站"),
        }
        if not source.exists():
            QMessageBox.warning(
                self,
                i18n.tr("目标已不存在"),
                i18n.tr("目标可能已被其他程序移动或删除，请重新扫描后再试。"),
            )
            return

        processes = self._processes_for_source(source)
        protected = [
            process
            for process in processes
            if process_risk(process) in {"self", "critical"}
        ]
        if protected:
            labels = "\n".join(
                f"{process.name} (PID {process.pid})" for process in protected[:6]
            )
            QMessageBox.warning(
                self,
                i18n.tr("目标包含受保护进程"),
                i18n.tr(
                    "该目标仍被 UnlockGo 自身或 Windows 关键进程占用，"
                    "无法自动结束这些进程，因此未执行文件操作。"
                )
                + chr(10) * 2
                + labels,
            )
            return

        if processes:
            labels = "\n".join(
                f"{index}. {process.name}  PID {process.pid}"
                for index, process in enumerate(processes[:10], start=1)
            )
            if len(processes) > 10:
                labels += "\n" + i18n.tr(
                    "…另有 {count} 个进程",
                    count=len(processes) - 10,
                )
            details = (
                i18n.tr("目标：{path}", path=source)
                + chr(10)
                + i18n.tr("文件操作：{operation}", operation=operation_labels[operation])
                + chr(10)
                + i18n.tr(
                    "将先结束 {count} 个关联进程：",
                    count=len(processes),
                )
                + chr(10) * 2
                + labels
                + chr(10) * 2
                + i18n.tr(
                    "结束后会重新检查占用；只有确认目标无占用时才执行文件操作。"
                )
                + chr(10)
                + i18n.tr("未保存的数据可能丢失。")
            )
            if any(process_risk(process) == "high" for process in processes):
                details += chr(10) + i18n.tr(
                    "其中包含 Windows 高风险进程，可能影响桌面或任务栏。"
                )
            answer = QMessageBox.warning(
                self,
                i18n.tr("解锁后执行文件操作？"),
                details + chr(10) * 2 + i18n.tr("确定要继续吗？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._begin_file_operation(
            source,
            operation,
            processes,
            destination=destination,
            new_name=new_name,
        )

    def _begin_file_operation(
        self,
        source: Path,
        operation: str,
        processes: tuple[ProcessInfo, ...],
        *,
        destination: str = "",
        new_name: str = "",
    ) -> None:
        self._termination_in_progress = True
        self._rescan_after_termination = False
        self.language_combo.setEnabled(False)
        self.clear_targets_button.setEnabled(False)
        self.drop_zone.setEnabled(False)
        self.table.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.rename_button.setEnabled(False)
        self.move_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.batch_terminate_button.setEnabled(False)
        self.target_list_button.setEnabled(False)
        self.cancel_scan_button.setEnabled(False)
        self.admin_restart_button.setEnabled(False)
        self.recursive_checkbox.setEnabled(False)
        self.file_mode_button.setEnabled(False)
        self.folder_mode_button.setEnabled(False)
        operation_label = {
            "rename": i18n.tr("重命名"),
            "move": i18n.tr("移动"),
            "copy": i18n.tr("复制"),
            "recycle": i18n.tr("删除到回收站"),
        }[operation]
        self._set_status("正在{operation}", "working", operation=operation_label)
        if processes:
            self._set_result_hint(
                "正在结束关联进程并{operation}…",
                operation=operation_label,
            )
        else:
            self._set_result_hint("正在{operation}…", operation=operation_label)

        self._terminate_thread = QThread(self)
        self._terminate_worker = FileOperationWorker(
            source_path=str(source),
            operation=operation,
            processes=processes,
            destination_directory=destination,
            new_name=new_name,
        )
        self._terminate_worker.moveToThread(self._terminate_thread)

        self._terminate_thread.started.connect(self._terminate_worker.run)
        self._terminate_worker.completed.connect(self._on_file_operation_completed)
        self._terminate_worker.failed.connect(self._on_file_operation_failed)
        self._terminate_worker.completed.connect(self._terminate_thread.quit)
        self._terminate_worker.failed.connect(self._terminate_thread.quit)
        self._terminate_worker.completed.connect(self._terminate_worker.deleteLater)
        self._terminate_worker.failed.connect(self._terminate_worker.deleteLater)
        self._terminate_thread.finished.connect(self._terminate_thread.deleteLater)
        self._terminate_thread.finished.connect(self._on_terminate_thread_finished)
        self._terminate_thread.start()

    def _on_file_operation_completed(
        self,
        source_path: str,
        operation: str,
        result_path: str,
    ) -> None:
        source = Path(source_path)
        result = Path(result_path)
        previous_move = self._last_move
        is_undo = bool(
            operation == "move"
            and previous_move is not None
            and self._path_key(source) == self._path_key(previous_move[1])
            and self._path_key(result) == self._path_key(previous_move[0])
        )

        if operation in {"rename", "move"}:
            updated: list[Path] = []
            for target in self._target_paths:
                if self._path_key(target) == self._path_key(source):
                    updated.append(result)
                else:
                    updated.append(target)
            self._target_paths = tuple(updated)
            self._selected_path = (
                self._target_paths[0] if len(self._target_paths) == 1 else None
            )
            if operation == "move":
                self._last_move = None if is_undo else (source, result)
            elif (
                previous_move is not None
                and self._path_key(source) == self._path_key(previous_move[1])
            ):
                self._last_move = (previous_move[0], result)
        elif operation == "recycle":
            source_key = self._path_key(source)
            self._target_paths = tuple(
                target
                for target in self._target_paths
                if self._path_key(target) != source_key
            )
            self._selected_path = (
                self._target_paths[0] if len(self._target_paths) == 1 else None
            )
            if (
                previous_move is not None
                and source_key == self._path_key(previous_move[1])
            ):
                self._last_move = None

        if self._target_paths:
            self._update_target_summary(self._target_paths)
        else:
            self._scan_result = None
            self._locks_by_path = {}
            self.table.clearContents()
            self.table.setRowCount(0)
            self.clear_targets_button.hide()
            self.rescan_button.setEnabled(False)

        operation_label = {
            "rename": i18n.tr("重命名"),
            "move": i18n.tr("移动"),
            "copy": i18n.tr("复制"),
            "recycle": i18n.tr("回收"),
        }[operation]
        self._rescan_after_termination = bool(self._target_paths)
        if operation == "recycle":
            self._set_status("已移入回收站", "success")
            self._set_result_hint(
                "{name} 已移入回收站",
                name=source.name or str(source),
            )
        else:
            self._set_status(
                "{operation}成功",
                "success",
                operation=operation_label,
            )
            if operation == "move" and is_undo:
                self._set_result_hint(
                    "已撤销移动，目标已恢复到：{path}",
                    path=result,
                )
            else:
                self._set_result_hint(
                    "已{operation}到：{path}",
                    operation=operation_label,
                    path=result,
                )
        if not self._target_paths:
            if operation == "recycle":
                self.placeholder.show_message(
                    "✓",
                    "已移入回收站",
                    "{path}\n\n如需恢复，可打开 Windows 回收站还原该文件。",
                    tone="success",
                    detail_values={"path": source},
                )
            elif operation == "move" and is_undo:
                self.placeholder.show_message(
                    "✓",
                    "移动已撤销",
                    str(result),
                    tone="success",
                )
            else:
                self.placeholder.show_message(
                    "✓",
                    "{operation}成功",
                    str(result),
                    tone="success",
                    title_values={"operation": operation_label},
                )
            self.result_stack.setCurrentWidget(self.placeholder)
        self._update_action_buttons()

    def _on_file_operation_failed(
        self,
        message: str,
        rescan_recommended: bool,
    ) -> None:
        self._rescan_after_termination = bool(
            rescan_recommended and self._target_paths
        )
        self._set_status("文件操作失败", "error")
        self._set_result_hint(
            "占用状态已变化，正在刷新…"
            if self._rescan_after_termination
            else "文件操作未完成"
        )
        QMessageBox.warning(
            self,
            i18n.tr("文件操作失败"),
            message or i18n.tr("未能完成文件操作，请重新扫描后再试。"),
        )
    def _begin_recycle(
        self,
        process: ProcessInfo,
        file_path: str,
        other_processes: tuple[ProcessInfo, ...] = (),
    ) -> None:
        self._termination_in_progress = True
        self._rescan_after_termination = False
        self.language_combo.setEnabled(False)
        self.clear_targets_button.setEnabled(False)
        self.drop_zone.setEnabled(False)
        self.table.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.rename_button.setEnabled(False)
        self.move_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.batch_terminate_button.setEnabled(False)
        self.target_list_button.setEnabled(False)
        self.cancel_scan_button.setEnabled(False)
        self.admin_restart_button.setEnabled(False)
        self.recursive_checkbox.setEnabled(False)
        self.file_mode_button.setEnabled(False)
        self.folder_mode_button.setEnabled(False)
        self._set_status("正在处理文件", "working")
        self._set_result_hint(
            "正在结束 {name} 并检查占用…",
            name=process.name,
        )

        self._terminate_thread = QThread(self)
        self._terminate_worker = TerminateAndRecycleWorker(
            process,
            file_path,
            other_processes,
        )
        self._terminate_worker.moveToThread(self._terminate_thread)

        self._terminate_thread.started.connect(self._terminate_worker.run)
        self._terminate_worker.completed.connect(self._on_recycle_completed)
        self._terminate_worker.failed.connect(self._on_recycle_failed)
        self._terminate_worker.completed.connect(self._terminate_thread.quit)
        self._terminate_worker.failed.connect(self._terminate_thread.quit)
        self._terminate_worker.completed.connect(self._terminate_worker.deleteLater)
        self._terminate_worker.failed.connect(self._terminate_worker.deleteLater)
        self._terminate_thread.finished.connect(self._terminate_thread.deleteLater)
        self._terminate_thread.finished.connect(self._on_terminate_thread_finished)
        self._terminate_thread.start()

    def _on_recycle_completed(self, process: object, file_path: str) -> None:
        del process
        target = Path(file_path)
        target_key = self._path_key(target)
        if (
            self._last_move is not None
            and target_key == self._path_key(self._last_move[1])
        ):
            self._last_move = None
        remaining = tuple(
            item for item in self._target_paths if self._path_key(item) != target_key
        )
        if remaining:
            self._target_paths = remaining
            self._selected_path = remaining[0] if len(remaining) == 1 else None
            self._update_target_summary(remaining)
            self._rescan_after_termination = True
            self._set_status("已移入回收站", "success")
            self._set_result_hint(
                "{name} 已移入回收站，正在刷新扫描结果…",
                name=target.name,
            )
            return

        self._target_paths = ()
        self._selected_path = None
        self._scan_result = None
        self._locks_by_path = {}
        self._rescan_after_termination = False
        self.table.clearContents()
        self.table.setRowCount(0)
        self.rescan_button.setEnabled(False)
        self.file_meta_label.setText(i18n.tr("已移入 Windows 回收站"))
        self._set_status("已移入回收站", "success")
        self._set_result_hint("{name} 已移入回收站", name=target.name)
        self.placeholder.show_message(
            "✓",
            "文件已移入回收站",
            "{path}\n\n如需恢复，可打开 Windows 回收站还原该文件。",
            tone="success",
            detail_values={"path": target},
        )
        self.result_stack.setCurrentWidget(self.placeholder)
        self._update_action_buttons()

    def _on_recycle_failed(self, message: str, rescan_recommended: bool) -> None:
        self._rescan_after_termination = bool(
            rescan_recommended and self._target_paths
        )
        self._set_status("回收失败", "error")
        self._set_result_hint(
            "占用状态已变化，正在刷新…"
            if self._rescan_after_termination
            else "文件未回收"
        )
        QMessageBox.warning(
            self,
            i18n.tr("结束并回收失败"),
            message or i18n.tr("未能结束进程或回收文件，请重新扫描后再试。"),
        )

    def _begin_terminate(self, process: ProcessInfo) -> None:
        self._termination_in_progress = True
        self._rescan_after_termination = False
        self.language_combo.setEnabled(False)
        self.clear_targets_button.setEnabled(False)
        self.drop_zone.setEnabled(False)
        self.table.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.rename_button.setEnabled(False)
        self.move_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.batch_terminate_button.setEnabled(False)
        self.target_list_button.setEnabled(False)
        self.recursive_checkbox.setEnabled(False)
        self.file_mode_button.setEnabled(False)
        self.folder_mode_button.setEnabled(False)
        self._set_status("正在结束进程", "working")
        self._set_result_hint("正在结束 {name}…", name=process.name)

        self._terminate_thread = QThread(self)
        self._terminate_worker = TerminateProcessWorker(process)
        self._terminate_worker.moveToThread(self._terminate_thread)

        self._terminate_thread.started.connect(self._terminate_worker.run)
        self._terminate_worker.completed.connect(self._on_terminate_completed)
        self._terminate_worker.failed.connect(self._on_terminate_failed)
        self._terminate_worker.completed.connect(self._terminate_thread.quit)
        self._terminate_worker.failed.connect(self._terminate_thread.quit)
        self._terminate_worker.completed.connect(self._terminate_worker.deleteLater)
        self._terminate_worker.failed.connect(self._terminate_worker.deleteLater)
        self._terminate_thread.finished.connect(self._terminate_thread.deleteLater)
        self._terminate_thread.finished.connect(self._on_terminate_thread_finished)
        self._terminate_thread.start()

    def _on_terminate_completed(self, process: object) -> None:
        self._rescan_after_termination = True
        self._set_status("进程已结束", "success")
        self._set_result_hint("进程已结束，正在重新扫描…")

    def _on_terminate_failed(self, message: str) -> None:
        self._set_status("结束失败", "error")
        self._set_result_hint("进程未结束")
        QMessageBox.warning(
            self,
            i18n.tr("结束进程失败"),
            message or i18n.tr("未能结束该进程，请重新扫描后再试。"),
        )

    def _begin_release_handle(
        self,
        process: ProcessInfo,
        file_path: str,
        handle_identity: HandleIdentity,
        *,
        allow_high_risk: bool = False,
    ) -> None:
        self._termination_in_progress = True
        self._rescan_after_termination = False
        self.language_combo.setEnabled(False)
        self.clear_targets_button.setEnabled(False)
        self.drop_zone.setEnabled(False)
        self.table.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.rename_button.setEnabled(False)
        self.move_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.batch_terminate_button.setEnabled(False)
        self.target_list_button.setEnabled(False)
        self.cancel_scan_button.setEnabled(False)
        self.admin_restart_button.setEnabled(False)
        self.recursive_checkbox.setEnabled(False)
        self.file_mode_button.setEnabled(False)
        self.folder_mode_button.setEnabled(False)
        self._set_status("正在释放句柄", "working")
        self._set_result_hint(
            "正在验证文件身份并释放 {name} 的句柄 0x{handle}…",
            name=process.name,
            handle=f"{handle_identity.handle_value:X}",
        )

        self._terminate_thread = QThread(self)
        self._terminate_worker = ReleaseHandleWorker(
            process,
            handle_identity,
            file_path,
            allow_high_risk=allow_high_risk,
        )
        self._terminate_worker.moveToThread(self._terminate_thread)

        self._terminate_thread.started.connect(self._terminate_worker.run)
        self._terminate_worker.completed.connect(self._on_release_handle_completed)
        self._terminate_worker.failed.connect(self._on_release_handle_failed)
        self._terminate_worker.completed.connect(self._terminate_thread.quit)
        self._terminate_worker.failed.connect(self._terminate_thread.quit)
        self._terminate_worker.completed.connect(self._terminate_worker.deleteLater)
        self._terminate_worker.failed.connect(self._terminate_worker.deleteLater)
        self._terminate_thread.finished.connect(self._terminate_thread.deleteLater)
        self._terminate_thread.finished.connect(self._on_terminate_thread_finished)
        self._terminate_thread.start()

    def _on_release_handle_completed(self, result: object) -> None:
        if not isinstance(result, HandleReleaseResult):
            self._on_release_handle_failed(i18n.tr("释放句柄返回了无效结果。"))
            return
        self._rescan_after_termination = True
        if result.unlocked:
            self._set_status("句柄已释放", "success")
            self._set_result_hint("目标已解锁，正在重新扫描…")
        else:
            self._set_status("句柄已释放", "success")
            self._set_result_hint(
                "{detail} 正在重新扫描…",
                detail=result.lock_probe.detail,
            )

    def _on_release_handle_failed(self, message: str) -> None:
        self._rescan_after_termination = bool(self._target_paths)
        self._set_status("释放句柄失败", "error")
        self._set_result_hint("句柄未释放，正在刷新扫描结果…")
        QMessageBox.warning(
            self,
            i18n.tr("释放句柄失败"),
            message or i18n.tr("未能安全释放该句柄，请重新扫描后再试。"),
        )

    def _on_terminate_thread_finished(self) -> None:
        self._terminate_worker = None
        self._terminate_thread = None
        self._termination_in_progress = False
        self._finish_scan_ui()
        if self._rescan_after_termination:
            self._rescan_after_termination = False
            QTimer.singleShot(0, self._rescan)

    def _on_recursive_changed(self, checked: bool) -> None:
        del checked
        if not self._target_paths or self._termination_in_progress:
            return
        self._update_target_summary(self._target_paths)
        if self._scan_in_progress:
            return

        self._scan_result = None
        self._locks_by_path = {}
        self.table.clearContents()
        self.table.setRowCount(0)
        self.result_title.setText(i18n.tr("待扫描目标"))
        self.rescan_button.setText(i18n.tr("开始扫描"))
        self.rescan_button.setEnabled(True)
        self.clear_targets_button.show()
        self.clear_targets_button.setEnabled(True)
        self._update_action_buttons()
        self._set_status("扫描范围已更新", "idle")
        self._set_result_hint("请点击“开始扫描”应用新的文件夹范围")
        self.placeholder.show_message(
            "↻",
            "扫描范围已更新",
            "点击“开始扫描”重新检查当前目标的占用情况。",
        )
        self.result_stack.setCurrentWidget(self.placeholder)

    def _update_target_summary(self, paths: tuple[Path, ...]) -> None:
        folder_count = sum(1 for path in paths if path.is_dir())
        file_count = len(paths) - folder_count
        if len(paths) == 1:
            path = paths[0]
            if path.is_dir():
                scope = i18n.tr(
                    "包含子文件夹"
                    if self.recursive_checkbox.isChecked()
                    else "仅当前层级"
                )
                meta = i18n.tr(
                    "文件夹 · {scope} · {parent}",
                    scope=scope,
                    parent=path.parent,
                )
            else:
                try:
                    stat = path.stat()
                    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                    meta = f"{self._format_size(stat.st_size)}  ·  {modified}  ·  {path.parent}"
                except OSError:
                    meta = str(path.parent)
            self.file_name_label.setText(path.name or str(path))
            self.file_name_label.setToolTip(str(path))
            self.file_meta_label.setText(meta)
            self.file_meta_label.setToolTip(str(path))
            return

        scope = i18n.tr(
            "包含子文件夹"
            if self.recursive_checkbox.isChecked()
            else "仅当前层级"
        )
        self.file_name_label.setText(i18n.tr("{count} 个目标", count=len(paths)))
        self.file_name_label.setToolTip("\n".join(str(path) for path in paths))
        parts = []
        if file_count:
            parts.append(i18n.tr("{file_count} 个文件", file_count=file_count))
        if folder_count:
            parts.append(
                i18n.tr(
                    "{folder_count} 个文件夹 · {scope}",
                    folder_count=folder_count,
                    scope=scope,
                )
            )
        try:
            common_parent = Path(os.path.commonpath([str(path.parent if path.is_file() else path) for path in paths]))
            parts.append(str(common_parent))
        except ValueError:
            pass
        self.file_meta_label.setText(" · ".join(parts))
        self.file_meta_label.setToolTip("\n".join(str(path) for path in paths))

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    def _begin_scan(self, paths: tuple[Path, ...]) -> None:
        self._scan_in_progress = True
        self._scan_cancel_requested = False
        self._scan_result = None
        self._locks_by_path = {}
        self.language_combo.setEnabled(False)
        self.drop_zone.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.clear_targets_button.setEnabled(False)
        self.rename_button.setEnabled(False)
        self.move_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.batch_terminate_button.setEnabled(False)
        self.target_list_button.setEnabled(False)
        self.admin_restart_button.setEnabled(False)
        self.cancel_scan_button.setText(i18n.tr("取消扫描"))
        self.cancel_scan_button.setEnabled(True)
        self.cancel_scan_button.show()
        self.recursive_checkbox.setEnabled(False)
        self.file_mode_button.setEnabled(False)
        self.folder_mode_button.setEnabled(False)
        self._set_status("正在扫描", "working")
        self._set_result_hint("正在读取进程信息…")

        folder_mode = any(path.is_dir() for path in paths)
        detail = (
            '正在扫描系统 File 句柄，并按目标匹配文件路径。'
            if len(paths) > 1 or folder_mode
            else '正在扫描系统 File 句柄，并补充 Restart Manager 结果。'
        )
        title = '正在扫描多个目标' if len(paths) > 1 else '正在扫描占用进程'
        self.placeholder.show_message(
            "\u21bb",
            title,
            detail,
            busy=True,
            tone="working",
        )
        self.result_stack.setCurrentWidget(self.placeholder)

        self._thread = QThread(self)
        self._worker = TargetScanWorker(
            [str(path) for path in paths],
            recursive=self.recursive_checkbox.isChecked(),
        )
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.completed.connect(self._on_scan_completed)
        self._worker.failed.connect(self._on_scan_failed)
        self._worker.cancelled.connect(self._on_scan_cancelled)
        self._worker.completed.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._worker.completed.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._worker.cancelled.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_scan_completed(self, result: object) -> None:
        if self._scan_cancel_requested:
            self._on_scan_cancelled()
            return
        if not isinstance(result, BatchScanResult):
            self._on_scan_failed("扫描返回了无效结果。")
            return
        self._show_batch_result(result)

    def _show_batch_result(self, result: BatchScanResult) -> None:
        self._scan_result = result
        self._result_title_source = '占用情况'
        self.result_title.setText(i18n.tr(self._result_title_source))
        self.rescan_button.setText(i18n.tr('重新扫描'))
        locks = list(result.locks)
        blocking_locks = [
            lock for lock in locks if lock.lock_state == LockState.BLOCKING
        ]
        unknown_locks = [
            lock for lock in locks if lock.lock_state == LockState.UNKNOWN
        ]
        non_blocking_locks = [
            lock for lock in locks if lock.lock_state == LockState.NON_BLOCKING
        ]
        admin_hint_parts = self._admin_permission_hint(
            locks,
            inaccessible_processes=result.inaccessible_processes,
            inaccessible_paths=result.inaccessible_paths,
        )
        inaccessible_paths_parts: tuple[tuple[str, dict[str, object]], ...] = (
            ((' · {count} 个文件夹因权限不足未检查', {"count": result.inaccessible_paths}),)
            if result.inaccessible_paths
            else ()
        )
        grouped: dict[str, list[ProcessInfo]] = {}
        seen_processes: dict[str, set[int]] = {}
        for lock in blocking_locks:
            key = self._path_key(lock.file_path)
            bucket = grouped.setdefault(key, [])
            seen = seen_processes.setdefault(key, set())
            if lock.process.pid not in seen:
                seen.add(lock.process.pid)
                bucket.append(lock.process)
        self._locks_by_path = {
            path: tuple(processes) for path, processes in grouped.items()
        }
        self._populate_result_table(locks)

        target_count = len(result.targets)
        blocking_pids = {lock.process.pid for lock in blocking_locks}
        blocking_paths = {
            self._path_key(lock.file_path) for lock in blocking_locks
        }
        unknown_pids = {lock.process.pid for lock in unknown_locks}
        non_blocking_pids = {lock.process.pid for lock in non_blocking_locks}
        self._update_action_buttons()

        if blocking_locks:
            self._set_status(
                '{count} 个文件存在访问锁定',
                "success",
                count=len(blocking_paths),
            )
            hint_parts: list[tuple[str, dict[str, object]]] = [
                (
                    '{target_count} 个目标 · {file_count} 个文件 · {process_count} 个候选进程 · {locked_count} 个存在锁定的文件',
                    {
                        "target_count": target_count,
                        "file_count": result.scanned_files,
                        "process_count": len(blocking_pids),
                        "locked_count": len(blocking_paths),
                    },
                )
            ]
            if unknown_pids:
                hint_parts.append(
                    (' · {count} 个进程无法确认', {"count": len(unknown_pids)})
                )
            if non_blocking_pids:
                hint_parts.append(
                    (
                        ' · {count} 个未发现锁定的候选进程',
                        {"count": len(non_blocking_pids)},
                    )
                )
            if (
                len(blocking_pids) == 1
                and process_risk(blocking_locks[0].process) == "self"
            ):
                hint_parts.append((' · 当前程序是唯一候选', {}))
            hint_parts.extend(inaccessible_paths_parts)
            hint_parts.extend(admin_hint_parts)
            self._set_result_hint_parts(tuple(hint_parts))
            self.result_stack.setCurrentWidget(self.table)
            return

        if unknown_locks:
            self._set_status('存在无法确认的目标状态', "idle")
            hint_parts = [
                (
                    '{target_count} 个目标 · {file_count} 个文件 · {process_count} 个进程需管理员确认',
                    {
                        "target_count": target_count,
                        "file_count": result.scanned_files,
                        "process_count": len(unknown_pids),
                    },
                )
            ]
            hint_parts.extend(inaccessible_paths_parts)
            hint_parts.extend(admin_hint_parts)
            self._set_result_hint_parts(tuple(hint_parts))
            self.result_stack.setCurrentWidget(self.table)
            return

        if non_blocking_locks:
            self._set_status('未发现文件访问锁定', "success")
            hint_parts = [
                (
                    '{target_count} 个目标 · {file_count} 个文件 · {process_count} 个未发现锁定的候选进程',
                    {
                        "target_count": target_count,
                        "file_count": result.scanned_files,
                        "process_count": len(non_blocking_pids),
                    },
                )
            ]
            hint_parts.extend(inaccessible_paths_parts)
            hint_parts.extend(admin_hint_parts)
            self._set_result_hint_parts(tuple(hint_parts))
            self.result_stack.setCurrentWidget(self.table)
            return

        self._set_status('未发现占用', "idle")
        hint_parts = [
            (
                '{target_count} 个目标 · {file_count} 个文件',
                {
                    "target_count": target_count,
                    "file_count": result.scanned_files,
                },
            )
        ]
        if result.inaccessible_processes:
            hint_parts.append(
                (
                    ' · {count} 个进程因权限不足无法检查',
                    {"count": result.inaccessible_processes},
                )
            )
        hint_parts.extend(inaccessible_paths_parts)
        hint_parts.extend(admin_hint_parts)
        self._set_result_hint_parts(tuple(hint_parts))
        detail_lines = [
            i18n.tr(
                '已检查 {target_count} 个目标中的 {file_count} 个文件，未发现可确认的占用情况。',
                target_count=target_count,
                file_count=result.scanned_files,
            )
        ]
        if result.scanned_files == 0:
            detail_lines = [i18n.tr('目标中没有可扫描的文件，或文件无法读取。')]
        if result.inaccessible_processes:
            detail_lines.append(
                i18n.tr(
                    '另有 {count} 个进程因权限不足无法检查。',
                    count=result.inaccessible_processes,
                )
            )
        if result.inaccessible_paths:
            detail_lines.append(
                i18n.tr(
                    '另有 {count} 个文件夹因权限不足未检查。',
                    count=result.inaccessible_paths,
                )
            )
        self.placeholder.show_message(
            "\u2713",
            '未发现被占用的文件',
            "\n".join(detail_lines),
            tone="success",
        )
        self.result_stack.setCurrentWidget(self.placeholder)

    def _on_scan_cancelled(self) -> None:
        self._set_status("扫描已取消", "idle")
        self._set_result_hint("已停止扫描")
        self.placeholder.show_message(
            "×",
            "扫描已取消",
            "本次扫描没有生成结果，可以点击“重新扫描”再次检查。",
        )
        self.result_stack.setCurrentWidget(self.placeholder)

    def _on_scan_failed(self, message: str) -> None:
        if self._scan_cancel_requested:
            self._on_scan_cancelled()
            return
        self._set_status("扫描失败", "error")
        self._set_result_hint("扫描失败")
        self.placeholder.show_message(
            "!",
            "扫描未能完成",
            message or i18n.tr("读取进程信息时发生未知错误。"),
            tone="error",
        )
        self.result_stack.setCurrentWidget(self.placeholder)

    def _on_thread_finished(self) -> None:
        self._worker = None
        self._thread = None
        self._scan_cancel_requested = False
        self._finish_scan_ui()

    def _finish_scan_ui(self) -> None:
        self._scan_in_progress = False
        self.cancel_scan_button.hide()
        self.cancel_scan_button.setText(i18n.tr("取消扫描"))
        self.drop_zone.setEnabled(True)
        self.table.setEnabled(True)
        self.rescan_button.setText(
            i18n.tr("重新扫描" if self._scan_result is not None else "开始扫描")
        )
        self.rescan_button.setEnabled(bool(self._target_paths))
        self.clear_targets_button.setVisible(bool(self._target_paths))
        self.clear_targets_button.setEnabled(bool(self._target_paths))
        self.recursive_checkbox.setEnabled(True)
        self.file_mode_button.setEnabled(True)
        self.folder_mode_button.setEnabled(True)
        self.language_combo.setEnabled(not self._termination_in_progress)
        self._update_action_buttons()

    def _populate_result_table(self, locks: list[FileLockInfo]) -> None:
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(locks))

        show_file_column = len(self._target_paths) != 1 or any(
            path.is_dir() for path in self._target_paths
        )
        self.table.setColumnHidden(0, not show_file_column)

        for row, lock in enumerate(locks):
            display_path = self._display_relative_path(lock.file_path)
            file_item = QTableWidgetItem(display_path)
            file_item.setToolTip(lock.file_path)
            file_item.setData(Qt.ItemDataRole.UserRole, lock.file_path)
            self.table.setItem(row, 0, file_item)
            self._set_process_cells(
                row,
                lock.process,
                file_path=lock.file_path,
                start_column=1,
                handles=lock.handles,
                handle_identities=lock.handle_identities,
                sources=lock.sources,
                lock_state=lock.lock_state,
                lock_detail=lock.lock_detail,
                lock_detail_source=lock.lock_detail_source,
                lock_detail_values=lock.lock_detail_values,
            )

        self.table.setSortingEnabled(True)

    def _set_process_cells(
        self,
        row: int,
        process: ProcessInfo,
        *,
        file_path: str,
        start_column: int,
        handles: tuple[int, ...] = (),
        handle_identities: tuple[HandleIdentity, ...] = (),
        sources: tuple[CandidateSource, ...] = (),
        lock_state: LockState = LockState.UNKNOWN,
        lock_detail: str = "",
        lock_detail_source: str = "",
        lock_detail_values: tuple[tuple[str, object], ...] = (),
    ) -> None:
        translated_lock_detail = (
            i18n.tr(lock_detail_source, **dict(lock_detail_values))
            if lock_detail_source
            else lock_detail
        )
        name_item = QTableWidgetItem(process.name)
        name_item.setToolTip(process.name)

        pid_item = NumericItem(str(process.pid))
        pid_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        handle_item = QTableWidgetItem(
            i18n.tr("{count} 个", count=len(handles))
            if handles
            else i18n.tr("未取得")
        )
        handle_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        source_set = set(sources)
        handle_item.setToolTip(
            "\n".join(f"HANDLE 0x{value:X}" for value in handles)
            if handles
            else (
                i18n.tr(
                    "本次未取得该进程对应目标的原始 File 句柄，"
                    "不代表进程没有句柄。"
                )
                + (
                    i18n.tr(
                        "\n该候选仅来自 Restart Manager，提升到管理员权限后"
                        "重新扫描可能读取更多句柄。"
                    )
                    if source_set == {CandidateSource.RESTART_MANAGER}
                    else ""
                )
            )
        )

        source_labels = {
            CandidateSource.SYSTEM_HANDLE: i18n.tr("系统句柄"),
            CandidateSource.RESTART_MANAGER: "Restart Manager",
            CandidateSource.PROCESS_RESOURCES: i18n.tr("进程资源"),
        }
        source_details = {
            CandidateSource.SYSTEM_HANDLE: i18n.tr(
                "系统 File 句柄表匹配到该目标路径。"
            ),
            CandidateSource.RESTART_MANAGER: i18n.tr(
                "Restart Manager 报告该进程使用此文件。"
            ),
            CandidateSource.PROCESS_RESOURCES: i18n.tr(
                "通过 psutil 的打开文件或内存映射信息发现。"
            ),
        }
        ordered_sources = [
            source
            for source in CandidateSource
            if source in source_set
        ]
        source_item = QTableWidgetItem(
            " + ".join(source_labels[source] for source in ordered_sources)
            if ordered_sources
            else "—"
        )
        source_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        source_tooltip = (
            i18n.tr("候选来源：\n")
            + "\n".join(f"· {source_details[source]}" for source in ordered_sources)
            + "\n\n"
            if ordered_sources
            else i18n.tr("未记录候选来源。\n\n")
        )
        source_item.setToolTip(
            source_tooltip
            + i18n.tr("该进程只是扫描中发现的候选，不代表它一定是锁定者。")
        )

        state_labels = {
            LockState.BLOCKING: i18n.tr("存在锁定"),
            LockState.NON_BLOCKING: i18n.tr("未发现锁定"),
            LockState.UNKNOWN: i18n.tr("无法确认"),
        }
        state_item = QTableWidgetItem(state_labels[lock_state])
        state_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        state_item.setToolTip(
            i18n.tr("目标级判断：")
            + (
                translated_lock_detail
                or i18n.tr(
                    {
                        LockState.BLOCKING: (
                            "目标当前无法获取 DELETE 权限，可能存在共享锁、"
                            "字节范围锁或内存映射冲突。"
                        ),
                        LockState.NON_BLOCKING: (
                            "目标当前可以获取 DELETE 权限，"
                            "未通过本次探测发现访问锁定。"
                        ),
                        LockState.UNKNOWN: "权限或路径状态导致无法确认。",
                    }[lock_state]
                )
            )
            + i18n.tr(
                " 该状态针对目标路径，不针对单个进程；"
                "最终操作结果以 Windows 返回的错误为准。"
            )
        )

        path_item = QTableWidgetItem(process.exe or "—")
        path_item.setToolTip(process.exe or i18n.tr("无法读取程序路径"))

        user_item = QTableWidgetItem(process.username or "—")
        user_item.setToolTip(process.username or i18n.tr("无法读取运行账户"))

        self.table.setItem(row, start_column, name_item)
        self.table.setItem(row, start_column + 1, pid_item)
        self.table.setItem(row, start_column + 2, handle_item)
        self.table.setItem(row, start_column + 3, source_item)
        self.table.setItem(row, start_column + 4, state_item)
        self.table.setItem(row, start_column + 5, path_item)
        self.table.setItem(row, start_column + 6, user_item)

        action_holder = QWidget()
        action_layout = QHBoxLayout(action_holder)
        action_layout.setContentsMargins(4, 5, 4, 5)
        action_layout.setSpacing(6)

        risk = process_risk(process)
        identities_by_handle = {
            identity.handle_value: identity
            for identity in handle_identities
        }
        release_identity = next(
            (
                identities_by_handle[handle_value]
                for handle_value in handles
                if handle_value in identities_by_handle
            ),
            None,
        )
        if lock_state != LockState.BLOCKING:
            status_button = QPushButton(
                i18n.tr(
                    "无需处理"
                    if lock_state == LockState.NON_BLOCKING
                    else "需管理员确认"
                )
            )
            status_button.setObjectName("secondaryButton")
            status_button.setEnabled(False)
            status_button.setMinimumWidth(100)
            status_button.setToolTip(
                translated_lock_detail
                or i18n.tr(
                    "目标级探测未发现锁定，当前无需结束此候选进程。"
                    if lock_state == LockState.NON_BLOCKING
                    else "权限不足或目标状态异常，请尝试以管理员权限重新扫描。"
                )
            )
            action_layout.addWidget(status_button)
        elif risk in {"self", "critical"}:
            protected_button = QPushButton(
                i18n.tr("当前程序" if risk == "self" else "系统保护")
            )
            protected_button.setObjectName("dangerButton")
            protected_button.setProperty("risk", risk)
            protected_button.setEnabled(False)
            protected_button.setMinimumWidth(92)
            protected_button.setToolTip(
                i18n.tr("这是正在运行 UnlockGo 的进程，禁止结束自身。")
                if risk == "self"
                else i18n.tr("这是 Windows 关键进程，禁止结束。")
            )
            action_layout.addWidget(protected_button)
        else:
            terminate_button = QPushButton(i18n.tr("结束进程"))
            terminate_button.setObjectName("dangerButton")
            terminate_button.setProperty("risk", risk)
            terminate_button.setCursor(Qt.CursorShape.PointingHandCursor)
            terminate_button.setMinimumWidth(72)
            terminate_button.clicked.connect(
                partial(self._request_terminate_process, process)
            )
            terminate_button.setToolTip(
                i18n.tr("高风险进程，结束前会再次确认。")
                if risk == "high"
                else i18n.tr("仅结束当前选中的进程。")
            )
            action_layout.addWidget(terminate_button)

            more_button = QPushButton(i18n.tr("更多操作"))
            more_button.setObjectName("moreButton")
            more_button.setCursor(Qt.CursorShape.PointingHandCursor)
            more_button.setMinimumWidth(76)
            more_button.setMaximumWidth(76)
            more_button.setToolTip(i18n.tr("查看其他文件处理方式"))

            more_menu = QMenu(more_button)
            more_menu.setMinimumWidth(184)
            more_menu.setToolTipsVisible(True)
            release_action = more_menu.addAction(i18n.tr("释放文件句柄"))
            release_action.setEnabled(release_identity is not None)
            release_action.setToolTip(
                i18n.tr(
                    "仅释放该进程持有的一个目标 File 句柄："
                    "HANDLE 0x{handle}{count_note}。"
                    "释放前会校验卷序列号和 File ID，不会结束进程。",
                    handle=f"{release_identity.handle_value:X}",
                    count_note=(
                        i18n.tr(
                            "（当前共识别 {count} 个，本次只处理一个）",
                            count=len(handles),
                        )
                        if len(handles) > 1
                        else ""
                    ),
                )
                if release_identity is not None
                else i18n.tr(
                    "本次扫描未取得可验证的 File 句柄身份，"
                    "为避免误关其他文件，已禁用此操作。"
                    "请以管理员身份重新扫描。"
                )
            )
            if release_identity is not None:
                release_action.triggered.connect(
                    partial(
                        self._request_release_handle,
                        process,
                        file_path,
                        release_identity,
                    )
                )
            more_menu.addSeparator()
            recycle_action = more_menu.addAction(i18n.tr("结束并删除"))
            recycle_action.setToolTip(
                i18n.tr("结束占用进程，并将当前目标移入回收站")
            )
            recycle_action.triggered.connect(
                partial(self._request_recycle, file_path, process)
            )
            more_menu.addSeparator()
            terminate_all_action = more_menu.addAction(i18n.tr("结束全部关联进程"))
            terminate_all_action.setToolTip(
                i18n.tr("结束当前目标关联的全部可处理进程")
            )
            terminate_all_action.triggered.connect(
                partial(self._request_terminate_all, file_path)
            )
            more_button.setMenu(more_menu)
            action_layout.addWidget(more_button)

        self.table.setCellWidget(row, start_column + 7, action_holder)

    @staticmethod
    def _path_key(file_path: str | Path) -> str:
        path = Path(file_path)
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            resolved = path.absolute()
        return os.path.normcase(os.path.normpath(str(resolved)))

    def _display_relative_path(self, file_path: str) -> str:
        path = Path(file_path)
        for target in self._target_paths:
            if target.is_file():
                if self._path_key(target) == self._path_key(path):
                    return target.name
                continue
            try:
                relative = path.resolve(strict=False).relative_to(
                    target.resolve(strict=False)
                )
            except (OSError, ValueError):
                continue
            label = str(relative)
            return label if len(self._target_paths) == 1 else f"{target.name}/{label}"
        return file_path

    def _update_action_buttons(self) -> None:
        busy = self._scan_in_progress or self._termination_in_progress
        self.admin_restart_button.setEnabled(not busy)
        target_count = len(self._target_paths)
        self.target_list_button.setText(
            i18n.tr("目标列表 ({count})", count=target_count)
            if target_count
            else i18n.tr("目标列表")
        )
        self.target_list_button.setEnabled(bool(target_count) and not busy)
        enabled = bool(self._target_paths) and self._scan_result is not None and not busy
        self.rename_button.setEnabled(enabled)
        self.move_button.setEnabled(enabled)
        self.copy_button.setEnabled(enabled)
        blocking_locks = (
            [
                lock
                for lock in self._scan_result.locks
                if lock.lock_state == LockState.BLOCKING
            ]
            if self._scan_result is not None
            else []
        )
        self.batch_terminate_button.setEnabled(
            enabled and bool(blocking_locks)
        )

    def _set_status(self, source: str, state: str, **values: object) -> None:
        self._status_source = (source, dict(values))
        self.status_chip.setText(i18n.tr(source, **values))
        self.status_chip.setProperty("state", state)
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)

    def _set_result_hint(self, source: str, **values: object) -> None:
        self._set_result_hint_parts(((source, dict(values)),))

    def _set_result_hint_parts(
        self,
        parts: tuple[tuple[str, dict[str, object]], ...],
    ) -> None:
        self._result_hint_parts = tuple(
            (source, dict(values)) for source, values in parts
        )
        self.result_hint.setText(
            "".join(
                i18n.tr(source, **values)
                for source, values in self._result_hint_parts
            )
        )

    def _admin_permission_hint(
        self,
        locks: list[FileLockInfo],
        *,
        inaccessible_processes: int = 0,
        inaccessible_paths: int = 0,
    ) -> tuple[tuple[str, dict[str, object]], ...]:
        if self._running_as_admin:
            return ()
        needs_recheck = (
            inaccessible_processes > 0
            or inaccessible_paths > 0
            or any(
                (
                    lock.lock_state == LockState.UNKNOWN
                    or not lock.sources
                    or CandidateSource.SYSTEM_HANDLE not in lock.sources
                )
                for lock in locks
            )
        )
        if not needs_recheck:
            return ()
        return ((' · 当前权限可能不足，可用右下角“以管理员身份重启”进一步确认', {}),)

    def _is_valid_drop_path(self, path: Path) -> bool:
        return path.is_file() or path.is_dir()

    def _valid_drop_paths_from_event(
        self,
        event: QDragEnterEvent | QDropEvent,
    ) -> list[str]:
        paths: list[str] = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                return []
            path = Path(url.toLocalFile())
            if not self._is_valid_drop_path(path):
                return []
            paths.append(str(path))
        return paths

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        paths = self._valid_drop_paths_from_event(event)
        if (
            not self._running_as_admin
            and not self._scan_in_progress
            and not self._termination_in_progress
            and paths
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self._valid_drop_paths_from_event(event)
        if (
            not self._running_as_admin
            and not self._scan_in_progress
            and not self._termination_in_progress
            and paths
        ):
            self._select_dropped_paths(paths)
            event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent) -> None:
        for thread in (self._thread, self._terminate_thread):
            if thread is None or not thread.isRunning():
                continue
            thread.requestInterruption()
            thread.quit()
            if not thread.wait(10000):
                event.ignore()
                return
        super().closeEvent(event)

    def _apply_styles(self) -> None:
        self.setStyleSheet(STYLESHEET)
