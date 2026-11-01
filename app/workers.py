from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtCore import QObject, Signal, Slot

from app import i18n
from app.file_actions import (
    FileActionError,
    preflight_recycle_target,
    recycle_target,
    terminate_and_recycle,
)
from app.file_operations import (
    FileOperationError,
    copy_target,
    move_target,
    preflight_file_operation,
    rename_target,
)
from app.models import BatchScanResult, HandleIdentity, LockState, ProcessInfo
from app.process_actions import (
    BatchTerminationResult,
    ProcessActionError,
    terminate_process,
    terminate_processes,
)
from app.scanner import ScanCancelledError, find_targets_locks
from app.windows_handle_release import (
    HandleReleaseError,
    HandleReleaseResult,
    release_file_handle,
)


class TargetScanWorker(QObject):
    """Runs one or more file/folder scans away from the GUI thread."""

    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        target_paths: Sequence[str],
        *,
        recursive: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._target_paths = tuple(target_paths)
        self._recursive = recursive

    @Slot()
    def run(self) -> None:
        thread = self.thread()

        def should_cancel() -> bool:
            return thread is not None and thread.isInterruptionRequested()

        try:
            result: BatchScanResult = find_targets_locks(
                self._target_paths,
                recursive=self._recursive,
                should_cancel=should_cancel,
            )
            if should_cancel():
                self.cancelled.emit()
                return
            self.completed.emit(result)
        except ScanCancelledError:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.failed.emit(str(exc))


class TerminateProcessWorker(QObject):
    """Terminates one verified process away from the GUI thread."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, process: ProcessInfo, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = process

    @Slot()
    def run(self) -> None:
        try:
            terminate_process(self._process)
        except ProcessActionError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.failed.emit(i18n.tr("结束进程时发生未知错误：{message}", message=exc))
        else:
            self.completed.emit(self._process)


class ReleaseHandleWorker(QObject):
    """Releases one verified source process handle away from the GUI thread."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        process: ProcessInfo,
        handle_identity: HandleIdentity,
        target_path: str,
        *,
        allow_high_risk: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._process = process
        self._handle_identity = handle_identity
        self._target_path = target_path
        self._allow_high_risk = allow_high_risk

    @Slot()
    def run(self) -> None:
        try:
            result: HandleReleaseResult = release_file_handle(
                self._process,
                self._handle_identity.handle_value,
                self._target_path,
                expected_identity=self._handle_identity.file_identity,
                allow_high_risk=self._allow_high_risk,
            )
        except (HandleReleaseError, ProcessActionError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.failed.emit(i18n.tr("释放句柄时发生未知错误：{message}", message=exc))
        else:
            self.completed.emit(result)


class TerminateAllProcessesWorker(QObject):
    """Terminates every eligible process associated with one result file."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        processes: Iterable[ProcessInfo],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._processes = tuple(processes)

    @Slot()
    def run(self) -> None:
        try:
            result: BatchTerminationResult = terminate_processes(self._processes)
        except ProcessActionError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.failed.emit(i18n.tr("结束关联进程时发生未知错误：{message}", message=exc))
        else:
            self.completed.emit(result)


class FileOperationWorker(QObject):
    """Optionally unlocks a target, verifies it, then renames, moves, or copies it."""

    completed = Signal(str, str, str)
    failed = Signal(str, bool)

    def __init__(
        self,
        *,
        source_path: str,
        operation: str,
        processes: Iterable[ProcessInfo] = (),
        destination_directory: str = "",
        new_name: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_path = source_path
        self._operation = operation
        self._processes = tuple(processes)
        self._destination_directory = destination_directory
        self._new_name = new_name

    @Slot()
    def run(self) -> None:
        changed = False
        try:
            if self._operation == "recycle":
                preflight_recycle_target(
                    self._source_path,
                    allow_directory=True,
                )
            else:
                preflight_file_operation(
                    self._source_path,
                    self._operation,
                    destination_directory=self._destination_directory,
                    new_name=self._new_name,
                )

            if self._processes:
                result = terminate_processes(self._processes)
                changed = bool(result.terminated)
                if result.failures:
                    details = "；".join(
                        f"{item.process.name} (PID {item.process.pid})：{item.message}"
                        for item in result.failures[:4]
                    )
                    raise FileOperationError(
                        i18n.tr("部分关联进程未能结束，为安全起见已取消文件操作。")
                        + (f"\n{details}" if details else "")
                    )

            verification = find_targets_locks(
                [self._source_path],
                recursive=True,
            )
            blocking_locks = [
                lock
                for lock in verification.locks
                if lock.lock_state == LockState.BLOCKING
            ]
            # Non-blocking and unknown results are not proof that the target is
            # locked. The operation below lets Windows perform the final check.
            if blocking_locks:
                details = ", ".join(
                    f"{lock.process.name} (PID {lock.process.pid})"
                    for lock in blocking_locks[:5]
                )
                raise FileOperationError(
                    i18n.tr("目标仍存在访问锁定，无法执行文件操作：{path}", path=details)
                )

            if self._operation == "rename":
                result_path = rename_target(self._source_path, self._new_name)
            elif self._operation == "move":
                result_path = move_target(
                    self._source_path,
                    self._destination_directory,
                )
            elif self._operation == "copy":
                result_path = copy_target(
                    self._source_path,
                    self._destination_directory,
                )
            elif self._operation == "recycle":
                result_path = recycle_target(self._source_path)
            else:
                raise FileOperationError(
                    i18n.tr("不支持的文件操作：{operation}", operation=self._operation)
                )
        except ProcessActionError as exc:
            self.failed.emit(str(exc), changed)
        except (FileActionError, FileOperationError) as exc:
            self.failed.emit(str(exc), changed)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.failed.emit(
                i18n.tr("执行文件操作时发生未知错误：{message}", message=exc),
                changed,
            )
        else:
            self.completed.emit(self._source_path, self._operation, str(result_path))


class TerminateAndRecycleWorker(QObject):
    """Ends one selected process, verifies locks, then recycles its file."""

    completed = Signal(object, str)
    failed = Signal(str, bool)

    def __init__(
        self,
        process: ProcessInfo,
        file_path: str,
        other_processes: tuple[ProcessInfo, ...] = (),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._process = process
        self._file_path = file_path
        self._other_processes = other_processes

    @Slot()
    def run(self) -> None:
        try:
            terminate_and_recycle(
                self._process,
                self._file_path,
                self._other_processes,
            )
        except ProcessActionError as exc:
            self.failed.emit(str(exc), False)
        except FileActionError as exc:
            self.failed.emit(str(exc), True)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.failed.emit(
                i18n.tr("结束进程并回收文件时发生未知错误：{message}", message=exc),
                True,
            )
        else:
            self.completed.emit(self._process, self._file_path)
