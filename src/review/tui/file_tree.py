from __future__ import annotations

from dataclasses import dataclass, field

from ..diff_model import ReviewFile


@dataclass
class _Directory:
    directories: dict[str, "_Directory"] = field(default_factory=dict)
    files: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class FileTreeRow:
    kind: str
    label: str
    depth: int
    file_index: int | None = None


def build_file_tree(files: list[ReviewFile]) -> list[FileTreeRow]:
    root = _Directory()
    for file_index, file in enumerate(files):
        parts = [part for part in file.path.split("/") if part]
        if not parts:
            root.files.append(file_index)
            continue
        directory = root
        for part in parts[:-1]:
            directory = directory.directories.setdefault(part, _Directory())
        directory.files.append(file_index)
    rows: list[FileTreeRow] = []
    _append_rows(root, files, rows, depth=0)
    return rows


def file_tree_row_index(rows: list[FileTreeRow], file_index: int) -> int:
    for row_index, row in enumerate(rows):
        if row.kind == "file" and row.file_index == file_index:
            return row_index
    return 0


def _append_rows(directory: _Directory, files: list[ReviewFile], rows: list[FileTreeRow], *, depth: int) -> None:
    for name, child in directory.directories.items():
        label, collapsed = _collapse_directory(name, child)
        rows.append(FileTreeRow(kind="directory", label=f"{label}/", depth=depth))
        _append_rows(collapsed, files, rows, depth=depth + 1)
    for file_index in directory.files:
        rows.append(FileTreeRow(kind="file", label=_file_label(files[file_index]), depth=depth, file_index=file_index))


def _collapse_directory(name: str, directory: _Directory) -> tuple[str, _Directory]:
    labels = [name]
    current = directory
    while not current.files and len(current.directories) == 1:
        child_name, child = next(iter(current.directories.items()))
        labels.append(child_name)
        current = child
    return "/".join(labels), current


def _file_label(file: ReviewFile) -> str:
    new_name = file.path.rsplit("/", 1)[-1] if file.path else file.display_path
    if file.old_path and file.old_path != file.path:
        old_name = file.old_path.rsplit("/", 1)[-1]
        if old_name != new_name:
            return f"{old_name} -> {new_name}"
    return new_name
