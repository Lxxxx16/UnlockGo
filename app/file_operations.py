from __future__ import annotations

import os
import shutil
from pathlib import Path

from app import i18n


class FileOperationError(RuntimeError):
    """A rename, move, or copy operation could not be completed safely."""


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError as exc:
        raise FileOperationError(
            i18n.tr("无法检查路径是否为符号链接或目录联接：{path}", path=path)
        ) from exc


def linked_path_component(path: str | os.PathLike[str]) -> Path | None:
    """Return a symlink/junction component in *path*, if one exists."""

    candidate = Path(path).expanduser().absolute()
    for component in (candidate, *candidate.parents):
        if _is_link_like(component):
            return component
    return None


def _reject_linked_path(
    path: str | os.PathLike[str],
    *,
    label: str | None = None,
) -> None:
    linked = linked_path_component(path)
    if linked is not None:
        label_text = label or i18n.tr("目标")
        raise FileOperationError(
            i18n.tr(
                "为安全起见，不支持操作符号链接或目录联接目标："
                "{label}路径中的 {path}",
                label=label_text,
                path=linked,
            )
        )


def _entry_exists(path: Path) -> bool:
    # lexists also detects a broken symlink, which exists() deliberately hides.
    return os.path.lexists(os.fspath(path))


def _resolved_existing(path: str | os.PathLike[str]) -> Path:
    source = Path(path).expanduser()
    _reject_linked_path(source)
    try:
        target = source.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FileOperationError(
            i18n.tr("目标不存在或无法访问：{path}", path=source)
        ) from exc
    if not target.is_file() and not target.is_dir():
        raise FileOperationError(
            i18n.tr("目标不是普通文件或文件夹：{path}", path=target)
        )
    return target


def _validate_new_name(name: str) -> str:
    if any(ord(character) < 32 for character in name):
        raise FileOperationError(
            i18n.tr("新名称包含 Windows 不允许的控制字符。")
        )
    cleaned = name.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise FileOperationError(i18n.tr("新名称不能为空。"))
    if Path(cleaned).name != cleaned or "/" in cleaned or "\\" in cleaned:
        raise FileOperationError(i18n.tr("新名称不能包含路径分隔符。"))
    if any(character in cleaned for character in '<>:"|?*'):
        raise FileOperationError(
            i18n.tr("新名称包含 Windows 不允许的字符。")
        )
    return cleaned


def _destination_in_source(source: Path, destination: Path) -> bool:
    if not source.is_dir():
        return False
    try:
        destination.relative_to(source)
        return True
    except ValueError:
        return False


def _prepare_rename(
    source_path: str | os.PathLike[str],
    new_name: str,
) -> tuple[Path, Path]:
    source = _resolved_existing(source_path)
    cleaned = _validate_new_name(new_name)
    destination = source.with_name(cleaned)
    if os.path.normcase(str(destination)) == os.path.normcase(str(source)):
        raise FileOperationError(i18n.tr("新名称与原名称相同。"))
    if _entry_exists(destination):
        raise FileOperationError(
            i18n.tr("目标名称已存在：{name}", name=destination.name)
        )
    return source, destination


def _prepare_destination_directory(
    destination_directory: str | os.PathLike[str],
    *,
    operation_label: str,
) -> Path:
    destination_dir = Path(destination_directory).expanduser()
    _reject_linked_path(
        destination_dir,
        label=i18n.tr("目标文件夹"),
    )
    if not destination_dir.is_dir():
        raise FileOperationError(
            i18n.tr(
                "{operation}目标必须是一个已存在的文件夹。",
                operation=operation_label,
            )
        )
    try:
        return destination_dir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FileOperationError(
            i18n.tr(
                "{operation}目标文件夹无法访问：{path}",
                operation=operation_label,
                path=destination_dir,
            )
        ) from exc


