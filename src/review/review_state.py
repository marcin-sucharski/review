from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .diff_model import ReviewComment, ReviewFile, ReviewLine, ReviewSource


@dataclass(frozen=True)
class Expansion:
    id: str
    file_path: str
    direction: str
    gap_start: int
    gap_end: int
    reveal_start: int
    reveal_end: int

    @property
    def remaining_count(self) -> int:
        return self.gap_end - self.gap_start + 1

    @property
    def reveal_count(self) -> int:
        return self.reveal_end - self.reveal_start + 1

    def label(self) -> str:
        if self.remaining_count <= self.reveal_count:
            suffix = "remaining line" if self.remaining_count == 1 else "remaining lines"
            return f"Show {self.remaining_count} {suffix} {self.direction}"
        return f"Show {self.reveal_count} lines {self.direction}"


@dataclass(frozen=True)
class DocumentItem:
    kind: str
    file_index: int
    file_path: str
    text: str = ""
    row_index: int | None = None
    line: ReviewLine | None = None
    expansion: Expansion | None = None
    comment: ReviewComment | None = None

    @property
    def selectable(self) -> bool:
        if self.kind == "code":
            return self.line is not None and self.line.selectable
        return self.kind in {"comment", "expansion"}


class ReviewState:
    def __init__(self, repository_root: Path, source: ReviewSource, files: list[ReviewFile]):
        self.repository_root = Path(repository_root)
        self.source = source
        self.files = files
        self.comments: list[ReviewComment] = []
        self.focus = "file"
        self.file_pane_index = 0
        self.selection_kind = "metadata"
        self.selected_file_path: str | None = None
        self.selected_row: int | None = None
        self.anchor_row: int | None = None
        self.active_row: int | None = None
        self.selected_expansion_id: str | None = None
        self.selected_comment_id: str | None = None
        self._comment_counter = 0
        self._document_cache_key: tuple | None = None
        self._document_cache: list[DocumentItem] = []
        self._initialize_selection()

    def _initialize_selection(self) -> None:
        for index, file in enumerate(self.files):
            first = file.first_visible_row()
            if first is not None:
                self._select_code(index, file.path, first)
                return
        if self.files:
            self._select_metadata(0, self.files[0].path)

    def file_by_path(self, path: str) -> ReviewFile:
        for file in self.files:
            if file.path == path:
                return file
        raise KeyError(path)

    def file_index(self, path: str) -> int:
        for index, file in enumerate(self.files):
            if file.path == path:
                return index
        raise KeyError(path)

    def comments_for_file(self, path: str) -> list[ReviewComment]:
        return sorted(
            [comment for comment in self.comments if comment.file_path == path],
            key=lambda comment: (comment.sorted_rows, comment.order),
        )

    def document_items(self) -> list[DocumentItem]:
        self._ensure_default_visibility()
        cache_key = self._document_signature()
        if cache_key == self._document_cache_key:
            return self._document_cache

        comments_by_file = self._comments_by_file()
        items: list[DocumentItem] = []
        for file_index, file in enumerate(self.files):
            items.extend(self._document_items_for_file(file_index, file, comments_by_file.get(file.path, [])))
        self._document_cache_key = cache_key
        self._document_cache = items
        return items

    def _comments_by_file(self) -> dict[str, list[ReviewComment]]:
        comments_by_file: dict[str, list[ReviewComment]] = {}
        comments = sorted(self.comments, key=lambda comment: (comment.file_path, comment.sorted_rows, comment.order))
        for comment in comments:
            comments_by_file.setdefault(comment.file_path, []).append(comment)
        return comments_by_file

    def _document_items_for_file(
        self,
        file_index: int,
        file: ReviewFile,
        file_comments: list[ReviewComment],
    ) -> list[DocumentItem]:
        items = [
            self._file_header_item(file_index, file, len(file_comments)),
            *self._metadata_items(file_index, file),
        ]
        if file.binary:
            items.append(self._metadata_item(file_index, file, f"Binary file changed: {file.display_path}"))
            return items
        items.extend(self._visible_file_items(file_index, file, file_comments))
        return items

    def _file_header_item(self, file_index: int, file: ReviewFile, comment_count: int) -> DocumentItem:
        suffix = f" ({comment_count} comments)" if comment_count else ""
        return self._item(
            "file_header",
            file_index,
            file,
            text=f"{file.status_marker()} {file.display_path}{suffix}",
        )

    def _metadata_items(self, file_index: int, file: ReviewFile) -> list[DocumentItem]:
        return [self._metadata_item(file_index, file, metadata) for metadata in file.metadata]

    def _metadata_item(self, file_index: int, file: ReviewFile, text: str) -> DocumentItem:
        return self._item("metadata", file_index, file, text=text)

    def _visible_file_items(
        self,
        file_index: int,
        file: ReviewFile,
        file_comments: list[ReviewComment],
    ) -> list[DocumentItem]:
        items: list[DocumentItem] = []
        comments_by_end_row = self._comments_by_end_row(file_comments)
        intervals = sorted(file.visible_intervals, key=lambda interval: interval.start)
        previous_end = -1
        for interval_index, interval in enumerate(intervals):
            if interval.start > previous_end + 1:
                direction = "above" if interval_index == 0 else "below"
                items.append(self._expansion_item(file_index, file, direction, previous_end + 1, interval.start - 1))
            items.extend(self._visible_interval_items(file_index, file, interval.start, interval.end, comments_by_end_row))
            previous_end = interval.end
        if file.lines and previous_end < len(file.lines) - 1:
            items.append(self._expansion_item(file_index, file, "below", previous_end + 1, len(file.lines) - 1))
        return items

    @staticmethod
    def _comments_by_end_row(file_comments: list[ReviewComment]) -> dict[int, list[ReviewComment]]:
        comments_by_end_row: dict[int, list[ReviewComment]] = {}
        for comment in file_comments:
            comments_by_end_row.setdefault(comment.sorted_rows[1], []).append(comment)
        return comments_by_end_row

    def _visible_interval_items(
        self,
        file_index: int,
        file: ReviewFile,
        start: int,
        end: int,
        comments_by_end_row: dict[int, list[ReviewComment]],
    ) -> list[DocumentItem]:
        items: list[DocumentItem] = []
        for row_index in range(start, end + 1):
            items.append(self._item("code", file_index, file, row_index=row_index, line=file.lines[row_index]))
            for comment in comments_by_end_row.get(row_index, []):
                items.append(self._item("comment", file_index, file, comment=comment, text=comment.body))
        return items

    def _expansion_item(
        self,
        file_index: int,
        file: ReviewFile,
        direction: str,
        gap_start: int,
        gap_end: int,
    ) -> DocumentItem:
        expansion = self._expansion_for_gap(file.path, direction, gap_start, gap_end)
        return self._item("expansion", file_index, file, expansion=expansion)

    @staticmethod
    def _item(kind: str, file_index: int, file: ReviewFile, **kwargs) -> DocumentItem:
        return DocumentItem(kind=kind, file_index=file_index, file_path=file.path, **kwargs)

    def _ensure_default_visibility(self) -> None:
        changed = False
        for file in self.files:
            if not file.visible_intervals and file.lines:
                file.add_visible_interval(0, min(20, len(file.lines) - 1))
                changed = True
        if changed:
            self._document_cache_key = None

    def _document_signature(self) -> tuple:
        file_signature = tuple(
            (
                file.path,
                file.old_path,
                file.status,
                file.binary,
                tuple(file.metadata),
                len(file.lines),
                tuple((interval.start, interval.end) for interval in file.visible_intervals),
            )
            for file in self.files
        )
        comment_signature = tuple(
            (comment.id, comment.file_path, comment.start_row, comment.end_row, comment.body, comment.order)
            for comment in self.comments
        )
        return file_signature, comment_signature

    @staticmethod
    def _expansion_for_gap(file_path: str, direction: str, gap_start: int, gap_end: int) -> Expansion:
        if direction == "above":
            reveal_end = gap_end
            reveal_start = max(gap_start, gap_end - 19)
        else:
            reveal_start = gap_start
            reveal_end = min(gap_end, gap_start + 19)
        return Expansion(
            id=f"{file_path}:{direction}:{gap_start}:{gap_end}",
            file_path=file_path,
            direction=direction,
            gap_start=gap_start,
            gap_end=gap_end,
            reveal_start=reveal_start,
            reveal_end=reveal_end,
        )

    def selected_document_index(self) -> int:
        active = self.active_document_index()
        if active is not None:
            return active
        items = self.document_items()
        for index, item in enumerate(items):
            if item.selectable:
                self.select_document_index(index)
                return index
        return 0

    def active_document_index(self) -> int | None:
        items = self.document_items()
        for index, item in enumerate(items):
            if self._item_is_selected(item):
                return index
        return None

    def _item_is_selected(self, item: DocumentItem) -> bool:
        if self.selection_kind == "code":
            return (
                item.kind == "code"
                and item.file_path == self.selected_file_path
                and item.row_index == self.active_row
            )
        if self.selection_kind == "expansion":
            return item.kind == "expansion" and item.expansion is not None and item.expansion.id == self.selected_expansion_id
        if self.selection_kind == "comment":
            return item.kind == "comment" and item.comment is not None and item.comment.id == self.selected_comment_id
        return False

    def _select_file_context(self, file_index: int, file_path: str) -> None:
        self.file_pane_index = file_index
        self.selected_file_path = file_path

    def _clear_row_selection(self) -> None:
        self.selected_row = None
        self.anchor_row = None
        self.active_row = None

    def _clear_item_selection(self) -> None:
        self.selected_expansion_id = None
        self.selected_comment_id = None

    def _select_code(self, file_index: int, file_path: str, active_row: int, *, anchor_row: int | None = None) -> None:
        self.selection_kind = "code"
        self._select_file_context(file_index, file_path)
        self.selected_row = active_row
        self.anchor_row = active_row if anchor_row is None else anchor_row
        self.active_row = active_row
        self._clear_item_selection()

    def _select_metadata(self, file_index: int, file_path: str) -> None:
        self.selection_kind = "metadata"
        self._select_file_context(file_index, file_path)
        self._clear_item_selection()
        self._clear_row_selection()

    def _select_expansion(self, file_index: int, file_path: str, expansion_id: str) -> None:
        self.selection_kind = "expansion"
        self._select_file_context(file_index, file_path)
        self.selected_expansion_id = expansion_id
        self.selected_comment_id = None
        self._clear_row_selection()

    def _select_comment(self, file_index: int, file_path: str, comment: ReviewComment) -> None:
        self.selection_kind = "comment"
        self._select_file_context(file_index, file_path)
        self.selected_comment_id = comment.id
        self.selected_row = comment.sorted_rows[1]
        self.anchor_row = None
        self.active_row = None
        self.selected_expansion_id = None

    def select_document_index(self, index: int) -> None:
        items = self.document_items()
        if not items:
            return
        index = max(0, min(index, len(items) - 1))
        item = items[index]
        if item.kind == "code" and item.row_index is not None and item.line is not None and item.line.selectable:
            self._select_code(item.file_index, item.file_path, item.row_index)
        elif item.kind == "expansion" and item.expansion is not None:
            self._select_expansion(item.file_index, item.file_path, item.expansion.id)
        elif item.kind == "comment" and item.comment is not None:
            self._select_comment(item.file_index, item.file_path, item.comment)
        elif item.file_path:
            self._select_metadata(item.file_index, item.file_path)

    def select_file(self, path: str) -> int:
        file_index = self.file_index(path)
        file = self.files[file_index]
        first = file.first_visible_row()
        if first is not None:
            self._select_code(file_index, file.path, first)
        else:
            self._select_metadata(file_index, file.path)
        for index, item in enumerate(self.document_items()):
            if item.kind == "file_header" and item.file_path == path:
                return index
        return 0

    def move_file_selection(self, delta: int) -> int:
        if not self.files:
            return 0
        self.file_pane_index = max(0, min(len(self.files) - 1, self.file_pane_index + delta))
        return self.select_file(self.files[self.file_pane_index].path)

    def move_selection(self, delta: int) -> int:
        items = self.document_items()
        selectable = [index for index, item in enumerate(items) if item.selectable]
        if not selectable:
            return 0
        current = self.active_document_index()
        if current is None:
            current = self._metadata_document_index(items)
        if current is None:
            current = selectable[0]
        if current in selectable:
            position = selectable.index(current)
        elif delta > 0:
            position = next((index for index, item_index in enumerate(selectable) if item_index > current), None)
            if position is None:
                return current
            position = min(len(selectable) - 1, position + delta - 1)
            self.select_document_index(selectable[position])
            return selectable[position]
        elif delta < 0:
            before = [index for index, item_index in enumerate(selectable) if item_index < current]
            if not before:
                return current
            position = max(0, before[-1] + delta + 1)
            self.select_document_index(selectable[position])
            return selectable[position]
        else:
            position = 0
        position = max(0, min(len(selectable) - 1, position + delta))
        self.select_document_index(selectable[position])
        return selectable[position]

    def _metadata_document_index(self, items: list[DocumentItem]) -> int | None:
        if self.selected_file_path is None:
            return None
        for index, item in enumerate(items):
            if item.kind == "file_header" and item.file_path == self.selected_file_path:
                return index
        return None

    def extend_selection(self, delta: int) -> int:
        if self.selection_kind != "code" or self.selected_file_path is None or self.active_row is None:
            return self.move_selection(delta)
        file = self.file_by_path(self.selected_file_path)
        anchor = self.anchor_row if self.anchor_row is not None else self.active_row
        visible_code_rows = self._visible_selectable_rows_in_anchor_interval(file, anchor)
        if not visible_code_rows:
            return self.selected_document_index()
        try:
            position = visible_code_rows.index(self.active_row)
        except ValueError:
            position = 0
        position = max(0, min(len(visible_code_rows) - 1, position + delta))
        if self.anchor_row is None:
            self.anchor_row = self.active_row
        self.active_row = visible_code_rows[position]
        self.selected_row = self.active_row
        return self.selected_document_index()

    def selected_range(self) -> tuple[int, int] | None:
        if self.selection_kind != "code" or self.anchor_row is None or self.active_row is None:
            return None
        return min(self.anchor_row, self.active_row), max(self.anchor_row, self.active_row)

    def is_row_in_selection(self, file_path: str, row_index: int) -> bool:
        if file_path != self.selected_file_path:
            return False
        selected = self.selected_range()
        if selected is None:
            return False
        file = self.file_by_path(file_path)
        return row_index in self._contiguous_visible_selection_rows(file, selected[0], selected[1])

    def add_comment(self, body: str) -> ReviewComment | None:
        if self.selected_file_path is None:
            return None
        selected = self.selected_range()
        if selected is None or not body.strip():
            return None
        file = self.file_by_path(self.selected_file_path)
        start, end = selected
        selected_rows = self._contiguous_visible_selection_rows(file, start, end)
        selected_lines = tuple(file.lines[index] for index in selected_rows)
        if not selected_lines:
            return None
        self._comment_counter += 1
        comment = ReviewComment(
            id=f"c{self._comment_counter}",
            file_path=file.path,
            start_row=start,
            end_row=end,
            body=body.rstrip(),
            selected_lines=selected_lines,
            order=self._comment_counter,
        )
        self.comments.append(comment)
        return comment

    def comment_for_selection(self) -> ReviewComment | None:
        if self.selection_kind == "comment" and self.selected_comment_id is not None:
            for comment in self.comments:
                if comment.id == self.selected_comment_id:
                    return comment
            return None
        if self.selected_file_path is None or self.active_row is None:
            return None
        candidates = [
            comment
            for comment in self.comments_for_file(self.selected_file_path)
            if comment.sorted_rows[0] <= self.active_row <= comment.sorted_rows[1]
        ]
        return candidates[0] if candidates else None

    def update_comment(self, comment_id: str, body: str) -> ReviewComment | None:
        body = body.rstrip()
        if not body.strip():
            return None
        for index, comment in enumerate(self.comments):
            if comment.id == comment_id:
                updated = ReviewComment(
                    id=comment.id,
                    file_path=comment.file_path,
                    start_row=comment.start_row,
                    end_row=comment.end_row,
                    body=body,
                    selected_lines=comment.selected_lines,
                    order=comment.order,
                )
                self.comments[index] = updated
                return updated
        return None

    def delete_comment(self, comment_id: str) -> bool:
        before = len(self.comments)
        self.comments = [comment for comment in self.comments if comment.id != comment_id]
        deleted = len(self.comments) != before
        if deleted and self.selected_comment_id == comment_id:
            self.selection_kind = "metadata"
            self._clear_item_selection()
            self._clear_row_selection()
        return deleted

    def select_range(self, file_path: str, anchor_row: int, active_row: int) -> int:
        file = self.file_by_path(file_path)
        start, end = min(anchor_row, active_row), max(anchor_row, active_row)
        if not self._contiguous_visible_selection_rows(file, start, end):
            return self.selected_document_index()
        self._select_code(self.file_index(file.path), file.path, active_row, anchor_row=anchor_row)
        return self.selected_document_index()

    @staticmethod
    def _visible_selectable_rows_in_anchor_interval(file: ReviewFile, anchor_row: int | None) -> list[int]:
        if anchor_row is None:
            return []
        for interval in file.visible_intervals:
            if interval.start <= anchor_row <= interval.end:
                if not file.lines[anchor_row].selectable:
                    return []
                start = anchor_row
                while start > interval.start and file.lines[start - 1].selectable:
                    start -= 1
                end = anchor_row
                while end < interval.end and file.lines[end + 1].selectable:
                    end += 1
                return list(range(start, end + 1))
        return []

    @staticmethod
    def _contiguous_visible_selection_rows(file: ReviewFile, start: int, end: int) -> list[int]:
        for interval in file.visible_intervals:
            if interval.start <= start <= end <= interval.end:
                rows = list(range(start, end + 1))
                if all(file.lines[row].selectable for row in rows):
                    return rows
                return []
        return []

    def expand_context(self, expansion_id: str) -> int:
        for item_index, item in enumerate(self.document_items()):
            if item.kind == "expansion" and item.expansion is not None and item.expansion.id == expansion_id:
                expansion = item.expansion
                file = self.file_by_path(expansion.file_path)
                file.add_visible_interval(expansion.reveal_start, expansion.reveal_end)
                row = self._first_selectable_row_between(file, expansion.reveal_start, expansion.reveal_end)
                if row is None:
                    row = file.first_visible_row()
                file_index = self.file_index(file.path)
                if row is None:
                    self._select_metadata(file_index, file.path)
                else:
                    self._select_code(file_index, file.path, row)
                return self.selected_document_index()
        return self.selected_document_index()

    @staticmethod
    def _first_selectable_row_between(file: ReviewFile, start: int, end: int) -> int | None:
        if not file.lines:
            return None
        start = max(0, start)
        end = min(len(file.lines) - 1, end)
        for row_index in range(start, end + 1):
            if file.lines[row_index].selectable:
                return row_index
        return None

    def activate_selection(self) -> str:
        if self.selection_kind == "expansion" and self.selected_expansion_id:
            self.expand_context(self.selected_expansion_id)
            return "expanded"
        if self.selection_kind == "comment" and self.selected_comment_id:
            return "edit-comment"
        if self.selection_kind == "code":
            if self.selected_file_path is not None and self.active_row is not None:
                file = self.file_by_path(self.selected_file_path)
                if 0 <= self.active_row < len(file.lines) and file.lines[self.active_row].selectable:
                    return "comment"
            self.selection_kind = "metadata"
            return "none"
        return "none"

    def file_for_document_index(self, index: int) -> str | None:
        items = self.document_items()
        if not items:
            return None
        index = max(0, min(index, len(items) - 1))
        for item in reversed(items[: index + 1]):
            if item.file_path:
                return item.file_path
        return items[index].file_path

    def update_file_highlight_for_document_index(self, index: int) -> None:
        path = self.file_for_document_index(index)
        if path is not None:
            self.file_pane_index = self.file_index(path)
