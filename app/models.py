from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LockState(str, Enum):
    """Whether an observed handle currently blocks deletion or renaming."""

    BLOCKING = "blocking"
    NON_BLOCKING = "non_blocking"
    UNKNOWN = "unknown"


class CandidateSource(str, Enum):
    """A discovery channel that produced a process candidate."""

    SYSTEM_HANDLE = "system_handle"
    RESTART_MANAGER = "restart_manager"
    PROCESS_RESOURCES = "process_resources"


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    """A process that currently uses a file or module."""

    pid: int
    name: str
    exe: str
    username: str
    create_time: float | None = None


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """The stable NTFS object identity exposed by Windows."""

    volume_serial: int
    file_index_high: int
    file_index_low: int


@dataclass(frozen=True, slots=True)
class HandleIdentity:
    """The scan-time identity of one process File handle."""

    handle_value: int
    file_identity: FileIdentity


@dataclass(frozen=True, slots=True)
class FileLockInfo:
    """A file and one process that currently uses it."""

    file_path: str
    process: ProcessInfo
    handles: tuple[int, ...] = ()
    sources: tuple[CandidateSource, ...] = ()
    lock_state: LockState = LockState.UNKNOWN
    lock_detail: str = ""
    handle_identities: tuple[HandleIdentity, ...] = ()
    lock_detail_source: str = ""
    lock_detail_values: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class ScanResult:
    """The result of scanning one file for handles or mapped modules."""

    processes: tuple[ProcessInfo, ...]
    inaccessible_processes: int = 0


@dataclass(frozen=True, slots=True)
class FolderScanResult:
    """The result of scanning the files inside one folder."""

    scanned_files: int
    locks: tuple[FileLockInfo, ...]
    inaccessible_processes: int = 0
    inaccessible_paths: int = 0


@dataclass(frozen=True, slots=True)
class TargetScanResult:
    """The result belonging to one file or folder target in a batch scan."""

    target_path: str
    is_folder: bool
    scanned_files: int
    locks: tuple[FileLockInfo, ...]
    inaccessible_paths: int = 0


@dataclass(frozen=True, slots=True)
class BatchScanResult:
    """Combined results for one or more files and folders."""

    targets: tuple[TargetScanResult, ...]
    locks: tuple[FileLockInfo, ...]
    scanned_files: int
    inaccessible_processes: int = 0
    inaccessible_paths: int = 0
