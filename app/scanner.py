from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace
from pathlib import Path

import psutil

from app import i18n
from app.models import (
    BatchScanResult,
    CandidateSource,
    FileIdentity,
    FileLockInfo,
    FolderScanResult,
    HandleIdentity,
    LockState,
    ProcessInfo,
    ScanResult,
    TargetScanResult,
)
from app.windows_handles import (
    FileHandleInfo,
    FileHandleScanResult,
    HandleScanCancelledError,
    enumerate_file_handles,
)
from app.windows_restart_manager import (
    RestartManagerError,
    find_restart_manager_processes,
)
from app.windows_lock_probe import LockProbeResult, probe_path_lock


class ScanCancelledError(RuntimeError):
    """The user cancelled the scan before it completed."""


CancelCheck = Callable[[], bool]


def _raise_if_cancelled(should_cancel: CancelCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise ScanCancelledError(i18n.tr("扫描已取消。"))


def _normalize_path(path: str | os.PathLike[str]) -> str:
    expanded = os.path.expandvars(os.fspath(path))
    try:
        resolved = Path(expanded).resolve(strict=False)
    except OSError:
        resolved = Path(expanded).absolute()
    return os.path.normcase(os.path.normpath(str(resolved)))


def _safe_process_value(process: psutil.Process, key: str, fallback: str = "") -> str:
    info = getattr(process, "info", {})
    value = info.get(key) if isinstance(info, dict) else None
    if not isinstance(value, str) or not value.strip():
        try:
            value = getattr(process, key)()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            value = None
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _safe_process_create_time(process: psutil.Process) -> float | None:
    info = getattr(process, "info", {})
    value = info.get("create_time") if isinstance(info, dict) else None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(process.create_time())
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return None


def _make_process_info(process: psutil.Process, fallback_name: str = "") -> ProcessInfo:
    return ProcessInfo(
        pid=process.pid,
        name=_safe_process_value(process, "name", fallback_name) or f"PID {process.pid}",
        exe=_safe_process_value(process, "exe"),
        username=_safe_process_value(process, "username"),
        create_time=_safe_process_create_time(process),
    )


def _collect_process_resources(
    process: psutil.Process,
    *,
    include_open_files: bool = True,
    should_cancel: CancelCheck | None = None,
) -> tuple[dict[str, str], bool]:
    """Return normalized resource paths and whether a source was inaccessible."""

    resources: dict[str, str] = {}
    inaccessible = False

    accessors = [process.memory_maps]
    if include_open_files:
        accessors.append(process.open_files)

    for accessor in accessors:
        _raise_if_cancelled(should_cancel)
        try:
            entries = accessor()
        except (psutil.AccessDenied, psutil.ZombieProcess):
            inaccessible = True
            continue
        except psutil.NoSuchProcess:
            raise
        except OSError:
            inaccessible = True
            continue

        for entry in entries:
            path = getattr(entry, "path", "")
            if not path:
                continue
            normalized = _normalize_path(path)
            resources.setdefault(normalized, os.fspath(path))

    return resources, inaccessible


def _matching_target_indices(
    normalized_path: str,
    file_targets: dict[str, list[int]],
    folder_targets: list[tuple[int, str, bool]],
) -> list[int]:
    matches = list(file_targets.get(normalized_path, ()))
    for target_index, folder_normalized, folder_recursive in folder_targets:
        if _is_path_in_folder(
            normalized_path,
            folder_normalized,
            recursive=folder_recursive,
        ):
            matches.append(target_index)
    return matches


def _process_info_for_pid(pid: int) -> ProcessInfo | None:
    try:
        return _make_process_info(psutil.Process(pid))
    except psutil.NoSuchProcess:
        return None
    except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return ProcessInfo(
            pid=pid,
            name=f"PID {pid}",
            exe="",
            username="",
        )


def _safe_enumerate_file_handles(
    *,
    pid_filter: set[int] | None = None,
    identity_filter: Callable[[str], bool] | None = None,
    should_cancel: CancelCheck | None = None,
) -> FileHandleScanResult:
    _raise_if_cancelled(should_cancel)
    try:
        return enumerate_file_handles(
            pid_filter=pid_filter,
            identity_filter=identity_filter,
            should_cancel=should_cancel,
        )
    except HandleScanCancelledError as exc:
        raise ScanCancelledError("Scan cancelled.") from exc
    except (AttributeError, OSError):
        # The legacy scanners remain available when raw handle access is denied.
        return FileHandleScanResult(handles=(), scan_succeeded=False)


def _merge_lock(
    locks: list[FileLockInfo],
    lock_indexes: dict[tuple[int, str, int], int],
    *,
    target_index: int,
    file_path: str,
    process_info: ProcessInfo,
    handles: Iterable[int] = (),
    handle_identities: Iterable[HandleIdentity] = (),
    source: CandidateSource,
) -> None:
    normalized = _normalize_path(file_path)
    key = (target_index, normalized, process_info.pid)
    existing_index = lock_indexes.get(key)
    new_handles = tuple(sorted(set(handles)))
    new_handle_identities = tuple(
        sorted(
            set(handle_identities),
            key=lambda item: (
                item.handle_value,
                item.file_identity.volume_serial,
                item.file_identity.file_index_high,
                item.file_identity.file_index_low,
            ),
        )
    )
    new_sources = (source,)
    if existing_index is None:
        lock_indexes[key] = len(locks)
        locks.append(
            FileLockInfo(
                file_path=file_path,
                process=process_info,
                handles=new_handles,
                handle_identities=new_handle_identities,
                sources=new_sources,
            )
        )
        return

    existing = locks[existing_index]
    merged_handles = tuple(sorted(set(existing.handles).union(new_handles)))
    merged_handle_identities = tuple(
        sorted(
            set(existing.handle_identities).union(new_handle_identities),
            key=lambda item: (
                item.handle_value,
                item.file_identity.volume_serial,
                item.file_identity.file_index_high,
                item.file_identity.file_index_low,
            ),
        )
    )
    merged_source_set = set(existing.sources).union(new_sources)
    merged_sources = tuple(
        candidate_source
        for candidate_source in CandidateSource
        if candidate_source in merged_source_set
    )
    if (
        merged_handles != existing.handles
        or merged_handle_identities != existing.handle_identities
        or merged_sources != existing.sources
    ):
        locks[existing_index] = replace(
            existing,
            handles=merged_handles,
            handle_identities=merged_handle_identities,
            sources=merged_sources,
        )


def _merge_single_target_handle(
    locks: list[FileLockInfo],
    handles_by_process: dict[tuple[str, int], int],
    *,
    file_path: str,
    process_info: ProcessInfo,
    handle_value: int,
    file_identity: FileIdentity | None = None,
) -> None:
    normalized = _normalize_path(file_path)
    key = (normalized, process_info.pid)
    existing_index = handles_by_process.get(key)
    new_handle_identities = (
        (HandleIdentity(handle_value=handle_value, file_identity=file_identity),)
        if file_identity is not None
        else ()
    )
    if existing_index is None:
        handles_by_process[key] = len(locks)
        locks.append(
            FileLockInfo(
                file_path=file_path,
                process=process_info,
                handles=(handle_value,),
                handle_identities=new_handle_identities,
                sources=(CandidateSource.SYSTEM_HANDLE,),
            )
        )
        return

    existing = locks[existing_index]
    merged_handles = tuple(sorted(set(existing.handles).union({handle_value})))
    merged_handle_identities = tuple(
        sorted(
            set(existing.handle_identities).union(new_handle_identities),
            key=lambda item: (
                item.handle_value,
                item.file_identity.volume_serial,
                item.file_identity.file_index_high,
                item.file_identity.file_index_low,
            ),
        )
    )
    if (
        merged_handles != existing.handles
        or merged_handle_identities != existing.handle_identities
    ):
        locks[existing_index] = replace(
            existing,
            handles=merged_handles,
            handle_identities=merged_handle_identities,
        )

def _find_open_targets(
    targets: dict[str, str],
    *,
    should_cancel: CancelCheck | None = None,
) -> tuple[list[FileLockInfo], int]:
    """Scan each process once and match handles or mapped files to targets."""

    locks: list[FileLockInfo] = []
    inaccessible = 0

    for process in psutil.process_iter(["pid", "name", "exe", "username", "create_time"]):
        _raise_if_cancelled(should_cancel)
        try:
            resources, process_inaccessible = _collect_process_resources(
                process,
                include_open_files=True,
                should_cancel=should_cancel,
            )
            if process_inaccessible:
                inaccessible += 1
            for normalized, original_path in resources.items():
                display_path = targets.get(normalized)
                if display_path is None:
                    continue
                locks.append(
                    FileLockInfo(
                        file_path=display_path,
                        process=_make_process_info(process),
                        sources=(CandidateSource.PROCESS_RESOURCES,),
                    )
                )
        except (psutil.AccessDenied, psutil.ZombieProcess):
            inaccessible += 1
        except psutil.NoSuchProcess:
            continue
        except OSError:
            inaccessible += 1

    return locks, inaccessible


def _find_folder_targets(
    folder: Path,
    *,
    recursive: bool,
    should_cancel: CancelCheck | None = None,
) -> tuple[list[FileLockInfo], int]:
    """Match process handles and mapped files against a folder scope."""

    locks: list[FileLockInfo] = []
    inaccessible = 0
    folder_normalized = _normalize_path(folder)
    folder_prefix = folder_normalized.rstrip("\\/") + os.sep

    for process in psutil.process_iter(["pid", "name", "exe", "username", "create_time"]):
        _raise_if_cancelled(should_cancel)
        try:
            resources, process_inaccessible = _collect_process_resources(
                process,
                include_open_files=True,
                should_cancel=should_cancel,
            )
            if process_inaccessible:
                inaccessible += 1
            process_info: ProcessInfo | None = None
            for normalized, original_path in resources.items():
                in_scope = (
                    normalized.startswith(folder_prefix)
                    if recursive
                    else os.path.dirname(normalized) == folder_normalized
                )
                if not in_scope:
                    continue
                if process_info is None:
                    process_info = _make_process_info(process)
                locks.append(
                    FileLockInfo(
                        file_path=original_path,
                        process=process_info,
                        sources=(CandidateSource.PROCESS_RESOURCES,),
                    )
                )
        except (psutil.AccessDenied, psutil.ZombieProcess):
            inaccessible += 1
        except psutil.NoSuchProcess:
            continue
        except OSError:
            inaccessible += 1

    return locks, inaccessible


def _find_candidate_targets(
    targets: dict[str, str],
    candidates: Iterable[ProcessInfo],
    *,
    should_cancel: CancelCheck | None = None,
) -> tuple[list[FileLockInfo], int]:
    """Recheck only previously known holders after a targeted process action."""

    locks: list[FileLockInfo] = []
    inaccessible = 0
    seen_pids: set[int] = set()

    for candidate in candidates:
        _raise_if_cancelled(should_cancel)
        if candidate.pid in seen_pids:
            continue
        seen_pids.add(candidate.pid)

        try:
            process = psutil.Process(candidate.pid)
            if candidate.create_time is None:
                inaccessible += 1
                continue
            if abs(process.create_time() - candidate.create_time) > 0.01:
                continue
            resources, process_inaccessible = _collect_process_resources(
                process,
                include_open_files=True,
                should_cancel=should_cancel,
            )
            if process_inaccessible:
                inaccessible += 1
            for normalized, original_path in resources.items():
                display_path = targets.get(normalized)
                if display_path is None:
                    continue
                locks.append(
                    FileLockInfo(
                        file_path=display_path,
                        process=_make_process_info(process),
                        sources=(CandidateSource.PROCESS_RESOURCES,),
                    )
                )
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
            inaccessible += 1

    return locks, inaccessible


def find_file_locks(
    file_path: str | os.PathLike[str],
    *,
    should_cancel: CancelCheck | None = None,
    process_candidates: Iterable[ProcessInfo] | None = None,
) -> ScanResult:
    """Return processes using *file_path* as an open handle or mapped file.

    When *process_candidates* is supplied, only those previously known holders
    are inspected instead of scanning every process's handle table. Restart
    Manager is still queried, and the later recycle operation remains protected
    by Windows' own sharing checks.
    """

    _raise_if_cancelled(should_cancel)
    normalized_target = _normalize_path(file_path)
    targets = {normalized_target: os.fspath(file_path)}
    if process_candidates is None:
        locks, inaccessible = _find_open_targets(
            targets,
            should_cancel=should_cancel,
        )
    else:
        locks, inaccessible = _find_candidate_targets(
            targets,
            process_candidates,
            should_cancel=should_cancel,
        )

    _raise_if_cancelled(should_cancel)
    seen_pids = {lock.process.pid for lock in locks}
    try:
        restart_manager_entries = find_restart_manager_processes(file_path)
    except (OSError, RestartManagerError):
        restart_manager_entries = ()

    for entry in restart_manager_entries:
        if entry.pid in seen_pids:
            continue
        try:
            process = psutil.Process(entry.pid)
            process_info = _make_process_info(process, fallback_name=entry.app_name)
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            process_info = ProcessInfo(
                pid=entry.pid,
                name=entry.app_name or f"PID {entry.pid}",
                exe="",
                username="",
            )
        seen_pids.add(entry.pid)
        locks.append(
            FileLockInfo(
                file_path=os.fspath(file_path),
                process=process_info,
                sources=(CandidateSource.RESTART_MANAGER,),
            )
        )

    processes = [lock.process for lock in locks]
    processes.sort(key=lambda item: (item.name.casefold(), item.pid))
    return ScanResult(processes=tuple(processes), inaccessible_processes=inaccessible)


def _walk_folder_files(
    folder: Path,
    recursive: bool,
    *,
    inaccessible_paths: list[str],
    should_cancel: CancelCheck | None = None,
) -> Iterator[Path]:
    _raise_if_cancelled(should_cancel)
    try:
        entries = os.scandir(folder)
    except OSError:
        inaccessible_paths.append(str(folder))
        return

    with entries:
        try:
            for entry in entries:
                _raise_if_cancelled(should_cancel)
                try:
                    if entry.is_file(follow_symlinks=False):
                        yield Path(entry.path)
                    elif recursive and entry.is_dir(follow_symlinks=False):
                        yield from _walk_folder_files(
                            Path(entry.path),
                            True,
                            inaccessible_paths=inaccessible_paths,
                            should_cancel=should_cancel,
                        )
                except OSError:
                    inaccessible_paths.append(str(entry.path))
        except OSError:
            inaccessible_paths.append(str(folder))


def _count_folder_files(
    folder: Path,
    recursive: bool,
    *,
    should_cancel: CancelCheck | None = None,
) -> tuple[int, int]:
    inaccessible_paths: list[str] = []
    scanned_files = sum(
        1
        for _ in _walk_folder_files(
            folder,
            recursive,
            inaccessible_paths=inaccessible_paths,
            should_cancel=should_cancel,
        )
    )
    return scanned_files, len(inaccessible_paths)


def find_folder_locks(
    folder_path: str | os.PathLike[str],
    *,
    recursive: bool = False,
    should_cancel: CancelCheck | None = None,
) -> FolderScanResult:
    """Find files in a folder that are held open or mapped by processes.

    Scanning is process-driven rather than limited by a target-file list. This
    removes the previous 1000-file truncation and includes loaded executables,
    DLLs, and extension modules that do not appear in psutil.open_files().
    Symlinked directories are not followed.
    """

    _raise_if_cancelled(should_cancel)
    folder = Path(folder_path).resolve()
    scanned_files, inaccessible_paths = _count_folder_files(
        folder,
        recursive,
        should_cancel=should_cancel,
    )
    if scanned_files == 0:
        return FolderScanResult(
            scanned_files=0,
            locks=(),
            inaccessible_paths=inaccessible_paths,
        )

    locks, inaccessible = _find_folder_targets(
        folder,
        recursive=recursive,
        should_cancel=should_cancel,
    )
    locks.sort(
        key=lambda item: (
            os.path.normcase(item.file_path),
            item.process.name.casefold(),
            item.process.pid,
        )
    )
    return FolderScanResult(
        scanned_files=scanned_files,
        locks=tuple(locks),
        inaccessible_processes=inaccessible,
        inaccessible_paths=inaccessible_paths,
    )


def _is_path_in_folder(
    normalized_path: str,
    folder_normalized: str,
    *,
    recursive: bool,
) -> bool:
    if normalized_path == folder_normalized:
        return True
    if recursive:
        prefix = folder_normalized.rstrip("\\/") + os.sep
        return normalized_path.startswith(prefix)
    return os.path.dirname(normalized_path) == folder_normalized


def _classify_target_locks(
    locks_by_target: dict[int, list[FileLockInfo]],
    *,
    should_cancel: CancelCheck | None = None,
) -> None:
    """Apply one non-destructive DELETE probe per distinct matched path."""

    probes: dict[str, LockProbeResult] = {}
    for target_locks in locks_by_target.values():
        for index, lock in enumerate(target_locks):
            _raise_if_cancelled(should_cancel)
            normalized = _normalize_path(lock.file_path)
            probe = probes.get(normalized)
            if probe is None:
                try:
                    probe = probe_path_lock(lock.file_path)
                except OSError as exc:
                    detail_source = "锁定探测失败：{message}"
                    probe = LockProbeResult(
                        state=LockState.UNKNOWN,
                        detail=i18n.tr(detail_source, message=exc),
                        detail_source=detail_source,
                        detail_values=(("message", exc),),
                    )
                probes[normalized] = probe
            target_locks[index] = replace(
                lock,
                lock_state=probe.state,
                lock_detail=probe.detail,
                lock_detail_source=probe.detail_source,
                lock_detail_values=probe.detail_values,
            )


def find_targets_locks(
    target_paths: Iterable[str | os.PathLike[str]],
    *,
    recursive: bool = False,
    should_cancel: CancelCheck | None = None,
) -> BatchScanResult:
    """Scan files and folders once, including raw Windows File handles."""

    _raise_if_cancelled(should_cancel)
    unique_targets: list[Path] = []
    seen_targets: set[str] = set()
    for raw_path in target_paths:
        path = Path(raw_path).expanduser().resolve(strict=True)
        normalized = _normalize_path(path)
        if normalized in seen_targets:
            continue
        if not path.is_file() and not path.is_dir():
            raise ValueError(
                i18n.tr("目标不是文件或文件夹：{path}", path=path)
            )
        seen_targets.add(normalized)
        unique_targets.append(path)

    if not unique_targets:
        raise ValueError(i18n.tr("没有可扫描的目标。"))

    target_metadata: list[tuple[Path, bool, int, int]] = []
    file_targets: dict[str, list[int]] = {}
    folder_targets: list[tuple[int, str, bool]] = []
    locks_by_target: dict[int, list[FileLockInfo]] = {}
    lock_indexes: dict[tuple[int, str, int], int] = {}

    for index, path in enumerate(unique_targets):
        locks_by_target[index] = []
        if path.is_dir():
            scanned_files, inaccessible_paths = _count_folder_files(
                path,
                recursive,
                should_cancel=should_cancel,
            )
            target_metadata.append(
                (path, True, scanned_files, inaccessible_paths)
            )
            folder_targets.append((index, _normalize_path(path), recursive))
        else:
            target_metadata.append((path, False, 1, 0))
            file_targets.setdefault(_normalize_path(path), []).append(index)

    inaccessible = 0
    def should_read_identity(file_path: str) -> bool:
        normalized = _normalize_path(file_path)
        return bool(
            _matching_target_indices(
                normalized,
                file_targets,
                folder_targets,
            )
        )

    handle_result = _safe_enumerate_file_handles(
        identity_filter=should_read_identity,
        should_cancel=should_cancel,
    )
    inaccessible += handle_result.inaccessible_processes

    if not handle_result.scan_succeeded:
        # Raw File handles are the fast primary source. The full psutil pass is
        # only needed when that source is unavailable on the current system.
        for process in psutil.process_iter(
            ["pid", "name", "exe", "username", "create_time"]
        ):
            _raise_if_cancelled(should_cancel)
            try:
                resources, process_inaccessible = _collect_process_resources(
                    process,
                    include_open_files=True,
                    should_cancel=should_cancel,
                )
                if process_inaccessible:
                    inaccessible += 1
                process_info: ProcessInfo | None = None
                for normalized, original_path in resources.items():
                    matching_targets = _matching_target_indices(
                        normalized,
                        file_targets,
                        folder_targets,
                    )
                    if not matching_targets:
                        continue
                    if process_info is None:
                        process_info = _make_process_info(process)
                    for target_index in matching_targets:
                        _merge_lock(
                            locks_by_target[target_index],
                            lock_indexes,
                            target_index=target_index,
                            file_path=original_path,
                            process_info=process_info,
                            source=CandidateSource.PROCESS_RESOURCES,
                        )
            except (psutil.AccessDenied, psutil.ZombieProcess):
                inaccessible += 1
            except psutil.NoSuchProcess:
                continue
            except OSError:
                inaccessible += 1

    process_info_by_pid: dict[int, ProcessInfo | None] = {}
    for found_handle in handle_result.handles:
        normalized = _normalize_path(found_handle.file_path)
        matching_targets = _matching_target_indices(
            normalized,
            file_targets,
            folder_targets,
        )
        if not matching_targets:
            continue
        if found_handle.pid not in process_info_by_pid:
            process_info_by_pid[found_handle.pid] = _process_info_for_pid(
                found_handle.pid
            )
        process_info = process_info_by_pid[found_handle.pid]
        if process_info is None:
            continue
        handle_identities = (
            (
                HandleIdentity(
                    handle_value=found_handle.handle_value,
                    file_identity=found_handle.file_identity,
                ),
            )
            if found_handle.file_identity is not None
            else ()
        )
        for target_index in matching_targets:
            _merge_lock(
                locks_by_target[target_index],
                lock_indexes,
                target_index=target_index,
                file_path=found_handle.file_path,
                process_info=process_info,
                handles=(found_handle.handle_value,),
                handle_identities=handle_identities,
                source=CandidateSource.SYSTEM_HANDLE,
            )

    _raise_if_cancelled(should_cancel)
    for normalized_file, target_indices in file_targets.items():
        try:
            restart_manager_entries = find_restart_manager_processes(
                normalized_file
            )
        except (OSError, RestartManagerError):
            continue
        for entry in restart_manager_entries:
            try:
                process = psutil.Process(entry.pid)
                process_info = _make_process_info(
                    process,
                    fallback_name=entry.app_name,
                )
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                process_info = ProcessInfo(
                    pid=entry.pid,
                    name=entry.app_name or f"PID {entry.pid}",
                    exe="",
                    username="",
                )
            for target_index in target_indices:
                _merge_lock(
                    locks_by_target[target_index],
                    lock_indexes,
                    target_index=target_index,
                    file_path=os.fspath(unique_targets[target_index]),
                    process_info=process_info,
                    source=CandidateSource.RESTART_MANAGER,
                )

    _classify_target_locks(
        locks_by_target,
        should_cancel=should_cancel,
    )

    target_results: list[TargetScanResult] = []
    aggregate_locks: list[FileLockInfo] = []
    aggregate_seen: set[tuple[str, int]] = set()
    for index, (path, is_folder, scanned_files, inaccessible_paths) in enumerate(target_metadata):
        target_locks = locks_by_target[index]
        target_locks.sort(
            key=lambda item: (
                _normalize_path(item.file_path),
                item.process.name.casefold(),
                item.process.pid,
            )
        )
        target_results.append(
            TargetScanResult(
                target_path=os.fspath(path),
                is_folder=is_folder,
                scanned_files=scanned_files,
                locks=tuple(target_locks),
                inaccessible_paths=inaccessible_paths,
            )
        )
        for lock in target_locks:
            key = (_normalize_path(lock.file_path), lock.process.pid)
            if key not in aggregate_seen:
                aggregate_seen.add(key)
                aggregate_locks.append(lock)

    aggregate_locks.sort(
        key=lambda item: (
            _normalize_path(item.file_path),
            item.process.name.casefold(),
            item.process.pid,
        )
    )
    return BatchScanResult(
        targets=tuple(target_results),
        locks=tuple(aggregate_locks),
        scanned_files=sum(item[2] for item in target_metadata),
        inaccessible_processes=inaccessible,
        inaccessible_paths=sum(item[3] for item in target_metadata),
    )

def validate_file(file_path: str | os.PathLike[str]) -> Path:
    path = Path(file_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            i18n.tr("文件不存在：{path}", path=path)
        )
    if not path.is_file():
        raise ValueError(
            i18n.tr("请选择一个文件，而不是文件夹：{path}", path=path)
        )
    return path.resolve()


def validate_folder(folder_path: str | os.PathLike[str]) -> Path:
    path = Path(folder_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            i18n.tr("文件夹不存在：{path}", path=path)
        )
    if not path.is_dir():
        raise ValueError(
            i18n.tr("请选择一个文件夹，而不是文件：{path}", path=path)
        )
    return path.resolve()
