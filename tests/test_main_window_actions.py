from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from app import APP_DISPLAY_NAME, i18n
from app.main_window import MainWindow
from app.models import (
    BatchScanResult,
    CandidateSource,
    FileIdentity,
    FileLockInfo,
    HandleIdentity,
    LockState,
    ProcessInfo,
)
from app.windows_handle_release import HandleReleaseResult
from app.windows_lock_probe import LockProbeResult
from app.widgets import TargetListDialog
from app.workers import FileOperationWorker

FILE_IDENTITY = FileIdentity(
    volume_serial=0x12345678,
    file_index_high=0x11223344,
    file_index_low=0x55667788,
)
HANDLE_IDENTITY = HandleIdentity(
    handle_value=0x120,
    file_identity=FILE_IDENTITY,
)


class MainWindowActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        i18n.set_language("zh_CN")
        self._save_language_patch = patch("app.main_window.i18n.save_language")
        self.save_language_mock = self._save_language_patch.start()
        self.window = MainWindow()
        self.window.table.setRowCount(1)

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self._save_language_patch.stop()
        i18n.set_language("zh_CN")

    def _buttons(self) -> list[QPushButton]:
        holder = self.window.table.cellWidget(0, 8)
        self.assertIsNotNone(holder)
        return holder.findChildren(QPushButton)

    def _switch_language(self, language: str) -> None:
        index = self.window.language_combo.findData(language)
        self.assertGreaterEqual(index, 0)
        self.window.language_combo.setCurrentIndex(index)
        self.app.processEvents()

    def test_selecting_paths_accumulates_without_starting_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.txt"
            second = Path(temp_dir) / "second.txt"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")

            with patch.object(self.window, "_begin_scan") as scan_mock:
                self.window._select_paths([str(first)])
                self.window._select_paths([str(second)])

        scan_mock.assert_not_called()
        self.assertEqual(self.window._target_paths, (first.resolve(), second.resolve()))
        self.assertEqual(self.window.rescan_button.text(), "开始扫描")
        self.assertTrue(self.window.rescan_button.isEnabled())
        self.assertFalse(self.window.clear_targets_button.isHidden())
        self.assertTrue(self.window.clear_targets_button.isEnabled())
        self.assertEqual(self.window.file_mode_button.text(), "文件")

    def test_start_scan_button_scans_all_staged_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.txt"
            second = Path(temp_dir) / "second.txt"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            self.window._select_paths([str(first), str(second)])

            with patch.object(self.window, "_begin_scan") as scan_mock:
                self.window._rescan()

        scan_mock.assert_called_once_with((first.resolve(), second.resolve()))

    def test_clear_targets_resets_staged_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")
            self.window._select_paths([str(target)])
            self.window._request_clear_targets()

        self.assertEqual(self.window._target_paths, ())
        self.assertFalse(self.window.rescan_button.isEnabled())
        self.assertTrue(self.window.clear_targets_button.isHidden())

    def test_normal_process_has_terminate_and_recycle_actions(self) -> None:
        process = ProcessInfo(
            pid=4321,
            name="sample.exe",
            exe=r"C:\Apps\sample.exe",
            username="tester",
            create_time=123.0,
        )

        self.window._set_process_cells(
            0,
            process,
            file_path=r"C:\Temp\target.txt",
            start_column=1,
            handles=(0x120,),
            handle_identities=(HANDLE_IDENTITY,),
            lock_state=LockState.BLOCKING,
        )

        buttons = self._buttons()
        self.assertEqual(
            [button.text() for button in buttons],
            ["结束进程", "更多操作"],
        )
        self.assertTrue(all(button.isEnabled() for button in buttons))
        menu = buttons[1].menu()
        actions = [action for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(
            [action.text() for action in actions],
            ["释放文件句柄", "结束并删除", "结束全部关联进程"],
        )
        self.assertTrue(actions[0].isEnabled())
        self.assertGreaterEqual(menu.minimumWidth(), 184)
        self.assertTrue(menu.toolTipsVisible())

    def test_more_menu_dispatches_secondary_actions(self) -> None:
        process = ProcessInfo(
            pid=4321,
            name="sample.exe",
            exe=r"C:\Apps\sample.exe",
            username="tester",
            create_time=123.0,
        )
        with (
            patch.object(self.window, "_request_release_handle") as release_mock,
            patch.object(self.window, "_request_recycle") as recycle_mock,
            patch.object(self.window, "_request_terminate_all") as all_mock,
        ):
            self.window._set_process_cells(
                0,
                process,
                file_path=r"C:\Temp\target.txt",
                start_column=1,
                handles=(0x120,),
                handle_identities=(HANDLE_IDENTITY,),
                lock_state=LockState.BLOCKING,
            )
            actions = [
                action
                for action in self._buttons()[1].menu().actions()
                if not action.isSeparator()
            ]
            actions[0].trigger()
            actions[1].trigger()
            actions[2].trigger()

        release_mock.assert_called_once_with(
            process,
            r"C:\Temp\target.txt",
            HANDLE_IDENTITY,
        )
        recycle_mock.assert_called_once_with(r"C:\Temp\target.txt", process)
        all_mock.assert_called_once_with(r"C:\Temp\target.txt")

    def test_release_handle_action_is_disabled_without_visible_handle(self) -> None:
        process = ProcessInfo(
            pid=4321,
            name="sample.exe",
            exe=r"C:\Apps\sample.exe",
            username="tester",
            create_time=123.0,
        )

        self.window._set_process_cells(
            0,
            process,
            file_path=r"C:\Temp\target.txt",
            start_column=1,
            lock_state=LockState.BLOCKING,
        )

        actions = [
            action
            for action in self._buttons()[1].menu().actions()
            if not action.isSeparator()
        ]
        self.assertEqual(actions[0].text(), "释放文件句柄")
        self.assertFalse(actions[0].isEnabled())
        self.assertIn("管理员身份", actions[0].toolTip())

    def test_release_handle_action_is_disabled_without_file_identity(self) -> None:
        process = ProcessInfo(
            pid=4321,
            name="sample.exe",
            exe=r"C:\Apps\sample.exe",
            username="tester",
            create_time=123.0,
        )

        self.window._set_process_cells(
            0,
            process,
            file_path=r"C:\Temp\target.txt",
            start_column=1,
            handles=(0x120,),
            lock_state=LockState.BLOCKING,
        )

        actions = [
            action
            for action in self._buttons()[1].menu().actions()
            if not action.isSeparator()
        ]
        self.assertFalse(actions[0].isEnabled())
        self.assertIn("File 句柄身份", actions[0].toolTip())

    def test_release_handle_requires_confirmation(self) -> None:
        process = ProcessInfo(
            pid=4321,
            name="sample.exe",
            exe=r"C:\Apps\sample.exe",
            username="tester",
            create_time=123.0,
        )

        with (
            patch(
                "app.main_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ) as question_mock,
            patch.object(self.window, "_begin_release_handle") as begin_mock,
        ):
            self.window._request_release_handle(
                process,
                r"C:\Temp\target.txt",
                HANDLE_IDENTITY,
            )

        self.assertEqual(question_mock.call_args.args[1], "释放文件句柄？")
        self.assertIn("不会结束进程", question_mock.call_args.args[2])
        begin_mock.assert_called_once_with(
            process,
            r"C:\Temp\target.txt",
            HANDLE_IDENTITY,
            allow_high_risk=False,
        )

    def test_high_risk_handle_release_requires_explicit_opt_in(self) -> None:
        process = ProcessInfo(
            pid=4321,
            name="explorer.exe",
            exe=r"C:\Windows\explorer.exe",
            username="tester",
            create_time=123.0,
        )

        with (
            patch(
                "app.main_window.QMessageBox.warning",
                return_value=QMessageBox.StandardButton.Yes,
            ) as warning_mock,
            patch.object(self.window, "_begin_release_handle") as begin_mock,
        ):
            self.window._request_release_handle(
                process,
                r"C:\Temp\target.txt",
                HANDLE_IDENTITY,
            )

        self.assertEqual(warning_mock.call_args.args[1], "释放高风险进程句柄？")
        begin_mock.assert_called_once_with(
            process,
            r"C:\Temp\target.txt",
            HANDLE_IDENTITY,
            allow_high_risk=True,
        )

    def test_release_handle_completion_requests_rescan(self) -> None:
        process = ProcessInfo(
            pid=4321,
            name="sample.exe",
            exe=r"C:\Apps\sample.exe",
            username="tester",
            create_time=123.0,
        )
        result = HandleReleaseResult(
            process=process,
            handle_value=0x120,
            target_path=r"C:\Temp\target.txt",
            lock_probe=LockProbeResult(
                state=LockState.NON_BLOCKING,
                detail="目标句柄已关闭，目标当前可获取 DELETE 权限。",
            ),
        )

        self.window._on_release_handle_completed(result)

        self.assertTrue(self.window._rescan_after_termination)
        self.assertEqual(self.window.status_chip.text(), "句柄已释放")
        self.assertIn("目标已解锁", self.window.result_hint.text())

    def test_file_handles_are_shown_in_dedicated_column(self) -> None:
        process = ProcessInfo(
            pid=4321,
            name="sample.exe",
            exe=r"C:\Apps\sample.exe",
            username="tester",
            create_time=123.0,
        )

        self.window._set_process_cells(
            0,
            process,
            file_path=r"C:\Temp\target.txt",
            start_column=1,
            handles=(0x120, 0x8A4),
            lock_state=LockState.BLOCKING,
        )

        handle_item = self.window.table.item(0, 3)
        self.assertIsNotNone(handle_item)
        self.assertEqual(handle_item.text(), "2 个")
        self.assertEqual(handle_item.toolTip(), "HANDLE 0x120\nHANDLE 0x8A4")

    def test_non_blocking_handle_is_shown_without_termination_actions(self) -> None:
        process = ProcessInfo(
            pid=4321,
            name="sample.exe",
            exe=r"C:\Apps\sample.exe",
            username="tester",
            create_time=123.0,
        )

        self.window._set_process_cells(
            0,
            process,
            file_path=r"C:\Temp\target.txt",
            start_column=1,
            handles=(0x120,),
            sources=(CandidateSource.SYSTEM_HANDLE,),
            lock_state=LockState.NON_BLOCKING,
            lock_detail="目标可以获取 DELETE 权限。",
        )

        source_item = self.window.table.item(0, 4)
        self.assertIsNotNone(source_item)
        self.assertEqual(source_item.text(), "系统句柄")
        self.assertIn("不代表它一定是锁定者", source_item.toolTip())
        state_item = self.window.table.item(0, 5)
        self.assertIsNotNone(state_item)
        self.assertEqual(state_item.text(), "未发现锁定")
        self.assertIn("目标级判断：目标可以获取 DELETE 权限。", state_item.toolTip())
        self.assertIn("不针对单个进程", state_item.toolTip())
        self.assertEqual(self.window.table.item(0, 6).text(), r"C:\Apps\sample.exe")
        self.assertEqual(self.window.table.item(0, 7).text(), "tester")
        buttons = self._buttons()
        self.assertEqual([button.text() for button in buttons], ["无需处理"])
        self.assertFalse(buttons[0].isEnabled())

    def test_candidate_sources_are_merged_for_display(self) -> None:
        process = ProcessInfo(
            pid=4321,
            name="sample.exe",
            exe=r"C:\Apps\sample.exe",
            username="tester",
            create_time=123.0,
        )

        self.window._set_process_cells(
            0,
            process,
            file_path=r"C:\Temp\target.txt",
            start_column=1,
            sources=(
                CandidateSource.RESTART_MANAGER,
                CandidateSource.SYSTEM_HANDLE,
            ),
            lock_state=LockState.BLOCKING,
        )

        self.assertEqual(
            self.window.table.item(0, 4).text(),
            "系统句柄 + Restart Manager",
        )
        self.assertEqual(self.window.table.item(0, 5).text(), "存在锁定")
        self.assertEqual(self.window.table.item(0, 7).text(), "tester")
        self.assertIsNotNone(self.window.table.cellWidget(0, 8))

    def test_restart_manager_only_candidate_suggests_admin_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")
            process = ProcessInfo(4321, "sample.exe", "", "", 123.0)
            self.window._running_as_admin = False
            self.window._target_paths = (target,)

            self.window._show_batch_result(
                BatchScanResult(
                    targets=(),
                    locks=(
                        FileLockInfo(
                            str(target),
                            process,
                            sources=(CandidateSource.RESTART_MANAGER,),
                            lock_state=LockState.BLOCKING,
                        ),
                    ),
                    scanned_files=1,
                )
            )

        self.assertIn("管理员身份重启", self.window.result_hint.text())
        handle_item = self.window.table.item(0, 3)
        self.assertEqual(handle_item.text(), "未取得")
        self.assertIn("不代表进程没有句柄", handle_item.toolTip())
        self.assertIn("管理员权限", handle_item.toolTip())

    def test_visible_system_handle_does_not_suggest_admin_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")
            process = ProcessInfo(4321, "sample.exe", "", "", 123.0)
            self.window._running_as_admin = False
            self.window._target_paths = (target,)

            self.window._show_batch_result(
                BatchScanResult(
                    targets=(),
                    locks=(
                        FileLockInfo(
                            str(target),
                            process,
                            handles=(0x120,),
                            sources=(CandidateSource.SYSTEM_HANDLE,),
                            lock_state=LockState.BLOCKING,
                        ),
                    ),
                    scanned_files=1,
                )
            )

        self.assertNotIn("管理员身份重启", self.window.result_hint.text())

    def test_action_column_fits_without_horizontal_scroll(self) -> None:
        self.window.resize(1500, 900)
        self.window.show()
        self.window.result_stack.setCurrentWidget(self.window.table)
        self.window.table.setColumnHidden(0, False)
        process = ProcessInfo(
            4321,
            "sample.exe",
            r"C:\A\Very\Long\Program\Path\sample.exe",
            "LAPTOP\\tester",
            123.0,
        )
        self.window._set_process_cells(
            0,
            process,
            file_path=r"C:\Temp\target.txt",
            start_column=1,
            handles=(0x120,),
            sources=(
                CandidateSource.SYSTEM_HANDLE,
                CandidateSource.RESTART_MANAGER,
            ),
            lock_state=LockState.BLOCKING,
        )
        self.app.processEvents()

        self.assertEqual(self.window.table.columnWidth(1), 130)
        self.assertEqual(self.window.table.columnWidth(2), 64)
        self.assertEqual(self.window.table.columnWidth(7), 215)
        self.assertEqual(self.window.table.columnWidth(8), 168)
        self.assertEqual(self.window.table.horizontalScrollBar().maximum(), 0)

    def test_single_file_result_fits_default_window_without_horizontal_scroll(
        self,
    ) -> None:
        self.window.resize(1080, 760)
        self.window.show()
        self.window.result_stack.setCurrentWidget(self.window.table)
        process = ProcessInfo(
            28788,
            "powershell.exe",
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            r"LAPTOP-XULIXIANG\tester",
            123.0,
        )
        self.window._set_process_cells(
            0,
            process,
            file_path=r"C:\Users\xulixiang\Desktop\test.txt",
            start_column=1,
            sources=(
                CandidateSource.SYSTEM_HANDLE,
                CandidateSource.RESTART_MANAGER,
            ),
            lock_state=LockState.BLOCKING,
        )
        self.window.table.setColumnHidden(0, True)
        self.app.processEvents()

        self.assertEqual(self.window.table.horizontalScrollBar().maximum(), 0)

    def test_content_is_centered_and_width_limited_on_wide_windows(self) -> None:
        self.window.resize(1920, 900)
        self.window.show()
        self.app.processEvents()

        root = self.window.centralWidget()
        self.assertLessEqual(self.window.content.width(), 1500)
        self.assertLessEqual(
            abs(
                self.window.content.geometry().center().x()
                - root.rect().center().x()
            ),
            2,
        )

    def test_unknown_lock_state_requires_manual_confirmation(self) -> None:
        process = ProcessInfo(
            pid=4321,
            name="sample.exe",
            exe=r"C:\Apps\sample.exe",
            username="tester",
            create_time=123.0,
        )

        self.window._set_process_cells(
            0,
            process,
            file_path=r"C:\Temp\target.txt",
            start_column=1,
            lock_state=LockState.UNKNOWN,
            lock_detail="当前权限不足。",
        )

        buttons = self._buttons()
        self.assertEqual([button.text() for button in buttons], ["需管理员确认"])
        self.assertFalse(buttons[0].isEnabled())

    def test_critical_process_has_only_disabled_protection_action(self) -> None:
        process = ProcessInfo(
            pid=1,
            name="System",
            exe="",
            username="",
            create_time=123.0,
        )

        self.window._set_process_cells(
            0,
            process,
            file_path=r"C:\Temp\target.txt",
            start_column=1,
            lock_state=LockState.BLOCKING,
        )

        buttons = self._buttons()
        self.assertEqual([button.text() for button in buttons], ["系统保护"])
        self.assertFalse(buttons[0].isEnabled())

    def test_global_terminate_all_refuses_batch_with_protected_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("test", encoding="utf-8")
            normal = ProcessInfo(4321, "sample.exe", "", "", 123.0)
            critical = ProcessInfo(1, "System", "", "", 100.0)
            self.window._target_paths = (target,)
            self.window._scan_result = BatchScanResult(
                targets=(),
                locks=(
                    FileLockInfo(
                        str(target),
                        normal,
                        lock_state=LockState.BLOCKING,
                    ),
                    FileLockInfo(
                        str(target),
                        critical,
                        lock_state=LockState.BLOCKING,
                    ),
                ),
                scanned_files=1,
            )

            with (
                patch("app.main_window.QMessageBox.warning") as warning_mock,
                patch.object(self.window, "_begin_terminate_all") as begin_mock,
            ):
                self.window._request_terminate_all_targets()

        begin_mock.assert_not_called()
        self.assertIn("System", warning_mock.call_args.args[2])
        self.assertIn("未结束任何进程", warning_mock.call_args.args[2])

    def test_global_terminate_all_deduplicates_processes(self) -> None:
        first = ProcessInfo(4321, "first.exe", "", "", 123.0)
        second = ProcessInfo(4322, "second.exe", "", "", 124.0)
        self.window._target_paths = (Path(r"C:\Temp\a.txt"), Path(r"C:\Temp\b.txt"))
        self.window._scan_result = BatchScanResult(
            targets=(),
            locks=(
                FileLockInfo(
                    r"C:\Temp\a.txt",
                    first,
                    lock_state=LockState.BLOCKING,
                ),
                FileLockInfo(
                    r"C:\Temp\a.txt",
                    first,
                    lock_state=LockState.BLOCKING,
                ),
                FileLockInfo(
                    r"C:\Temp\b.txt",
                    second,
                    lock_state=LockState.BLOCKING,
                ),
            ),
            scanned_files=2,
        )

        with (
            patch(
                "app.main_window.QMessageBox.warning",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch.object(self.window, "_begin_terminate_all") as begin_mock,
        ):
            self.window._request_terminate_all_targets()

        begin_mock.assert_called_once_with((first, second))

    def test_global_terminate_all_ignores_non_blocking_and_unknown_handles(self) -> None:
        blocking = ProcessInfo(4321, "blocking.exe", "", "", 123.0)
        non_blocking = ProcessInfo(4322, "reader.exe", "", "", 124.0)
        unknown = ProcessInfo(4323, "unknown.exe", "", "", 125.0)
        self.window._target_paths = (Path(r"C:\Temp\a.txt"),)
        self.window._scan_result = BatchScanResult(
            targets=(),
            locks=(
                FileLockInfo(
                    r"C:\Temp\a.txt",
                    blocking,
                    lock_state=LockState.BLOCKING,
                ),
                FileLockInfo(
                    r"C:\Temp\a.txt",
                    non_blocking,
                    lock_state=LockState.NON_BLOCKING,
                ),
                FileLockInfo(
                    r"C:\Temp\a.txt",
                    unknown,
                    lock_state=LockState.UNKNOWN,
                ),
            ),
            scanned_files=1,
        )

        with (
            patch(
                "app.main_window.QMessageBox.warning",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch.object(self.window, "_begin_terminate_all") as begin_mock,
        ):
            self.window._request_terminate_all_targets()

        begin_mock.assert_called_once_with((blocking,))

    def test_batch_terminate_button_requires_blocking_handles(self) -> None:
        self.window._target_paths = (Path(r"C:\Temp\a.txt"),)
        process = ProcessInfo(4321, "reader.exe", "", "", 123.0)
        self.window._scan_result = BatchScanResult(
            targets=(),
            locks=(
                FileLockInfo(
                    r"C:\Temp\a.txt",
                    process,
                    lock_state=LockState.NON_BLOCKING,
                ),
            ),
            scanned_files=1,
        )

        self.window._update_action_buttons()
        self.assertFalse(self.window.batch_terminate_button.isEnabled())

        self.window._scan_result = BatchScanResult(
            targets=(),
            locks=(
                FileLockInfo(
                    r"C:\Temp\a.txt",
                    process,
                    lock_state=LockState.BLOCKING,
                ),
            ),
            scanned_files=1,
        )
        self.window._update_action_buttons()
        self.assertTrue(self.window.batch_terminate_button.isEnabled())

    def test_recycle_completed_removes_only_deleted_batch_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.txt"
            second = Path(temp_dir) / "second.txt"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            self.window._target_paths = (first, second)
            self.window._scan_result = BatchScanResult(
                targets=(), locks=(), scanned_files=2
            )

            self.window._on_recycle_completed(None, str(first))

        self.assertEqual(self.window._target_paths, (second,))
        self.assertEqual(self.window._selected_path, second)
        self.assertTrue(self.window._rescan_after_termination)


    def test_known_other_holder_blocks_batch_termination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("test", encoding="utf-8")
            selected = ProcessInfo(4321, "selected.exe", "", "", 123.0)
            other = ProcessInfo(4322, "other.exe", "", "", 124.0)
            self.window._selected_path = target
            self.window._locks_by_path = {
                self.window._path_key(target): (selected, other)
            }

            with (
                patch("app.main_window.QMessageBox.warning") as warning_mock,
                patch.object(self.window, "_begin_recycle") as begin_mock,
            ):
                self.window._request_recycle(str(target), selected)

        begin_mock.assert_not_called()
        self.assertEqual(warning_mock.call_args.args[1], "仍有其他占用者")
        self.assertIn("other.exe", warning_mock.call_args.args[2])


    def test_batch_terminate_button_has_readable_label(self) -> None:
        self.assertEqual(
            self.window.batch_terminate_button.text(),
            "一键结束全部关联进程",
        )

    def test_admin_restart_passes_targets_and_closes_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target with space.txt"
            target.write_text("target", encoding="utf-8")
            self.window._select_paths([str(target)])

            with (
                patch(
                    "app.main_window.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
                patch(
                    "app.main_window.request_admin_relaunch",
                    return_value=True,
                ) as relaunch_mock,
                patch.object(self.window, "close") as close_mock,
            ):
                self.window._request_admin_restart()

        relaunch_mock.assert_called_once_with(
            ["--target", str(target.resolve())]
        )
        close_mock.assert_called_once_with()

    def test_admin_restart_failure_keeps_current_window(self) -> None:
        with (
            patch(
                "app.main_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch(
                "app.main_window.request_admin_relaunch",
                return_value=False,
            ),
            patch("app.main_window.QMessageBox.warning") as warning_mock,
            patch.object(self.window, "close") as close_mock,
        ):
            self.window._request_admin_restart()

        close_mock.assert_not_called()
        warning_mock.assert_called_once()
        self.assertTrue(self.window.admin_restart_button.isEnabled())

    def test_admin_restart_button_is_hidden_when_already_elevated(self) -> None:
        with patch("app.main_window.is_running_as_admin", return_value=True):
            elevated_window = MainWindow()

        try:
            self.assertTrue(elevated_window.admin_restart_button.isHidden())
            self.assertFalse(elevated_window.acceptDrops())
            self.assertFalse(elevated_window.drop_zone.acceptDrops())
            self.assertIn("管理员模式", elevated_window.drop_zone._title.text())
            self.assertFalse(elevated_window.drop_zone._title.wordWrap())
            self.assertGreaterEqual(
                elevated_window.drop_zone._detail.minimumWidth(),
                600,
            )
            self.assertIn(
                "普通权限资源管理器",
                elevated_window.drop_zone._detail.text(),
            )
        finally:
            elevated_window.close()
            elevated_window.deleteLater()
            self.app.processEvents()

    def test_window_title_does_not_duplicate_application_display_name(self) -> None:
        self.assertEqual(self.window.windowTitle(), APP_DISPLAY_NAME)

    def test_window_and_header_use_application_icon(self) -> None:
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertFalse(self.window.app_logo.pixmap().isNull())
        self.assertGreaterEqual(self.window.app_logo.pixmap().width(), 32)

    def test_admin_restart_button_has_readable_label(self) -> None:
        self.assertEqual(
            self.window.admin_restart_button.text(),
            "以管理员身份重启",
        )

    def test_target_list_button_is_available_for_staged_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")
            self.window._select_paths([str(target)])

            self.assertTrue(self.window.target_list_button.isEnabled())
            self.assertEqual(
                self.window.target_list_button.text(),
                "目标列表 (1)",
            )

    def test_target_dialog_shows_full_moved_path_and_enables_undo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination_dir = root / "destination"
            destination_dir.mkdir()
            original = root / "sample.txt"
            moved = destination_dir / "sample.txt"
            original.write_text("content", encoding="utf-8")
            original.rename(moved)
            dialog = TargetListDialog(
                (moved,),
                last_move=(original, moved),
                parent=self.window,
            )
            try:
                self.assertEqual(dialog.table.item(0, 2).text(), str(moved))
                self.assertTrue(dialog.undo_button.isEnabled())
            finally:
                dialog.deleteLater()
                self.app.processEvents()

    def test_move_completion_updates_target_and_records_undo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination_dir = root / "destination"
            destination_dir.mkdir()
            source = root / "sample.txt"
            moved = destination_dir / "sample.txt"
            source.write_text("content", encoding="utf-8")
            source.rename(moved)
            self.window._target_paths = (source,)
            self.window._scan_result = BatchScanResult(
                targets=(), locks=(), scanned_files=1
            )
            self.window._update_action_buttons()

            self.window._on_file_operation_completed(
                str(source), "move", str(moved)
            )

            self.assertEqual(self.window._target_paths, (moved,))
            self.assertEqual(self.window._last_move, (source, moved))
            self.assertTrue(self.window.target_list_button.isEnabled())
            self.assertIn(str(moved), self.window.result_hint.text())

    def test_undo_completion_restores_target_and_clears_move_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "sample.txt"
            moved = root / "moved" / "sample.txt"
            moved.parent.mkdir()
            original.write_text("content", encoding="utf-8")
            original.rename(moved)
            self.window._target_paths = (moved,)
            self.window._last_move = (original, moved)
            moved.rename(original)

            self.window._on_file_operation_completed(
                str(moved), "move", str(original)
            )

            self.assertEqual(self.window._target_paths, (original,))
            self.assertIsNone(self.window._last_move)
            self.assertIn("已撤销移动", self.window.result_hint.text())

    def test_recycle_completion_removes_only_selected_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")
            self.window._target_paths = (first, second)
            self.window._last_move = (root / "original.txt", first)

            self.window._on_file_operation_completed(
                str(first), "recycle", str(first)
            )

            self.assertEqual(self.window._target_paths, (second,))
            self.assertIsNone(self.window._last_move)
            self.assertEqual(
                self.window.target_list_button.text(),
                "目标列表 (1)",
            )

    def test_file_operation_worker_recycles_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "folder"
            folder.mkdir()
            completed: list[tuple[str, str, str]] = []
            worker = FileOperationWorker(
                source_path=str(folder),
                operation="recycle",
            )
            worker.completed.connect(
                lambda source, operation, result: completed.append(
                    (source, operation, result)
                )
            )
            with (
                patch("app.workers.find_targets_locks") as scan_mock,
                patch(
                    "app.workers.recycle_target",
                    return_value=folder,
                ) as recycle_mock,
            ):
                scan_mock.return_value = BatchScanResult(
                    targets=(),
                    locks=(
                        FileLockInfo(
                            str(folder),
                            ProcessInfo(
                                4321,
                                "reader.exe",
                                "",
                                "",
                                123.0,
                            ),
                            lock_state=LockState.NON_BLOCKING,
                        ),
                    ),
                    scanned_files=0,
                    inaccessible_processes=2,
                )
                worker.run()

            recycle_mock.assert_called_once_with(str(folder))
            self.assertEqual(
                completed,
                [(str(folder), "recycle", str(folder))],
            )



    def test_batch_result_reports_inaccessible_folders(self) -> None:
        self.window._running_as_admin = True
        self.window._show_batch_result(
            BatchScanResult(
                targets=(),
                locks=(),
                scanned_files=0,
                inaccessible_paths=2,
            )
        )

        self.assertIn("2 个文件夹因权限不足未检查", self.window.result_hint.text())

    def test_selecting_linked_path_is_rejected_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")

            with (
                patch(
                    "app.main_window.linked_path_component",
                    return_value=target,
                ),
                patch("app.main_window.QMessageBox.warning") as warning_mock,
            ):
                self.window._select_paths([str(target)])

        self.assertEqual(self.window._target_paths, ())
        warning_mock.assert_called_once()
        self.assertIn("符号链接或目录联接", warning_mock.call_args.args[2])

    def test_file_operation_worker_rejects_control_character_before_terminating_processes(
        self,
    ) -> None:
        process = ProcessInfo(4321, "sample.exe", "", "", 123.0)
        failures: list[tuple[str, bool]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.txt"
            source.write_text("data", encoding="utf-8")
            worker = FileOperationWorker(
                source_path=str(source),
                operation="rename",
                new_name="bad\x01name",
                processes=(process,),
            )
            worker.failed.connect(
                lambda message, changed: failures.append((message, changed))
            )

            with (
                patch("app.workers.terminate_processes") as terminate_mock,
                patch("app.workers.find_targets_locks") as scan_mock,
            ):
                worker.run()

        terminate_mock.assert_not_called()
        scan_mock.assert_not_called()
        self.assertEqual(len(failures), 1)
        self.assertIn("控制字符", failures[0][0])
        self.assertFalse(failures[0][1])

    def test_language_switch_retranslates_static_ui(self) -> None:
        self._switch_language("en_US")

        self.assertEqual(i18n.get_language(), "en_US")
        self.save_language_mock.assert_called_once_with("en_US")
        self.assertEqual(self.window.windowTitle(), "UnlockGo File Lock Remover")
        self.assertEqual(
            self.window.app_subtitle.text(),
            "Find and resolve processes locking your files",
        )
        self.assertEqual(self.window.file_mode_button.text(), "File")
        self.assertEqual(self.window.folder_mode_button.text(), "Folder")
        self.assertEqual(self.window.rename_button.text(), "Rename")
        self.assertEqual(self.window.move_button.text(), "Move")
        self.assertEqual(self.window.copy_button.text(), "Copy")
        self.assertEqual(self.window.rescan_button.text(), "Scan")
        self.assertEqual(self.window.result_hint.text(), "No file selected")
        self.assertEqual(self.window.placeholder._title.text(), "No file selected")
        self.assertEqual(
            self.window.placeholder._detail.text(),
            "Add a target, then click Scan to check for locking processes.",
        )
        self.assertEqual(
            [
                self.window.table.horizontalHeaderItem(column).text()
                for column in range(self.window.table.columnCount())
            ],
            [
                "File",
                "Process",
                "PID",
                "Handles",
                "Candidate source",
                "Target state",
                "Executable",
                "Account",
                "Actions",
            ],
        )
        self.assertEqual(
            self.window.privacy_label.text(),
            "Runs locally \u00b7 Files are never uploaded",
        )

    def test_language_switch_preserves_targets_and_scan_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")
            result = BatchScanResult(
                targets=(),
                locks=(),
                scanned_files=1,
            )
            self.window._target_paths = (target,)
            self.window._show_batch_result(result)
            targets_before = self.window._target_paths
            result_before = self.window._scan_result

            self._switch_language("en_US")

        self.assertIs(self.window._target_paths, targets_before)
        self.assertIs(self.window._scan_result, result_before)
        self.assertEqual(self.window.rescan_button.text(), "Rescan")
        self.assertEqual(self.window.result_title.text(), "Lock status")

    def test_language_switch_is_disabled_during_scan(self) -> None:
        with (
            patch("app.main_window.QThread"),
            patch("app.main_window.TargetScanWorker"),
        ):
            self.window._begin_scan((Path("target.txt"),))

        self.assertFalse(self.window.language_combo.isEnabled())

    def test_language_switch_is_disabled_during_file_operation(self) -> None:
        with (
            patch("app.main_window.QThread"),
            patch("app.main_window.FileOperationWorker"),
        ):
            self.window._begin_file_operation(
                Path("target.txt"),
                "rename",
                (),
                new_name="renamed.txt",
            )

        self.assertFalse(self.window.language_combo.isEnabled())

    def test_lock_detail_tooltip_is_retranslated_with_scan_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.txt"
            target.write_text("target", encoding="utf-8")
            self.window._running_as_admin = True
            self.window._target_paths = (target,)
            self.window._show_batch_result(
                BatchScanResult(
                    targets=(),
                    locks=(
                        FileLockInfo(
                            str(target),
                            ProcessInfo(4321, "reader.exe", "", "", 123.0),
                            lock_state=LockState.NON_BLOCKING,
                            lock_detail_source='目标可以获取 DELETE 权限，本次探测未发现访问锁定。',
                        ),
                    ),
                    scanned_files=1,
                )
            )

            self.assertIn("DELETE", self.window.table.item(0, 5).toolTip())
            self._switch_language("en_US")
            tooltip = self.window.table.item(0, 5).toolTip()

        self.assertIn("no lock was detected by this probe", tooltip)
        self.assertIn(
            "Candidates without a detected lock: 1",
            self.window.result_hint.text(),
        )


if __name__ == "__main__":
    unittest.main()
