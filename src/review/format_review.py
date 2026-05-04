from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from .diff_model import ReviewComment, ReviewFile, ReviewLine
from .languages import fence_language
from .review_state import ReviewState

OutputFormat = str


def format_review(state: ReviewState, output_format: OutputFormat = "md") -> str:
    if not state.comments:
        return "No review comments.\n"
    if output_format == "xml":
        return format_review_xml(state)
    if output_format == "md":
        return format_review_markdown(state)
    raise ValueError(f"unsupported output format: {output_format}")


def format_review_xml(state: ReviewState) -> str:
    lines = [
        "<review_feedback>",
        "  <instructions>Use these review comments as feedback on the referenced code changes. For each review_comment, inspect the context and address the message.</instructions>",
        "  <metadata>",
        f"    <repository{_attrs(path=str(state.repository_root))} />",
        f"    <source{_attrs(kind=state.source.kind, target_branch=state.source.target_branch, base_ref=state.source.base_ref)}>{_xml_text(state.source.label())}</source>",
        "  </metadata>",
        "  <review_comments>",
    ]

    comments_by_file = _comments_by_file(state.comments)
    for file in state.files:
        comments = _comments_for_file(comments_by_file, file.path)
        if not comments:
            continue
        lines.extend(_format_file(file, comments))

    lines.extend(["  </review_comments>", "</review_feedback>"])
    return "\n".join(lines) + "\n"


def format_review_markdown(state: ReviewState) -> str:
    lines = [
        f"# Review comments for {state.repository_root}",
        f"## Source: {state.source.label()}",
        "",
    ]

    comments_by_file = _comments_by_file(state.comments)
    for file in state.files:
        comments = _comments_for_file(comments_by_file, file.path)
        if not comments:
            continue
        lines.append(f"### File: {file.display_path}")
        lines.append("")
        for comment in comments:
            lines.extend(_format_markdown_comment(file, comment))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _comments_by_file(comments: list[ReviewComment]) -> dict[str, list[ReviewComment]]:
    comments_by_file: dict[str, list[ReviewComment]] = {}
    for comment in comments:
        comments_by_file.setdefault(comment.file_path, []).append(comment)
    return comments_by_file


def _comments_for_file(comments_by_file: dict[str, list[ReviewComment]], path: str) -> list[ReviewComment]:
    return sorted(comments_by_file.get(path, []), key=lambda comment: (comment.sorted_rows, comment.order))


def _format_file(file: ReviewFile, comments: list[ReviewComment]) -> list[str]:
    attrs = _attrs(
        path=file.path,
        display_path=file.display_path,
        old_path=file.old_path,
    )
    lines = [f"    <file{attrs}>"]
    for comment in comments:
        lines.extend(_format_comment(file, comment))
    lines.append("    </file>")
    return lines


def _format_comment(file: ReviewFile, comment: ReviewComment) -> list[str]:
    reference = _line_reference(comment.selected_lines)
    context_lines = _comment_context_lines(file, comment)
    body = [
        f"      <review_comment{_attrs(id=comment.id)}>",
        "        <location>",
        f"          <line_range{_attrs(**reference.attributes)}>{_xml_text(reference.label)}</line_range>",
        "        </location>",
        f"        <context{_attrs(radius=2)}><![CDATA[{_cdata_text(_format_context_lines(context_lines))}]]></context>",
        f"        <message>{_xml_text(comment.body)}</message>",
        "      </review_comment>",
    ]
    return body


def _format_markdown_comment(file: ReviewFile, comment: ReviewComment) -> list[str]:
    context_lines = [_format_context_line(line) for line in _comment_context_lines(file, comment)]
    body = [_line_reference(comment.selected_lines).label]
    body.extend(_fenced_block(context_lines, fence_language(file.language)))
    body.append("Comment:")
    body.extend(_fenced_block(comment.body.splitlines() or [""], "text", fence_char="~"))
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


@dataclass(frozen=True)
class LineReference:
    label: str
    attributes: dict[str, object | None]


def _line_reference(lines: tuple[ReviewLine, ...]) -> LineReference:
    new_numbers = [line.new_line for line in lines if line.new_line is not None]
    old_numbers = [line.old_line for line in lines if line.old_line is not None]
    has_old_only = any(line.new_line is None and line.old_line is not None for line in lines)
    has_new = bool(new_numbers)

    if has_new and not has_old_only:
        start, end = min(new_numbers), max(new_numbers)
        return LineReference(_range_label("Line", "Lines", start, end), {"side": "new", "start": start, "end": end})
    if old_numbers and not has_new:
        start, end = min(old_numbers), max(old_numbers)
        return LineReference(_range_label("Old line", "Old lines", start, end), {"side": "old", "start": start, "end": end})
    if old_numbers and new_numbers:
        old_start, old_end = min(old_numbers), max(old_numbers)
        new_start, new_end = min(new_numbers), max(new_numbers)
        old = _range_label("Old line", "Old lines", old_start, old_end)
        new = _range_label("New line", "New lines", new_start, new_end)
        return LineReference(
            f"{old}; {new}",
            {"side": "mixed", "old_start": old_start, "old_end": old_end, "new_start": new_start, "new_end": new_end},
        )
    return LineReference("Lines: unavailable", {"side": "unknown"})


def _range_label(single: str, plural: str, start: int, end: int) -> str:
    if start == end:
        return f"{single}: {start}"
    return f"{plural}: {start}-{end}"


def _format_context_lines(lines: list[ReviewLine]) -> str:
    return "\n".join(_format_context_line(line) for line in lines)


def _format_context_line(line: ReviewLine) -> str:
    number = line.primary_line
    number_text = "?" if number is None else str(number)
    return f"{number_text.rjust(4)} {line.marker} {line.text}"


def _fenced_block(lines: list[str], info_string: str, *, fence_char: str = "`") -> list[str]:
    fence = _fence_for(lines, fence_char)
    return [f"{fence}{info_string}", *lines, fence]


def _fence_for(lines: list[str], fence_char: str = "`") -> str:
    longest = 2
    for line in lines:
        current = 0
        for char in line:
            if char == fence_char:
                current += 1
                longest = max(longest, current)
            else:
                current = 0
    return fence_char * (longest + 1)


def _attrs(**attrs: object | None) -> str:
    parts = []
    for name, value in attrs.items():
        if value is not None:
            parts.append(f" {name}={quoteattr(_xml_safe_text(str(value)))}")
    return "".join(parts)


def _xml_text(text: object) -> str:
    return escape(_xml_safe_text(str(text)))


def _cdata_text(text: str) -> str:
    return _xml_safe_text(text).replace("]]>", "]]]]><![CDATA[>")


def _xml_safe_text(text: str) -> str:
    return "".join(char if _is_xml_char(char) else "\uFFFD" for char in text)


def _is_xml_char(char: str) -> bool:
    codepoint = ord(char)
    return (
        codepoint in {0x09, 0x0A, 0x0D}
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def write_review_to_path(state: ReviewState, path: Path, output_format: OutputFormat = "md") -> None:
    path.write_text(format_review(state, output_format), encoding="utf-8")
