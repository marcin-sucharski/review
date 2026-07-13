from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from .languages import language_for_path

ReviewKind = Literal["uncommitted", "branch"]
LineKind = Literal["context", "addition", "deletion", "metadata"]
FileStatus = Literal["modified", "added", "deleted", "renamed", "copied", "binary", "mode", "type"]
LARGE_DIFF_AUTOJUNK_THRESHOLD = 1_000
COARSE_DIFF_LINE_THRESHOLD = 20_000
LARGE_DIFF_RESYNC_WINDOW = 64


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
    non_selectable_rows: frozenset[int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.language == "text":
            self.language = language_for_path(self.path)
        if not self.visible_intervals:
            self.visible_intervals = initial_visible_intervals(self.lines)
        self.non_selectable_rows = frozenset(index for index, line in enumerate(self.lines) if not line.selectable)

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
    rows: list[ReviewLine] = []

    def add(kind: LineKind, text: str, old_line: int | None, new_line: int | None) -> None:
        rows.append(ReviewLine(len(rows), kind, text, old_line, new_line))

    large_input = max(len(old_lines), len(new_lines)) >= LARGE_DIFF_AUTOJUNK_THRESHOLD
    prefix_count = _common_prefix_length(old_lines, new_lines) if large_input else 0
    suffix_count = _common_suffix_length(old_lines, new_lines, prefix_count) if large_input else 0

    for offset, text in enumerate(old_lines[:prefix_count]):
        add("context", text, offset + 1, offset + 1)

    old_middle_end = len(old_lines) - suffix_count if suffix_count else len(old_lines)
    new_middle_end = len(new_lines) - suffix_count if suffix_count else len(new_lines)
    old_middle = old_lines[prefix_count:old_middle_end]
    new_middle = new_lines[prefix_count:new_middle_end]

    large_matching = len(old_middle) + len(new_middle) > COARSE_DIFF_LINE_THRESHOLD
    if large_matching:
        opcodes = [("replace", 0, len(old_middle), 0, len(new_middle))]
    else:
        autojunk = max(len(old_middle), len(new_middle)) >= LARGE_DIFF_AUTOJUNK_THRESHOLD
        large_matching = autojunk
        opcodes = SequenceMatcher(a=old_middle, b=new_middle, autojunk=autojunk).get_opcodes()
    if large_matching:
        opcodes = _resynchronize_large_replacements(old_middle, new_middle, opcodes)

    for tag, old_start, old_end, new_start, new_end in opcodes:
        if tag == "equal":
            for offset, text in enumerate(old_middle[old_start:old_end]):
                add("context", text, prefix_count + old_start + offset + 1, prefix_count + new_start + offset + 1)
        elif tag == "delete":
            for offset, text in enumerate(old_middle[old_start:old_end]):
                add("deletion", text, prefix_count + old_start + offset + 1, None)
        elif tag == "insert":
            for offset, text in enumerate(new_middle[new_start:new_end]):
                add("addition", text, None, prefix_count + new_start + offset + 1)
        elif tag == "replace":
            for offset, text in enumerate(old_middle[old_start:old_end]):
                add("deletion", text, prefix_count + old_start + offset + 1, None)
            for offset, text in enumerate(new_middle[new_start:new_end]):
                add("addition", text, None, prefix_count + new_start + offset + 1)

    if suffix_count:
        old_suffix_start = len(old_lines) - suffix_count
        new_suffix_start = len(new_lines) - suffix_count
        for offset, text in enumerate(old_lines[old_suffix_start:]):
            add("context", text, old_suffix_start + offset + 1, new_suffix_start + offset + 1)
    return rows


def _common_prefix_length(old_lines: list[str], new_lines: list[str]) -> int:
    limit = min(len(old_lines), len(new_lines))
    index = 0
    while index < limit and old_lines[index] == new_lines[index]:
        index += 1
    return index


def _common_suffix_length(old_lines: list[str], new_lines: list[str], prefix_count: int) -> int:
    limit = min(len(old_lines), len(new_lines)) - prefix_count
    count = 0
    while count < limit and old_lines[len(old_lines) - count - 1] == new_lines[len(new_lines) - count - 1]:
        count += 1
    return count


def _resynchronize_large_replacements(
    old_lines: list[str],
    new_lines: list[str],
    opcodes: list[tuple[str, int, int, int, int]],
) -> list[tuple[str, int, int, int, int]]:
    refined: list[tuple[str, int, int, int, int]] = []
    for opcode in opcodes:
        tag, old_start, old_end, new_start, new_end = opcode
        if tag != "replace":
            refined.append(opcode)
            continue
        refined.extend(_bounded_alignment_opcodes(old_lines, new_lines, old_start, old_end, new_start, new_end))
    return refined


def _bounded_alignment_opcodes(
    old_lines: list[str],
    new_lines: list[str],
    old_start: int,
    old_end: int,
    new_start: int,
    new_end: int,
) -> list[tuple[str, int, int, int, int]]:
    opcodes: list[tuple[str, int, int, int, int]] = []
    old_index = old_start
    new_index = new_start
    while old_index < old_end and new_index < new_end:
        if old_lines[old_index] == new_lines[new_index]:
            equal_old_start = old_index
            equal_new_start = new_index
            while old_index < old_end and new_index < new_end and old_lines[old_index] == new_lines[new_index]:
                old_index += 1
                new_index += 1
            opcodes.append(("equal", equal_old_start, old_index, equal_new_start, new_index))
            continue

        alignment = _next_bounded_alignment(old_lines, new_lines, old_index, old_end, new_index, new_end)
        if alignment is None:
            opcodes.append(("replace", old_index, old_end, new_index, new_end))
            old_index = old_end
            new_index = new_end
            break
        next_old, next_new = alignment
        tag = "replace"
        if next_old == old_index:
            tag = "insert"
        elif next_new == new_index:
            tag = "delete"
        opcodes.append((tag, old_index, next_old, new_index, next_new))
        old_index = next_old
        new_index = next_new

    if old_index < old_end:
        opcodes.append(("delete", old_index, old_end, new_index, new_index))
    elif new_index < new_end:
        opcodes.append(("insert", old_index, old_index, new_index, new_end))
    return opcodes


def _next_bounded_alignment(
    old_lines: list[str],
    new_lines: list[str],
    old_start: int,
    old_end: int,
    new_start: int,
    new_end: int,
) -> tuple[int, int] | None:
    old_stop = min(old_end, old_start + LARGE_DIFF_RESYNC_WINDOW + 1)
    new_stop = min(new_end, new_start + LARGE_DIFF_RESYNC_WINDOW + 1)
    new_positions: dict[str, int] = {}
    for index in range(new_start, new_stop):
        new_positions.setdefault(new_lines[index], index)

    candidates = []
    for old_index in range(old_start, old_stop):
        new_index = new_positions.get(old_lines[old_index])
        if new_index is None:
            continue
        distance = old_index - old_start + new_index - new_start
        candidates.append((distance, max(old_index - old_start, new_index - new_start), old_index, new_index))
    if not candidates:
        return None
    _, _, old_index, new_index = min(candidates)
    return old_index, new_index


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