def _reject_linked_entries(folder: Path) -> None:
    """Reject links inside a folder so copying cannot leave its source tree."""

    try:
        entries = os.scandir(folder)
    except OSError as exc:
        raise FileOperationError(
            i18n.tr("无法安全检查要复制的文件夹：{path}", path=folder)
        ) from exc

    with entries:
        try:
            for entry in entries:
                entry_path = Path(entry.path)
                if _is_link_like(entry_path):
                    raise FileOperationError(
                        i18n.tr(
                            "为安全起见，不支持复制包含符号链接或目录联接的文件夹："
                            "{path}",
                            path=entry_path,
                        )
                    )
                if entry.is_dir(follow_symlinks=False):
                    _reject_linked_entries(entry_path)
        except OSError as exc:
            raise FileOperationError(
                i18n.tr("无法完整安全检查要复制的文件夹：{path}", path=folder)
            ) from exc


def _prepare_transfer(
    source_path: str | os.PathLike[str],
    destination_directory: str | os.PathLike[str],
    *,
    operation_label: str,
    reject_linked_entries_in_source: bool = False,
) -> tuple[Path, Path]:
    source = _resolved_existing(source_path)
    destination_dir = _prepare_destination_directory(
        destination_directory,
        operation_label=operation_label,
    )
    destination = destination_dir / source.name
    if reject_linked_entries_in_source and source.is_dir():
        _reject_linked_entries(source)
    if _destination_in_source(source, destination):
        raise FileOperationError(
            i18n.tr(
                "不能把文件夹{operation}到它自身或子文件夹中。",
                operation=operation_label,
            )
        )
    if os.path.normcase(str(destination)) == os.path.normcase(str(source)):
        raise FileOperationError(
            i18n.tr(
                "目标位置与当前位置相同，无需{operation}。",
                operation=operation_label,
            )
        )
    if _entry_exists(destination):
        raise FileOperationError(
            i18n.tr("目标位置已有同名项目：{path}", path=destination)
        )
    return source, destination


def preflight_file_operation(
    source_path: str | os.PathLike[str],
    operation: str,
    *,
    destination_directory: str | os.PathLike[str] = "",
    new_name: str = "",
) -> None:
    """Validate a file operation without terminating processes or changing files."""

    if operation == "rename":
        _prepare_rename(source_path, new_name)
    elif operation == "move":
        _prepare_transfer(
            source_path,
            destination_directory,
            operation_label=i18n.tr("移动"),
        )
    elif operation == "copy":
        _prepare_transfer(
            source_path,
            destination_directory,
            operation_label=i18n.tr("复制"),
            reject_linked_entries_in_source=True,
        )
    else:
        raise FileOperationError(
            i18n.tr("不支持的文件操作：{operation}", operation=operation)
        )


def rename_target(
    source_path: str | os.PathLike[str],
    new_name: str,
) -> Path:
    source, destination = _prepare_rename(source_path, new_name)
    try:
        return source.rename(destination)
    except OSError as exc:
        raise FileOperationError(
            i18n.tr("重命名失败：{message}", message=exc)
        ) from exc


def move_target(
    source_path: str | os.PathLike[str],
    destination_directory: str | os.PathLike[str],
) -> Path:
    source, destination = _prepare_transfer(
        source_path,
        destination_directory,
        operation_label=i18n.tr("移动"),
    )
    try:
        return Path(shutil.move(str(source), str(destination)))
    except OSError as exc:
        raise FileOperationError(
            i18n.tr("移动失败：{message}", message=exc)
        ) from exc


def copy_target(
    source_path: str | os.PathLike[str],
    destination_directory: str | os.PathLike[str],
) -> Path:
    source, destination = _prepare_transfer(
        source_path,
        destination_directory,
        operation_label=i18n.tr("复制"),
        reject_linked_entries_in_source=True,
    )
    try:
        if source.is_dir():
            shutil.copytree(source, destination, copy_function=shutil.copy2)
        else:
            shutil.copy2(source, destination)
    except OSError as exc:
        raise FileOperationError(
            i18n.tr("复制失败：{message}", message=exc)
        ) from exc
    return destination