from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .diff_model import ReviewComment, ReviewFile, ReviewLine
from .languages import fence_language
from .review_state import ReviewState


def format_review(state: ReviewState) -> str:
    if not state.comments:
        return "No review comments.\n"

    lines = [
        f"Review comments for {state.repository_root}",
        f"Source: {state.source.label()}",
        "",
    ]

    comments_by_file: dict[str, list[ReviewComment]] = defaultdict(list)
    for comment in state.comments:
        comments_by_file[comment.file_path].append(comment)

    for file in state.files:
        comments = sorted(comments_by_file.get(file.path, []), key=lambda comment: (comment.sorted_rows, comment.order))
        if not comments:
            continue
        lines.append(f"File: {file.display_path}")
        lines.append("")
        for comment in comments:
            lines.extend(_format_comment(file, comment))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _format_comment(file: ReviewFile, comment: ReviewComment) -> list[str]:
    body: list[str] = []
    body.append(_line_label(comment.selected_lines))
    context_lines = _comment_context_lines(file, comment)
    code_lines = [_format_context_line(line) for line in context_lines]
    fence = _fence_for(code_lines)
    body.append(f"{fence}{fence_language(file.language)}")
    body.extend(code_lines)
    body.append(fence)
    body.append("Comment:")
    comment_lines = comment.body.splitlines() or [""]
    comment_fence = _fence_for(comment_lines, "~")
    body.append(f"{comment_fence}text")
    body.extend(comment_lines)
    body.append(comment_fence)
    return body


def _comment_context_lines(file: ReviewFile, comment: ReviewComment, radius: int = 2) -> list[ReviewLine]:
    if not file.lines:
        return list(comment.selected_lines)
    start, end = comment.sorted_rows
    start = max(0, start - radius)
    end = min(len(file.lines) - 1, end + radius)
    if start > end:
        return list(comment.selected_lines)
    return file.lines[start : end + 1]


def _line_label(lines: tuple[ReviewLine, ...]) -> str:
    new_numbers = [line.new_line for line in lines if line.new_line is not None]
    old_numbers = [line.old_line for line in lines if line.old_line is not None]
    has_old_only = any(line.new_line is None and line.old_line is not None for line in lines)
    has_new = bool(new_numbers)

    if has_new and not has_old_only:
        return _range_label("Line", "Lines", min(new_numbers), max(new_numbers))
    if old_numbers and not has_new:
        return _range_label("Old line", "Old lines", min(old_numbers), max(old_numbers))
    if old_numbers and new_numbers:
        old = _range_label("Old line", "Old lines", min(old_numbers), max(old_numbers))
        new = _range_label("New line", "New lines", min(new_numbers), max(new_numbers))
        return f"{old}; {new}"
    return "Lines: unavailable"


def _range_label(single: str, plural: str, start: int, end: int) -> str:
    if start == end:
        return f"{single}: {start}"
    return f"{plural}: {start}-{end}"


def _format_context_line(line: ReviewLine) -> str:
    number = line.primary_line
    number_text = "?" if number is None else str(number)
    return f"{number_text.rjust(4)} {line.marker} {line.text}"


def _fence_for(code_lines: list[str], fence_char: str = "`") -> str:
    longest = 2
    for line in code_lines:
        current = 0
        for char in line:
            if char == fence_char:
                current += 1
                longest = max(longest, current)
            else:
                current = 0
    return fence_char * (longest + 1)


def write_review_to_path(state: ReviewState, path: Path) -> None:
    path.write_text(format_review(state), encoding="utf-8")
