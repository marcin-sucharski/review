from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from .languages import language_for_path

ReviewKind = Literal["uncommitted", "branch"]
LineKind = Literal["context", "addition", "deletion", "metadata"]
FileStatus = Literal["modified", "added", "deleted", "renamed", "copied", "binary", "mode", "type"]


@dataclass(frozen=True)
class ReviewSource:
    kind: ReviewKind
    target_branch: str | None = None
    base_ref: str | None = None

    def label(self) -> str:
        if self.kind == "branch":
            target = self.target_branch or "unknown"
            return f"branch comparison against {target}"
        return "uncommitted changes"


@dataclass(frozen=True)
class ReviewLine:
    index: int
    kind: LineKind
    text: str
    old_line: int | None = None
    new_line: int | None = None

    @property
    def marker(self) -> str:
        if self.kind == "addition":
            return "+"
        if self.kind == "deletion":
            return "-"
        return " "

    @property
    def primary_line(self) -> int | None:
        return self.new_line if self.new_line is not None else self.old_line

    @property
    def selectable(self) -> bool:
        return self.kind in {"context", "addition", "deletion"}


@dataclass(frozen=True)
class VisibleInterval:
    start: int
    end: int

    def contains(self, index: int) -> bool:
        return self.start <= index <= self.end

    def overlaps_or_touches(self, other: "VisibleInterval") -> bool:
        return self.start <= other.end + 1 and other.start <= self.end + 1

    def merge(self, other: "VisibleInterval") -> "VisibleInterval":
        return VisibleInterval(min(self.start, other.start), max(self.end, other.end))


@dataclass
class ReviewFile:
    path: str
    status: FileStatus
    lines: list[ReviewLine]
    old_path: str | None = None
    language: str = "text"
    binary: bool = False
    metadata: list[str] = field(default_factory=list)
    visible_intervals: list[VisibleInterval] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.language == "text":
            self.language = language_for_path(self.path)
        if not self.visible_intervals:
            self.visible_intervals = initial_visible_intervals(self.lines)

    @property
    def display_path(self) -> str:
        if self.old_path and self.old_path != self.path:
            return f"{self.old_path} -> {self.path}"
        return self.path

    def status_marker(self) -> str:
        return {
            "modified": "M",
            "added": "A",
            "deleted": "D",
            "renamed": "R",
            "copied": "C",
            "binary": "B",
            "mode": "M",
            "type": "T",
        }.get(self.status, "?")

    def is_visible(self, index: int) -> bool:
        return any(interval.contains(index) for interval in self.visible_intervals)

    def add_visible_interval(self, start: int, end: int) -> None:
        if not self.lines:
            return
        start = max(0, start)
        end = min(len(self.lines) - 1, end)
        if start > end:
            return
        intervals = [*self.visible_intervals, VisibleInterval(start, end)]
        intervals.sort(key=lambda interval: interval.start)
        merged: list[VisibleInterval] = []
        for interval in intervals:
            if merged and merged[-1].overlaps_or_touches(interval):
                merged[-1] = merged[-1].merge(interval)
            else:
                merged.append(interval)
        self.visible_intervals = merged

    def first_visible_row(self) -> int | None:
        for interval in self.visible_intervals:
            for row_index in range(interval.start, interval.end + 1):
                if self.lines[row_index].selectable:
                    return row_index
        return None

    def changed_indices(self) -> list[int]:
        return [line.index for line in self.lines if line.kind in {"addition", "deletion"}]


@dataclass(frozen=True)
class ReviewComment:
    id: str
    file_path: str
    start_row: int
    end_row: int
    body: str
    selected_lines: tuple[ReviewLine, ...]
    order: int

    @property
    def sorted_rows(self) -> tuple[int, int]:
        return min(self.start_row, self.end_row), max(self.start_row, self.end_row)


def build_review_lines(old_lines: list[str], new_lines: list[str]) -> list[ReviewLine]:
    matcher = SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    rows: list[ReviewLine] = []

    def add(kind: LineKind, text: str, old_line: int | None, new_line: int | None) -> None:
        rows.append(ReviewLine(len(rows), kind, text, old_line, new_line))

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            for offset, text in enumerate(old_lines[old_start:old_end]):
                add("context", text, old_start + offset + 1, new_start + offset + 1)
        elif tag == "delete":
            for offset, text in enumerate(old_lines[old_start:old_end]):
                add("deletion", text, old_start + offset + 1, None)
        elif tag == "insert":
            for offset, text in enumerate(new_lines[new_start:new_end]):
                add("addition", text, None, new_start + offset + 1)
        elif tag == "replace":
            for offset, text in enumerate(old_lines[old_start:old_end]):
                add("deletion", text, old_start + offset + 1, None)
            for offset, text in enumerate(new_lines[new_start:new_end]):
                add("addition", text, None, new_start + offset + 1)
    return rows


def create_review_file(
    path: str,
    status: FileStatus,
    old_lines: list[str],
    new_lines: list[str],
    *,
    old_path: str | None = None,
    binary: bool = False,
    metadata: list[str] | None = None,
) -> ReviewFile:
    rows = [] if binary else build_review_lines(old_lines, new_lines)
    final_status: FileStatus = "binary" if binary else status
    return ReviewFile(
        path=path,
        old_path=old_path,
        status=final_status,
        language=language_for_path(path),
        lines=rows,
        binary=binary,
        metadata=metadata or [],
    )


def initial_visible_intervals(
    lines: list[ReviewLine],
    *,
    context_radius: int = 20,
    full_file_threshold: int = 180,
) -> list[VisibleInterval]:
    if not lines:
        return []
    if len(lines) <= full_file_threshold:
        return [VisibleInterval(0, len(lines) - 1)]
    changed = [line.index for line in lines if line.kind in {"addition", "deletion"}]
    if not changed:
        return [VisibleInterval(0, min(len(lines) - 1, full_file_threshold - 1))]
    intervals = [
        VisibleInterval(max(0, index - context_radius), min(len(lines) - 1, index + context_radius))
        for index in changed
    ]
    intervals.sort(key=lambda interval: interval.start)
    merged: list[VisibleInterval] = []
    for interval in intervals:
        if merged and merged[-1].overlaps_or_touches(interval):
            merged[-1] = merged[-1].merge(interval)
        else:
            merged.append(interval)
    return merged
