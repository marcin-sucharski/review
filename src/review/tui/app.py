from __future__ import annotations

import curses
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ..diff_model import ReviewComment, ReviewLine
from ..review_state import DocumentItem, ReviewState
from .file_tree import FileTreeRow, build_file_tree, file_tree_row_index
from .highlight import syntax_spans


Focus = Literal["file", "comments", "review"]
CommandHandler = Callable[[], None]
GUTTER_WIDTH = 8
INITIAL_STATUS_MESSAGE = "Tab switches panes. T toggles left pane. z centers code. :q quits."
NO_SELECTED_COMMENT_MESSAGE = "No comment is attached to the selected line."
INTERRUPT_CONFIRMATION_MESSAGE = "Press Ctrl+C again to quit review."
SEARCH_CANCELLED_MESSAGE = "Search cancelled."
MOUSE_SCROLL_LINES = 3
SHIFT_UP_KEYS = {getattr(curses, "KEY_SR", -1000), getattr(curses, "KEY_SUP", -1001)}
SHIFT_DOWN_KEYS = {getattr(curses, "KEY_SF", -1002), getattr(curses, "KEY_SDOWN", -1003)}
COMMENT_WORD_LEFT_KEY = "comment-word-left"
COMMENT_WORD_RIGHT_KEY = "comment-word-right"
COMMENT_ESCAPE_TIMEOUT_MS = 35
BOLD_ROLES = {"keyword", "tag", "heading", "error", "rail", "emphasis"}
BACKGROUND_COLORS = {
    "addition": (194, curses.COLOR_GREEN),
    "addition-selection": (193, curses.COLOR_GREEN),
    "deletion": (224, curses.COLOR_RED),
    "deletion-selection": (223, curses.COLOR_RED),
    "comment": (230, curses.COLOR_YELLOW),
    "search": (226, curses.COLOR_YELLOW),
    "selection": (229, curses.COLOR_WHITE),
}
FOREGROUND_WITH_BACKGROUND = {
    "plain": curses.COLOR_BLACK,
    "line-number": curses.COLOR_BLACK,
    "rail": curses.COLOR_YELLOW,
    "punctuation": curses.COLOR_BLACK,
    "muted": curses.COLOR_BLACK,
    "keyword": curses.COLOR_BLUE,
    "function": curses.COLOR_MAGENTA,
    "builtin": curses.COLOR_MAGENTA,
    "type": curses.COLOR_BLUE,
    "string": curses.COLOR_MAGENTA,
    "number": curses.COLOR_BLUE,
    "comment": curses.COLOR_BLUE,
    "tag": curses.COLOR_MAGENTA,
    "attribute": curses.COLOR_BLUE,
    "heading": curses.COLOR_MAGENTA,
    "emphasis": curses.COLOR_BLUE,
    "operator": curses.COLOR_BLACK,
    "warning": curses.COLOR_BLACK,
    "error": curses.COLOR_RED,
}
FOREGROUND_DEFAULT = {
    "header": curses.COLOR_CYAN,
    "warning": curses.COLOR_YELLOW,
    "link": curses.COLOR_BLUE,
    "muted": curses.COLOR_BLUE,
    "line-number": curses.COLOR_BLUE,
    "rail": curses.COLOR_YELLOW,
    "keyword": curses.COLOR_BLUE,
    "function": curses.COLOR_MAGENTA,
    "builtin": curses.COLOR_MAGENTA,
    "type": curses.COLOR_CYAN,
    "string": curses.COLOR_GREEN,
    "number": curses.COLOR_CYAN,
    "comment": curses.COLOR_BLUE,
    "tag": curses.COLOR_MAGENTA,
    "attribute": curses.COLOR_BLUE,
    "heading": curses.COLOR_MAGENTA,
    "emphasis": curses.COLOR_BLUE,
    "operator": curses.COLOR_RED,
    "error": curses.COLOR_RED,
}


@dataclass(frozen=True)
class DrawFrame:
    active_index: int | None
    selected_file_path: str | None
    selected_rows: frozenset[int]
    comment_input_row: int | None
    comment_ranges: dict[str, tuple[tuple[int, int], ...]]

    def is_selected_row(self, file_path: str, row_index: int | None) -> bool:
        return row_index is not None and file_path == self.selected_file_path and row_index in self.selected_rows

    def has_comment_range(self, file_path: str, row_index: int | None) -> bool:
        if row_index is None:
            return False
        return any(start <= row_index <= end for start, end in self.comment_ranges.get(file_path, ()))


@dataclass(frozen=True)
class CommentPaneRow:
    kind: Literal["file", "comment"]
    file_index: int
    file_path: str
    text: str = ""
    comment: ReviewComment | None = None


@dataclass(frozen=True)
class ScrollRegion:
    visible_height: int
    footer_y: int | None


class ReviewApp:
    def __init__(self, state: ReviewState):
        self.state = state
        self.focus: Focus = "review"
        self.file_scroll = 0
        self.comment_scroll = 0
        self.comment_pane_index = 0
        self.review_scroll = 0
        self.command_mode = False
        self.command_buffer = ""
        self.search_mode = False
        self.search_buffer = ""
        self.search_query = ""
        self.comment_mode = False
        self.comment_buffer = ""
        self.comment_cursor_index = 0
        self.comment_cursor_goal_column: int | None = None
        self.editing_comment_id: str | None = None
        self.status_message = INITIAL_STATUS_MESSAGE
        self.quit_requested = False
        self.file_pane_visible = False
        self.interrupt_armed = False
        self.mouse_drag_anchor: tuple[str, int] | None = None
        self.screen_map: dict[int, int] = {}
        self.comment_cursor: tuple[int, int] | None = None
        self.left_width = 32
        self.content_height = 0
        self.review_width = 80
        self._color_pairs: dict[tuple[int, int], int] = {}
        self._next_color_pair = 1
        self.command_handlers = self._build_command_handlers()
        self._file_tree_cache_key: tuple[tuple[str, str | None, str], ...] | None = None
        self._file_tree_cache: list[FileTreeRow] = []

    def run(self) -> ReviewState:
        curses.wrapper(self._main)
        return self.state

    def _main(self, stdscr) -> None:
        self._set_cursor_visibility(0)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
        try:
            curses.nonl()
        except curses.error:
            pass
        try:
            curses.raw()
        except curses.error:
            pass
        curses.set_escdelay(25)
        self._init_colors()
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except curses.error:
            pass
        stdscr.keypad(True)

        while not self.quit_requested:
            self._draw(stdscr)
            key = self._read_key(stdscr)
            self._handle_key(key)

    def _read_key(self, stdscr) -> int | str:
        key = stdscr.get_wch()
        if self.comment_mode and _is_escape(key):
            return self._read_comment_escape_key(stdscr)
        return key

    def _read_comment_escape_key(self, stdscr) -> int | str:
        sequence: list[int | str] = []
        timeout_supported = hasattr(stdscr, "timeout")
        try:
            if timeout_supported:
                stdscr.timeout(COMMENT_ESCAPE_TIMEOUT_MS)
            else:
                stdscr.nodelay(True)
            for _ in range(8):
                try:
                    sequence.append(stdscr.get_wch())
                except curses.error:
                    break
                if _escape_sequence_complete(sequence):
                    break
        finally:
            if timeout_supported:
                stdscr.timeout(-1)
            else:
                stdscr.nodelay(False)
        decoded = _decode_comment_escape_sequence(sequence)
        return decoded if decoded is not None else "\x1b"

    def _init_colors(self) -> None:
        if not curses.has_colors():
            return
        self._color_pairs = {}
        self._next_color_pair = 1

    def _draw(self, stdscr) -> None:
        self.comment_cursor = None
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 12 or width < 60:
            self._safe_addnstr(stdscr, 0, 0, "Terminal is too small for review. Resize to at least 60x12.", width - 1, curses.A_BOLD)
            self._apply_cursor(stdscr, height, width)
            stdscr.refresh()
            return

        self.content_height = height - 1
        self.left_width = max(24, min(42, width // 3)) if self.file_pane_visible else 0
        if self.file_pane_visible:
            for y in range(self.content_height):
                self._safe_addnstr(stdscr, y, self.left_width, "|", 1, self._style("muted"))
            self._draw_file_pane(stdscr, height, width)
        elif self.focus in {"file", "comments"}:
            self.focus = "review"
        self._draw_review_pane(stdscr, height, width)
        self._draw_status(stdscr, height, width)
        self._apply_cursor(stdscr, height, width)
        stdscr.refresh()

    def _draw_file_pane(self, stdscr, height: int, width: int) -> None:
        file_start, file_height, comment_start, comment_height = self._left_pane_layout()
        self._draw_file_tree_region(stdscr, file_start, file_height)
        self._draw_comment_list_region(stdscr, comment_start, comment_height)

    def _left_pane_layout(self) -> tuple[int, int, int, int]:
        if self.content_height <= 3:
            return 0, self.content_height, self.content_height, 0
        file_height = max(2, (self.content_height + 1) // 2)
        comment_height = max(0, self.content_height - file_height)
        return 0, file_height, file_height, comment_height

    def _draw_file_tree_region(self, stdscr, y: int, height: int) -> None:
        if height <= 0:
            return
        title = " Modified files "
        attr = curses.A_BOLD | (curses.A_REVERSE if self.focus == "file" else self._style("header"))
        self._safe_addnstr(stdscr, y, 0, title.ljust(self.left_width), self.left_width, attr)
        visible_height = max(0, height - 1)
        if visible_height <= 0:
            return
        rows = self._file_tree_rows()
        region = self._scroll_region(y, height, len(rows))
        visible_height = region.visible_height
        comment_counts = self._comment_counts_by_file()
        selected_row = file_tree_row_index(rows, self.state.file_pane_index) if rows else 0
        self.file_scroll = max(0, min(self.file_scroll, max(0, len(rows) - visible_height)))
        if selected_row < self.file_scroll:
            self.file_scroll = selected_row
        elif selected_row >= self.file_scroll + visible_height:
            self.file_scroll = selected_row - visible_height + 1
        for screen_row, row_index in enumerate(range(self.file_scroll, min(len(rows), self.file_scroll + visible_height)), start=y + 1):
            row = rows[row_index]
            text = self._file_tree_text(row, comment_counts)
            selected = row.kind == "file" and row.file_index == self.state.file_pane_index
            row_attr = curses.A_REVERSE if selected else curses.A_NORMAL
            if self.focus == "file" and selected:
                row_attr |= curses.A_BOLD
            if row.kind == "directory":
                row_attr |= curses.A_BOLD | self._style("muted")
            self._safe_addnstr(stdscr, screen_row, 0, text.ljust(self.left_width), self.left_width, row_attr)
        self._draw_scroll_footer(stdscr, region, self.file_scroll, len(rows), visible_height)

    def _draw_comment_list_region(self, stdscr, y: int, height: int) -> None:
        if height <= 0:
            return
        title = " Review comments "
        attr = curses.A_BOLD | (curses.A_REVERSE if self.focus == "comments" else self._style("header"))
        self._safe_addnstr(stdscr, y, 0, title.ljust(self.left_width), self.left_width, attr)
        visible_height = max(0, height - 1)
        if visible_height <= 0:
            return
        rows = self._comment_pane_rows()
        if not rows:
            self.comment_scroll = 0
            self.comment_pane_index = 0
            self._safe_addnstr(stdscr, y + 1, 0, " No comments".ljust(self.left_width), self.left_width, self._style("muted"))
            return
        region = self._scroll_region(y, height, len(rows))
        visible_height = region.visible_height
        selected_row = self._selected_comment_pane_row_index(rows)
        self._ensure_comment_pane_scroll(rows, selected_row, visible_height)
        for screen_row, row_index in enumerate(range(self.comment_scroll, min(len(rows), self.comment_scroll + visible_height)), start=y + 1):
            row = rows[row_index]
            selected = row_index == selected_row and row.kind == "comment"
            text = self._comment_pane_text(row)
            row_attr = curses.A_REVERSE if selected else curses.A_NORMAL
            if self.focus == "comments" and selected:
                row_attr |= curses.A_BOLD
            if row.kind == "file":
                row_attr |= curses.A_BOLD | self._style("muted")
            self._safe_addnstr(stdscr, screen_row, 0, text.ljust(self.left_width), self.left_width, row_attr)
        self._draw_scroll_footer(stdscr, region, self.comment_scroll, len(rows), visible_height)

    @staticmethod
    def _scroll_region(y: int, height: int, row_count: int) -> ScrollRegion:
        body_height = max(0, height - 1)
        if height >= 3 and row_count > body_height:
            return ScrollRegion(max(0, body_height - 1), y + height - 1)
        return ScrollRegion(body_height, None)

    def _draw_scroll_footer(
        self,
        stdscr,
        region: ScrollRegion,
        scroll: int,
        row_count: int,
        visible_height: int,
    ) -> None:
        if region.footer_y is None:
            return
        above = max(0, scroll)
        below = max(0, row_count - scroll - visible_height)
        text = _scroll_footer_text(above, below)
        self._safe_addnstr(stdscr, region.footer_y, 0, text.ljust(self.left_width), self.left_width, self._style("muted"))

    def _ensure_comment_pane_scroll(self, rows: list[CommentPaneRow], selected_row: int, visible_height: int) -> None:
        self.comment_scroll = max(0, min(self.comment_scroll, max(0, len(rows) - visible_height)))
        if selected_row < self.comment_scroll:
            self.comment_scroll = selected_row
        elif selected_row >= self.comment_scroll + visible_height:
            self.comment_scroll = selected_row - visible_height + 1

    def _file_tree_rows(self) -> list[FileTreeRow]:
        cache_key = tuple((file.path, file.old_path, file.status) for file in self.state.files)
        if cache_key != self._file_tree_cache_key:
            self._file_tree_cache_key = cache_key
            self._file_tree_cache = build_file_tree(self.state.files)
        return self._file_tree_cache

    def _comment_counts_by_file(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for comment in self.state.comments:
            counts[comment.file_path] = counts.get(comment.file_path, 0) + 1
        return counts

    def _file_tree_text(self, row: FileTreeRow, comment_counts: dict[str, int] | None = None) -> str:
        indent = "  " * row.depth
        if row.kind == "directory":
            return f" {indent}{row.label}"
        if row.file_index is None:
            return ""
        file = self.state.files[row.file_index]
        comments = (comment_counts or {}).get(file.path, 0)
        comment_text = f" [{comments}]" if comments else ""
        return f" {indent}{file.status_marker()} {row.label}{comment_text}"

    def _comment_pane_rows(self) -> list[CommentPaneRow]:
        rows: list[CommentPaneRow] = []
        for file_index, file in enumerate(self.state.files):
            comments = self.state.comments_for_file(file.path)
            if not comments:
                continue
            rows.append(CommentPaneRow("file", file_index, file.path, text=file.display_path))
            for comment in comments:
                rows.append(CommentPaneRow("comment", file_index, file.path, comment=comment))
        return rows

    def _selected_comment_pane_row_index(self, rows: list[CommentPaneRow]) -> int:
        if self.state.selected_comment_id is not None:
            for index, row in enumerate(rows):
                if row.comment is not None and row.comment.id == self.state.selected_comment_id:
                    self.comment_pane_index = index
                    return index
        selectable = self._comment_pane_selectable_rows(rows)
        if self.comment_pane_index in selectable:
            return self.comment_pane_index
        if selectable:
            self.comment_pane_index = selectable[0]
            return selectable[0]
        self.comment_pane_index = 0
        return 0

    @staticmethod
    def _comment_pane_selectable_rows(rows: list[CommentPaneRow]) -> list[int]:
        return [index for index, row in enumerate(rows) if row.kind == "comment" and row.comment is not None]

    def _comment_pane_text(self, row: CommentPaneRow) -> str:
        if row.kind == "file":
            return _truncate(f" {row.text}", self.left_width)
        if row.comment is None:
            return ""
        line = _comment_line_label(row.comment).rjust(4)
        body = " ".join(row.comment.body.split())
        return _truncate(f" {line} {body}", self.left_width)

    def _draw_review_pane(self, stdscr, height: int, width: int) -> None:
        right_x = self.left_width + 1 if self.file_pane_visible else 0
        right_width = width - right_x
        self.review_width = right_width
        items = self.state.document_items()
        if not items:
            self._safe_addnstr(stdscr, 0, right_x, " No changes ", right_width - 1, curses.A_BOLD)
            return
        active_index = self.state.active_document_index()
        self._ensure_selected_visible(items, active_index)
        if self.focus == "review":
            self.state.update_file_highlight_for_document_index(active_index if active_index is not None else self.review_scroll)
        frame = self._draw_frame(active_index)
        sticky = self._sticky_header(items)
        attr = curses.A_BOLD | (curses.A_REVERSE if self.focus == "review" else self._style("header"))
        self._safe_addnstr(stdscr, 0, right_x, f" {sticky} ".ljust(right_width), right_width - 1, attr)

        self.screen_map = {}
        y = 1
        index = self.review_scroll
        while y < self.content_height and index < len(items):
            item = items[index]
            used = self._draw_review_item(stdscr, y, right_x, right_width, item, index, index == frame.active_index, frame)
            for screen_y in range(y, min(self.content_height, y + used)):
                self.screen_map[screen_y] = index
            y += max(1, used)
            index += 1

    def _draw_frame(self, active_index: int | None = None) -> DrawFrame:
        selected_file_path = None
        selected_rows: frozenset[int] = frozenset()
        selected = self.state.selected_visible_rows()
        if selected is not None:
            selected_file_path, rows = selected
            selected_rows = frozenset(rows)
        comment_input_row = max(selected_rows) if selected_rows else None
        return DrawFrame(
            active_index=self.state.active_document_index() if active_index is None else active_index,
            selected_file_path=selected_file_path,
            selected_rows=selected_rows,
            comment_input_row=comment_input_row,
            comment_ranges=self._comment_ranges_by_file(),
        )

    def _comment_ranges_by_file(self) -> dict[str, tuple[tuple[int, int], ...]]:
        ranges: dict[str, list[tuple[int, int]]] = {}
        for comment in self.state.comments:
            ranges.setdefault(comment.file_path, []).append(comment.sorted_rows)
        return {path: tuple(path_ranges) for path, path_ranges in ranges.items()}

    def _draw_review_item(
        self,
        stdscr,
        y: int,
        x: int,
        width: int,
        item: DocumentItem,
        document_index: int,
        selected: bool,
        frame: DrawFrame | None = None,
    ) -> int:
        if frame is None:
            frame = self._draw_frame()
        if item.kind == "file_header":
            attr = curses.A_BOLD | self._style("header")
            return self._draw_full_width_row(stdscr, y, x, width, f" {item.text} ", attr)
        if item.kind == "metadata":
            return self._draw_full_width_row(stdscr, y, x, width, f"   {item.text}", self._style("warning"))
        if item.kind == "expansion" and item.expansion is not None:
            attr = self._style("link") | (curses.A_REVERSE if selected else curses.A_NORMAL)
            return self._draw_full_width_row(stdscr, y, x, width, f"   ... {item.expansion.label()} ...", attr)
        if item.kind == "comment" and item.comment is not None:
            editing = self.comment_mode and self.editing_comment_id == item.comment.id
            body = self.comment_buffer if editing else item.comment.body
            return self._draw_comment(
                stdscr,
                y,
                x,
                width,
                body or " ",
                saved=not editing,
                selected=selected,
                cursor_index=self.comment_cursor_index if editing else None,
            )
        if item.kind == "code" and item.line is not None and item.row_index is not None:
            return self._draw_code_item(stdscr, y, x, width, item, selected, frame)
        return 1

    def _review_item_height(self, item: DocumentItem, width: int, frame: DrawFrame | None = None) -> int:
        if item.kind in {"file_header", "metadata", "expansion"}:
            return 1
        if item.kind == "comment" and item.comment is not None:
            editing = self.comment_mode and self.editing_comment_id == item.comment.id
            body = self.comment_buffer if editing else item.comment.body
            return _comment_visual_height(body or " ", width)
        if item.kind == "code" and item.line is not None:
            height = _code_line_visual_height(item.line.text, width)
            if frame is not None and self._should_draw_comment_input(item, frame):
                height += _comment_visual_height(self.comment_buffer or " ", width)
            return height
        return 1

    def _draw_full_width_row(self, stdscr, y: int, x: int, width: int, text: str, attr: int) -> int:
        self._safe_addnstr(stdscr, y, x, text.ljust(width), width - 1, attr)
        return 1

    def _draw_code_item(self, stdscr, y: int, x: int, width: int, item: DocumentItem, selected: bool, frame: DrawFrame) -> int:
        used = self._draw_code_line(stdscr, y, x, width, item, selected, frame)
        if self._should_draw_comment_input(item, frame):
            used += self._draw_comment(
                stdscr,
                y + used,
                x,
                width,
                self.comment_buffer or " ",
                saved=False,
                cursor_index=self.comment_cursor_index,
            )
        return used

    def _should_draw_comment_input(self, item: DocumentItem, frame: DrawFrame) -> bool:
        if not self.comment_mode or item.row_index is None:
            return False
        return item.file_path == frame.selected_file_path and item.row_index == frame.comment_input_row

    def _draw_code_line(
        self,
        stdscr,
        y: int,
        x: int,
        width: int,
        item: DocumentItem,
        selected: bool,
        frame: DrawFrame | None = None,
    ) -> int:
        if frame is None:
            frame = self._draw_frame()
        line = item.line
        assert line is not None
        number = line.primary_line
        body_width = _body_width(width)
        chunks = _wrap_text_segments(line.text, body_width)
        selected_range = frame.is_selected_row(item.file_path, item.row_index)
        range_rail = selected_range or frame.has_comment_range(item.file_path, item.row_index)
        background = self._line_background(line, selected or selected_range)
        modifiers = self._code_line_modifiers(item, selected, selected_range)
        file = self.state.file_by_path(item.file_path)
        spans = syntax_spans(line.text, file.language)
        for visual_offset, (chunk, source_offset) in enumerate(chunks):
            if y + visual_offset >= self.content_height:
                break
            number_text = _line_number_text(number, visual_offset)
            marker = line.marker if visual_offset == 0 else " "
            row_attr = self._style("plain", background, modifiers)
            self._safe_addnstr(stdscr, y + visual_offset, x, " " * max(0, width - 1), width - 1, row_attr)
            self._draw_gutter(stdscr, y + visual_offset, x, number_text, marker, range_rail, background, modifiers)
            self._draw_syntax(
                stdscr,
                y + visual_offset,
                x + GUTTER_WIDTH,
                chunk,
                body_width,
                source_offset,
                spans,
                background,
                modifiers,
                self._item_search_ranges(item),
            )
        return max(1, len(chunks))

    def _code_line_modifiers(self, item: DocumentItem, selected: bool, selected_range: bool) -> int:
        return curses.A_NORMAL

    def _draw_gutter(
        self,
        stdscr,
        y: int,
        x: int,
        number_text: str,
        marker: str,
        range_rail: bool,
        background: str | None,
        modifiers: int,
    ) -> None:
        self._safe_addnstr(stdscr, y, x, number_text + " ", 5, self._style("line-number", background, modifiers))
        rail = "|" if range_rail else " "
        rail_attr = self._style("rail", background, modifiers)
        self._safe_addnstr(stdscr, y, x + 5, rail, 1, rail_attr)
        self._safe_addnstr(stdscr, y, x + 6, marker + " ", 2, self._style("line-number", background, modifiers))

    def _draw_comment(
        self,
        stdscr,
        y: int,
        x: int,
        width: int,
        text: str,
        *,
        saved: bool,
        selected: bool = False,
        cursor_index: int | None = None,
    ) -> int:
        lines = _comment_display_lines(text)
        modifiers = curses.A_NORMAL
        background = self._comment_background(saved, selected)
        attr = self._style("warning", background, modifiers) | (curses.A_BOLD if not saved else curses.A_NORMAL)
        row_attr = self._style("plain", background, modifiers)
        used = 0
        line_start = 0
        for line in lines:
            if y + used >= self.content_height:
                break
            chunks = _wrap_text_segments(line, _body_width(width))
            for chunk, source_offset in chunks:
                if y + used >= self.content_height:
                    break
                self._safe_addnstr(stdscr, y + used, x, " " * max(0, width - 1), width - 1, row_attr)
                self._draw_comment_gutter(stdscr, y + used, x, background=background, selected=selected)
                self._safe_addnstr(stdscr, y + used, x + GUTTER_WIDTH, chunk, width - GUTTER_WIDTH - 1, attr)
                if cursor_index is not None:
                    chunk_start = line_start + source_offset
                    chunk_end = chunk_start + len(chunk)
                    if chunk_start <= cursor_index <= chunk_end:
                        cursor_column = cursor_index - chunk_start
                        self.comment_cursor = (y + used, min(x + width - 1, x + GUTTER_WIDTH + cursor_column))
                used += 1
            line_start += len(line) + 1
        return max(1, used)

    @staticmethod
    def _comment_background(saved: bool, selected: bool) -> str | None:
        if selected:
            return "selection"
        if saved:
            return "comment"
        return None

    def _draw_comment_gutter(self, stdscr, y: int, x: int, *, background: str | None = None, selected: bool = False) -> None:
        self._draw_gutter(stdscr, y, x, "    ", " ", True, background, curses.A_NORMAL)

    def _draw_syntax(
        self,
        stdscr,
        y: int,
        x: int,
        text: str,
        width: int,
        source_offset: int,
        spans: list[tuple[int, int, str]],
        background: str | None,
        modifiers: int,
        search_ranges: list[tuple[int, int]] | None = None,
    ) -> None:
        for local_start, local_end, role, match in _syntax_segments(text, source_offset, spans, search_ranges or []):
            attr = self._style(role, "search" if match else background, modifiers)
            self._safe_addnstr(stdscr, y, x + local_start, text[local_start:local_end], width - local_start, attr)

    def _item_search_ranges(self, item: DocumentItem) -> list[tuple[int, int]]:
        if not self.search_query:
            return []
        text = _item_search_text(item)
        if text is None:
            return []
        return _literal_match_ranges(text, self.search_query)

    def _draw_status(self, stdscr, height: int, width: int) -> None:
        y = height - 1
        text, attr = self._status_line()
        self._safe_addnstr(stdscr, y, 0, text.ljust(width), width - 1, attr)

    def _apply_cursor(self, stdscr, height: int, width: int) -> None:
        if self.search_mode:
            self._set_cursor_visibility(1)
            try:
                stdscr.move(height - 1, min(width - 1, 1 + len(self.search_buffer)))
            except curses.error:
                pass
        elif self.comment_mode and self.comment_cursor is not None:
            self._set_cursor_visibility(1)
            y, x = self.comment_cursor
            try:
                stdscr.move(max(0, min(height - 1, y)), max(0, min(width - 1, x)))
            except curses.error:
                pass
        else:
            self._set_cursor_visibility(0)

    @staticmethod
    def _set_cursor_visibility(visibility: int) -> None:
        try:
            curses.curs_set(visibility)
        except curses.error:
            pass

    def _status_line(self) -> tuple[str, int]:
        if self.interrupt_armed:
            return INTERRUPT_CONFIRMATION_MESSAGE, curses.A_REVERSE
        if self.command_mode:
            return ":" + self.command_buffer, curses.A_REVERSE
        if self.search_mode:
            return "/" + self.search_buffer, curses.A_REVERSE
        if self.comment_mode:
            action = "Edit comment" if self.editing_comment_id else "New comment"
            return f"{action}: Enter saves, Ctrl+J inserts newline, Esc cancels", curses.A_REVERSE
        return self.status_message, self._style("muted")

    def _handle_key(self, key: int | str) -> None:
        if _is_ctrl_c(key):
            self._handle_interrupt()
            return
        self.interrupt_armed = False
        if self.comment_mode:
            self._handle_comment_key(key)
            return
        if self.command_mode:
            self._handle_command_key(key)
            return
        if self.search_mode:
            self._handle_search_key(key)
            return

        if self._handle_global_key(key):
            return
        if self.focus == "file":
            self._handle_file_key(key)
        elif self.focus == "comments":
            self._handle_comment_pane_key(key)
        else:
            self._handle_review_key(key)

    def _handle_interrupt(self) -> None:
        if self.interrupt_armed:
            self.quit_requested = True
            return
        self.interrupt_armed = True

    def _handle_global_key(self, key: int | str) -> bool:
        if key in (9, "\t"):
            self._switch_focus()
            return True
        if key in ("t", "T", ord("t"), ord("T")):
            self._toggle_file_pane()
            return True
        if key in (ord(":"), ":"):
            self._enter_command_mode()
            return True
        if key in (ord("/"), "/"):
            self._enter_search_mode()
            return True
        if key in ("z", ord("z")):
            self._center_review_on_selection()
            return True
        if key in ("n", "N", ord("n"), ord("N")):
            self._jump_to_search_match(1)
            return True
        if key in ("p", "P", ord("p"), ord("P")):
            self._jump_to_search_match(-1)
            return True
        if _is_escape(key):
            if self.state.collapse_selection_to_active_row():
                self.status_message = "Selection cleared."
                return True
        if key == curses.KEY_MOUSE:
            self._handle_mouse()
            return True
        return False

    def _switch_focus(self) -> None:
        if self.file_pane_visible:
            if self.focus == "review":
                self.focus = "file"
            elif self.focus == "file":
                self.focus = "comments"
                self._focus_comment_pane_selection()
            else:
                self.focus = "review"
        else:
            self.focus = "review"

    def _enter_command_mode(self) -> None:
        self.command_mode = True
        self.command_buffer = ""

    def _enter_search_mode(self) -> None:
        self.search_mode = True
        self.search_buffer = ""

    def _handle_file_key(self, key: int | str) -> None:
        if _is_up_key(key):
            self.review_scroll = self._move_file_tree_selection(-1)
        elif _is_down_key(key):
            self.review_scroll = self._move_file_tree_selection(1)
        elif key == curses.KEY_PPAGE:
            self.review_scroll = self._move_file_tree_selection(-max(1, self.content_height - 3))
        elif key == curses.KEY_NPAGE:
            self.review_scroll = self._move_file_tree_selection(max(1, self.content_height - 3))
        elif _is_enter(key):
            if self.state.files:
                self.review_scroll = self.state.select_file(self.state.files[self.state.file_pane_index].path)
                self.focus = "review"

    def _handle_comment_pane_key(self, key: int | str) -> None:
        if _is_up_key(key):
            self._move_comment_pane_selection(-1)
        elif _is_down_key(key):
            self._move_comment_pane_selection(1)
        elif key == curses.KEY_PPAGE:
            self._move_comment_pane_selection(-max(1, self._comment_pane_visible_height()))
        elif key == curses.KEY_NPAGE:
            self._move_comment_pane_selection(max(1, self._comment_pane_visible_height()))
        elif _is_enter(key):
            self._focus_comment_pane_selection(prefer_current=True)
            self.focus = "review"
        elif _is_comment_delete_key(key):
            self._delete_selected_comment()

    def _handle_review_key(self, key: int | str) -> None:
        if _is_up_key(key):
            self._move_review_selection(-1)
        elif _is_down_key(key):
            self._move_review_selection(1)
        elif key == curses.KEY_PPAGE:
            self._page_review_selection(-1)
        elif key == curses.KEY_NPAGE:
            self._page_review_selection(1)
        elif key in SHIFT_UP_KEYS:
            self._move_review_selection(-1, extend=True)
        elif key in SHIFT_DOWN_KEYS:
            self._move_review_selection(1, extend=True)
        elif _is_comment_delete_key(key):
            self._delete_selected_comment()
        elif _is_enter(key):
            self._activate_review_selection()

    def _move_review_selection(self, delta: int, *, extend: bool = False) -> None:
        if extend:
            self.state.extend_selection(delta)
        else:
            self.state.move_selection(delta)
        self._keep_selection_in_editor_view()

    def _activate_review_selection(self) -> None:
        action = self.state.activate_selection()
        if action == "comment":
            self._start_new_comment()
        elif action == "edit-comment":
            self._start_edit_selected_comment()
        elif action == "expanded":
            self._keep_selection_in_editor_view()

    def _handle_command_key(self, key: int | str) -> None:
        if _is_escape(key):
            self.command_mode = False
            return
        if _is_backspace(key):
            self.command_buffer = self.command_buffer[:-1]
            return
        if _is_enter(key):
            self._run_command_buffer()
            return
        text = _printable_key(key)
        if text is not None:
            self.command_buffer += text

    def _run_command_buffer(self) -> None:
        command = self.command_buffer.strip()
        self.command_mode = False
        handler = self.command_handlers.get(command)
        if handler is not None:
            handler()
        else:
            self.status_message = f"Unknown command: {command}"

    def _handle_search_key(self, key: int | str) -> None:
        if _is_escape(key):
            self.search_mode = False
            self.search_buffer = ""
            self.status_message = SEARCH_CANCELLED_MESSAGE
            return
        if _is_backspace(key):
            self.search_buffer = self.search_buffer[:-1]
            return
        if _is_enter(key):
            self._submit_search()
            return
        text = _printable_key(key)
        if text is not None:
            self.search_buffer += text

    def _submit_search(self) -> None:
        query = self.search_buffer
        self.search_mode = False
        self.search_buffer = ""
        if not query:
            self.search_query = ""
            self.status_message = "Search cleared."
            return
        self.search_query = query
        self._jump_to_search_match(1, include_current=True)

    def _jump_to_search_match(self, direction: int, *, include_current: bool = False) -> None:
        if not self.search_query:
            self.status_message = "No active search."
            return
        matches = self._search_match_indexes()
        if not matches:
            self.status_message = f"No matches for /{self.search_query}"
            return
        current = self.state.active_document_index()
        if current is None:
            current = self.review_scroll
        target = _next_search_target(matches, current, direction, include_current)
        self.state.select_document_index(target)
        self.review_scroll = target
        self._keep_selection_in_editor_view()
        self.status_message = f"Match {matches.index(target) + 1}/{len(matches)} for /{self.search_query}"

    def _search_match_indexes(self) -> list[int]:
        return [
            index
            for index, item in enumerate(self.state.document_items())
            if item.selectable and self._item_search_ranges(item)
        ]

    def _handle_comment_key(self, key: int | str) -> None:
        key = _normalize_comment_key(key)
        if _is_comment_line_start_key(key):
            self._move_comment_cursor_to_line_start_or_message_start()
            return
        if _is_comment_line_end_key(key):
            self._move_comment_cursor_to_line_end_or_message_end()
            return
        if _is_comment_word_left_key(key):
            self._move_comment_cursor_word(-1)
            return
        if _is_comment_word_right_key(key):
            self._move_comment_cursor_word(1)
            return
        if _is_comment_word_delete_key(key):
            self._delete_comment_word_before_cursor()
            return
        if _is_escape(key):
            self._close_comment_input()
            return
        if _is_backspace(key):
            self._delete_comment_character_before_cursor()
            return
        if key == curses.KEY_LEFT:
            self._move_comment_cursor_horizontal(-1)
            return
        if key == curses.KEY_RIGHT:
            self._move_comment_cursor_horizontal(1)
            return
        if key == curses.KEY_UP:
            self._move_comment_cursor_vertical(-1)
            return
        if key == curses.KEY_DOWN:
            self._move_comment_cursor_vertical(1)
            return
        if _is_comment_newline(key):
            self._insert_comment_text("\n")
            return
        if _is_enter(key):
            self._submit_comment_input()
            return
        if key in (9, "\t"):
            self._insert_comment_text("\t")
            return
        text = _printable_key(key)
        if text is not None:
            self._insert_comment_text(text)

    def _insert_comment_text(self, text: str) -> None:
        self.comment_cursor_index = max(0, min(len(self.comment_buffer), self.comment_cursor_index))
        self.comment_buffer = self.comment_buffer[: self.comment_cursor_index] + text + self.comment_buffer[self.comment_cursor_index :]
        self.comment_cursor_index += len(text)
        self.comment_cursor_goal_column = None

    def _delete_comment_character_before_cursor(self) -> None:
        self.comment_cursor_index = max(0, min(len(self.comment_buffer), self.comment_cursor_index))
        if self.comment_cursor_index == 0:
            return
        self.comment_buffer = self.comment_buffer[: self.comment_cursor_index - 1] + self.comment_buffer[self.comment_cursor_index :]
        self.comment_cursor_index -= 1
        self.comment_cursor_goal_column = None

    def _delete_comment_word_before_cursor(self) -> None:
        self.comment_cursor_index = max(0, min(len(self.comment_buffer), self.comment_cursor_index))
        if self.comment_cursor_index == 0:
            return
        start = _previous_comment_word_index(self.comment_buffer, self.comment_cursor_index)
        self.comment_buffer = self.comment_buffer[:start] + self.comment_buffer[self.comment_cursor_index :]
        self.comment_cursor_index = start
        self.comment_cursor_goal_column = None

    def _move_comment_cursor_horizontal(self, delta: int) -> None:
        self.comment_cursor_index = max(0, min(len(self.comment_buffer), self.comment_cursor_index + delta))
        self.comment_cursor_goal_column = None

    def _move_comment_cursor_to_line_start_or_message_start(self) -> None:
        start, _ = _comment_cursor_line_bounds(self.comment_buffer, self.comment_cursor_index)
        self.comment_cursor_index = 0 if self.comment_cursor_index == start else start
        self.comment_cursor_goal_column = None

    def _move_comment_cursor_to_line_end_or_message_end(self) -> None:
        _, end = _comment_cursor_line_bounds(self.comment_buffer, self.comment_cursor_index)
        message_end = len(self.comment_buffer)
        self.comment_cursor_index = message_end if self.comment_cursor_index == end else end
        self.comment_cursor_goal_column = None

    def _move_comment_cursor_word(self, direction: int) -> None:
        self.comment_cursor_index = _comment_word_cursor_index(self.comment_buffer, self.comment_cursor_index, direction)
        self.comment_cursor_goal_column = None

    def _move_comment_cursor_vertical(self, delta: int) -> None:
        line_index, column = _comment_cursor_line_column(self.comment_buffer, self.comment_cursor_index)
        if self.comment_cursor_goal_column is None:
            self.comment_cursor_goal_column = column
        target_line = line_index + delta
        self.comment_cursor_index = _comment_cursor_index_for_line_column(
            self.comment_buffer,
            target_line,
            self.comment_cursor_goal_column,
        )

    def _submit_comment_input(self) -> None:
        if self.editing_comment_id:
            if self.state.update_comment(self.editing_comment_id, self.comment_buffer):
                self.status_message = "Comment updated."
            else:
                self.status_message = "Empty comments are ignored."
        else:
            comment = self.state.add_comment(self.comment_buffer)
            if comment is None:
                self.status_message = "Empty comments are ignored."
                self._close_comment_input()
                return
            self.state.select_comment(comment.id)
            self._keep_selection_in_editor_view()
            self.status_message = "Comment saved."
        self._close_comment_input()

    def _close_comment_input(self) -> None:
        self.comment_mode = False
        self.comment_buffer = ""
        self.comment_cursor_index = 0
        self.comment_cursor_goal_column = None
        self.editing_comment_id = None

    def _build_command_handlers(self) -> dict[str, CommandHandler]:
        handlers: dict[str, CommandHandler] = {}
        for aliases, handler in (
            (("q", "quit"), self._command_quit),
            (("q!", "quit!"), self._command_force_quit),
            (("e", "edit", "edit-comment"), self._command_edit_comment),
            (("d", "delete", "delete-comment"), self._command_delete_comment),
            (("c", "center", "centre"), self._command_center),
        ):
            for alias in aliases:
                handlers[alias] = handler
        return handlers

    def _command_quit(self) -> None:
        self.quit_requested = True

    def _command_force_quit(self) -> None:
        self.quit_requested = True

    def _command_center(self) -> None:
        self._center_review_on_selection()

    def _command_edit_comment(self) -> None:
        self._start_edit_selected_comment()

    def _start_new_comment(self) -> None:
        self.comment_mode = True
        self.comment_buffer = ""
        self.comment_cursor_index = 0
        self.comment_cursor_goal_column = None
        self.editing_comment_id = None

    def _start_edit_selected_comment(self) -> None:
        comment = self.state.comment_for_selection()
        if comment is None:
            self.status_message = NO_SELECTED_COMMENT_MESSAGE
            return
        self.comment_mode = True
        self.editing_comment_id = comment.id
        self.comment_buffer = comment.body
        self.comment_cursor_index = len(self.comment_buffer)
        self.comment_cursor_goal_column = None

    def _command_delete_comment(self) -> None:
        self._delete_selected_comment()

    def _delete_selected_comment(self) -> None:
        comment = self.state.comment_for_selection()
        if comment is None:
            self.status_message = NO_SELECTED_COMMENT_MESSAGE
        elif self.state.delete_comment(comment.id):
            self.status_message = "Comment deleted."
        else:
            self.status_message = "Comment was already deleted."

    def _toggle_file_pane(self) -> None:
        self.file_pane_visible = not self.file_pane_visible
        if not self.file_pane_visible:
            self.focus = "review"
            self.status_message = "Left pane hidden. Press T to show it."
        else:
            self.status_message = "Left pane shown. Press T to hide it."

    def _move_file_tree_selection(self, delta: int) -> int:
        rows = self._file_tree_rows()
        file_rows = [row_index for row_index, row in enumerate(rows) if row.kind == "file" and row.file_index is not None]
        if not file_rows:
            return 0
        current_row = file_tree_row_index(rows, self.state.file_pane_index)
        try:
            position = file_rows.index(current_row)
        except ValueError:
            position = 0
        position = max(0, min(len(file_rows) - 1, position + delta))
        row = rows[file_rows[position]]
        assert row.file_index is not None
        return self.state.select_file(self.state.files[row.file_index].path)

    def _move_comment_pane_selection(self, delta: int) -> None:
        rows = self._comment_pane_rows()
        selectable = self._comment_pane_selectable_rows(rows)
        if not selectable:
            self.status_message = "No review comments."
            return
        current_row = self._selected_comment_pane_row_index(rows)
        try:
            position = selectable.index(current_row)
        except ValueError:
            position = 0
        position = max(0, min(len(selectable) - 1, position + delta))
        self.comment_pane_index = selectable[position]
        self._focus_comment_pane_selection(rows, prefer_current=True)

    def _focus_comment_pane_selection(self, rows: list[CommentPaneRow] | None = None, *, prefer_current: bool = False) -> None:
        if rows is None:
            rows = self._comment_pane_rows()
        selectable = self._comment_pane_selectable_rows(rows)
        if not selectable:
            self.status_message = "No review comments."
            return
        if prefer_current and self.comment_pane_index in selectable:
            row_index = self.comment_pane_index
        else:
            row_index = self._selected_comment_pane_row_index(rows)
        if row_index not in selectable:
            row_index = selectable[0]
        row = rows[row_index]
        if row.comment is None:
            return
        self.comment_pane_index = row_index
        self.review_scroll = self.state.select_comment(row.comment.id)
        self._keep_selection_in_editor_view()
        self.status_message = "Focused review comment."

    def _comment_pane_visible_height(self) -> int:
        _, _, _, comment_height = self._left_pane_layout()
        return max(1, comment_height - 1)

    def _center_review_on_selection(self) -> None:
        active = self.state.active_document_index()
        if active is None:
            active = self.review_scroll
        items = self.state.document_items()
        if not items:
            return
        viewport = self._review_viewport_height()
        max_scroll = max(0, len(items) - viewport)
        self.review_scroll = max(0, min(max_scroll, active - viewport // 2))
        self.state.update_file_highlight_for_document_index(self.review_scroll)
        self.status_message = "Centered code view."

    def _page_review_selection(self, direction: int) -> None:
        active_before = self.state.active_document_index()
        offset = 0 if active_before is None else max(0, active_before - self.review_scroll)
        viewport = self._review_viewport_height()
        self.state.move_selection(direction * viewport)
        active_after = self.state.active_document_index()
        if active_after is None:
            return
        items = self.state.document_items()
        max_scroll = max(0, len(items) - viewport)
        self.review_scroll = max(0, min(max_scroll, active_after - offset))
        self.state.update_file_highlight_for_document_index(active_after)

    def _handle_mouse(self) -> None:
        mouse = self._read_mouse()
        if mouse is None:
            return
        x, y, button = mouse
        if y >= self.content_height:
            return
        if self._handle_mouse_wheel(button):
            return
        if x < self.left_width:
            if self._comment_pane_contains_y(y):
                self._handle_comment_pane_mouse(y)
            else:
                self._handle_file_pane_mouse(y)
            return
        self._handle_review_pane_mouse(y, button)

    @staticmethod
    def _read_mouse() -> tuple[int, int, int] | None:
        try:
            _, x, y, _, button = curses.getmouse()
        except curses.error:
            return None
        return x, y, button

    def _handle_mouse_wheel(self, button: int) -> bool:
        if button & _mouse_mask("BUTTON4_PRESSED"):
            self._scroll_review_for_mouse(-MOUSE_SCROLL_LINES)
            return True
        if button & _mouse_mask("BUTTON5_PRESSED"):
            self._scroll_review_for_mouse(MOUSE_SCROLL_LINES)
            return True
        return False

    def _scroll_review_for_mouse(self, delta: int) -> None:
        if delta < 0:
            self.review_scroll = max(0, self.review_scroll + delta)
        else:
            max_scroll = max(0, len(self.state.document_items()) - 1)
            self.review_scroll = min(max_scroll, self.review_scroll + delta)
        self.state.update_file_highlight_for_document_index(self.review_scroll)
        self._select_first_visible_selectable()

    def _handle_file_pane_mouse(self, y: int) -> None:
        self.focus = "file"
        self._clear_mouse_drag()
        row = self._file_tree_row_at(y)
        if row is not None and row.kind == "file" and row.file_index is not None:
            self.review_scroll = self.state.select_file(self.state.files[row.file_index].path)

    def _comment_pane_contains_y(self, y: int) -> bool:
        _, _, comment_start, comment_height = self._left_pane_layout()
        return comment_height > 0 and comment_start <= y < comment_start + comment_height

    def _file_tree_row_at(self, y: int) -> FileTreeRow | None:
        _, _, comment_start, _ = self._left_pane_layout()
        if y >= comment_start:
            return None
        rows = self._file_tree_rows()
        row_index = self.file_scroll + y - 1
        if 0 <= row_index < len(rows):
            return rows[row_index]
        return None

    def _handle_comment_pane_mouse(self, y: int) -> None:
        self.focus = "comments"
        self._clear_mouse_drag()
        rows = self._comment_pane_rows()
        row_index = self._comment_pane_row_index_at(y)
        if row_index is not None and 0 <= row_index < len(rows) and rows[row_index].kind == "comment":
            self.comment_pane_index = row_index
            self._focus_comment_pane_selection(rows, prefer_current=True)

    def _comment_pane_row_index_at(self, y: int) -> int | None:
        _, _, comment_start, comment_height = self._left_pane_layout()
        if comment_height <= 1 or y <= comment_start:
            return None
        row_index = self.comment_scroll + y - comment_start - 1
        return row_index if row_index >= 0 else None

    def _handle_review_pane_mouse(self, y: int, button: int) -> None:
        self.focus = "review"
        document_index = self.screen_map.get(y)
        if document_index is None:
            return
        item = self.state.document_items()[document_index]
        if item.kind == "code" and item.row_index is not None:
            self._handle_code_mouse(document_index, item, button)
        elif item.kind == "expansion" and item.expansion is not None and _mouse_primary_down(button):
            self._clear_mouse_drag()
            self.review_scroll = self.state.expand_context(item.expansion.id)
        else:
            self._select_review_mouse_item(document_index)

    def _handle_code_mouse(self, document_index: int, item: DocumentItem, button: int) -> None:
        assert item.row_index is not None
        if _mouse_primary_down(button):
            self.state.select_document_index(document_index)
            self.mouse_drag_anchor = (item.file_path, item.row_index)
        elif _mouse_drag_or_release(button):
            if self.mouse_drag_anchor and self.mouse_drag_anchor[0] == item.file_path:
                self.state.select_range(self.mouse_drag_anchor[0], self.mouse_drag_anchor[1], item.row_index)
            if button & _mouse_mask("BUTTON1_RELEASED"):
                self._clear_mouse_drag()
        else:
            self.state.select_document_index(document_index)

    def _select_review_mouse_item(self, document_index: int) -> None:
        self._clear_mouse_drag()
        self.state.select_document_index(document_index)
        self.review_scroll = document_index

    def _clear_mouse_drag(self) -> None:
        self.mouse_drag_anchor = None

    def _ensure_selected_visible(self, items: list[DocumentItem] | None = None, active: int | None = None) -> None:
        if active is None:
            active = self.state.active_document_index()
        if items is None:
            items = self.state.document_items()
        if active is None:
            self.review_scroll = max(0, min(self.review_scroll, self._max_review_scroll(items)))
            return
        self.review_scroll = self._scroll_for_visible_active(items, active, bottom_guard=0)

    def _select_first_visible_selectable(self) -> None:
        items = self.state.document_items()
        if not items:
            return
        viewport = self._review_viewport_height()
        width = self._current_review_width()
        used = 0
        for index in range(self.review_scroll, len(items)):
            if used >= viewport:
                break
            if items[index].selectable:
                self.state.select_document_index(index)
                return
            used += self._review_item_height(items[index], width)
        self.state.select_document_index(self.review_scroll)

    def _sticky_header(self, items: list[DocumentItem]) -> str:
        path = self.state.file_for_document_index(self.review_scroll)
        if path is None:
            return "Review"
        file = self.state.file_by_path(path)
        return f"{file.status_marker()} {file.display_path}"

    def _keep_selection_in_editor_view(self) -> None:
        active = self.state.active_document_index()
        if active is None:
            return
        items = self.state.document_items()
        self.review_scroll = self._scroll_for_visible_active(items, active, bottom_guard=3)
        self.state.update_file_highlight_for_document_index(active)

    def _scroll_for_visible_active(self, items: list[DocumentItem], active: int, *, bottom_guard: int) -> int:
        if not items:
            return 0
        active = max(0, min(active, len(items) - 1))
        scroll = max(0, min(self.review_scroll, active))
        width = self._current_review_width()
        viewport = self._review_viewport_height()
        target_bottom = max(1, viewport - max(0, bottom_guard))
        active_height = self._review_item_height(items[active], width)

        if active_height > target_bottom:
            target_bottom = viewport
        while scroll < active:
            active_top = self._review_rows_between(items, scroll, active, width)
            active_bottom = active_top + active_height
            if active_top >= 0 and (active_bottom <= target_bottom or (active_height > target_bottom and active_top == 0)):
                break
            scroll += 1
        return max(0, min(scroll, active))

    def _review_rows_between(self, items: list[DocumentItem], start: int, end: int, width: int | None = None) -> int:
        width = width or self._current_review_width()
        start = max(0, min(start, len(items)))
        end = max(0, min(end, len(items)))
        if end <= start:
            return 0
        return sum(self._review_item_height(item, width) for item in items[start:end])

    def _max_review_scroll(self, items: list[DocumentItem]) -> int:
        if not items:
            return 0
        viewport = self._review_viewport_height()
        width = self._current_review_width()
        if self._review_rows_between(items, 0, len(items), width) <= viewport:
            return 0
        used = 0
        for index in range(len(items) - 1, -1, -1):
            used += self._review_item_height(items[index], width)
            if used >= viewport:
                return index
        return 0

    def _current_review_width(self) -> int:
        return max(1, self.review_width)

    def _review_viewport_height(self) -> int:
        return max(1, self.content_height - 1)

    @staticmethod
    def _line_background(line: ReviewLine | None, selected: bool) -> str | None:
        if selected and line is not None and line.kind in {"addition", "deletion"}:
            return f"{line.kind}-selection"
        if selected:
            return "selection"
        if line is not None and line.kind in {"addition", "deletion"}:
            return line.kind
        return None

    def _style(self, role: str, background: str | None = None, modifiers: int = curses.A_NORMAL) -> int:
        return modifiers | self._role_modifier(role) | self._color_pair_attr(role, background)

    @staticmethod
    def _role_modifier(role: str) -> int:
        if role in BOLD_ROLES:
            return curses.A_BOLD
        return curses.A_NORMAL

    def _color_pair_attr(self, role: str, background: str | None) -> int:
        try:
            if not curses.has_colors():
                return curses.A_NORMAL
        except curses.error:
            return curses.A_NORMAL

        foreground = self._foreground_color(role, background)
        bg_color = self._background_color(background)
        if foreground == -1 and bg_color == -1:
            return curses.A_NORMAL

        key = (foreground, bg_color)
        pair_number = self._color_pairs.get(key)
        if pair_number is None:
            max_pairs = max(1, getattr(curses, "COLOR_PAIRS", 64) or 64)
            if self._next_color_pair >= max_pairs:
                return curses.A_NORMAL
            pair_number = self._next_color_pair
            self._next_color_pair += 1
            if not self._init_color_pair(pair_number, foreground, bg_color):
                return curses.A_NORMAL
            self._color_pairs[key] = pair_number
        try:
            return curses.color_pair(pair_number)
        except curses.error:
            return curses.A_NORMAL

    @staticmethod
    def _foreground_color(role: str, background: str | None) -> int:
        if background is not None:
            return FOREGROUND_WITH_BACKGROUND.get(role, curses.COLOR_BLACK)
        return FOREGROUND_DEFAULT.get(role, -1)

    @staticmethod
    def _background_color(background: str | None) -> int:
        color = BACKGROUND_COLORS.get(background or "")
        if color is not None:
            preferred_256, fallback = color
            return _terminal_color(preferred_256, fallback)
        return -1

    @staticmethod
    def _init_color_pair(pair_number: int, foreground: int, background: int) -> bool:
        try:
            curses.init_pair(pair_number, foreground, background)
            return True
        except curses.error:
            fallback_foreground = curses.COLOR_WHITE if foreground == -1 else foreground
            fallback_background = curses.COLOR_BLACK if background == -1 else background
            try:
                curses.init_pair(pair_number, fallback_foreground, fallback_background)
                return True
            except curses.error:
                return False

    @staticmethod
    def _safe_addnstr(stdscr, y: int, x: int, text: str, n: int, attr: int = curses.A_NORMAL) -> None:
        if n <= 0:
            return
        try:
            stdscr.addnstr(y, x, text, n, attr)
        except curses.error:
            pass


def _item_search_text(item: DocumentItem) -> str | None:
    if item.kind == "code" and item.line is not None:
        return item.line.text
    if item.kind == "comment" and item.comment is not None:
        return item.comment.body
    return None


def _literal_match_ranges(text: str, query: str) -> list[tuple[int, int]]:
    if not query:
        return []
    ranges: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(query, start)
        if index < 0:
            return ranges
        end = index + len(query)
        ranges.append((index, end))
        start = max(index + 1, end)


def _next_search_target(matches: list[int], current: int, direction: int, include_current: bool) -> int:
    if direction >= 0:
        current_match = next((index for index in matches if include_current and index >= current), None)
        if current_match is not None:
            return current_match
        return next((index for index in matches if index > current), matches[0])
    current_match = next((index for index in reversed(matches) if include_current and index <= current), None)
    if current_match is not None:
        return current_match
    return next((index for index in reversed(matches) if index < current), matches[-1])


def _syntax_segments(
    text: str,
    source_offset: int,
    spans: list[tuple[int, int, str]],
    search_ranges: list[tuple[int, int]],
) -> list[tuple[int, int, str, bool]]:
    if not text:
        return []
    chunk_start = source_offset
    chunk_end = source_offset + len(text)
    boundaries = {0, len(text)}
    for start, end, _role in spans:
        _add_local_boundaries(boundaries, start, end, chunk_start, chunk_end)
    for start, end in search_ranges:
        _add_local_boundaries(boundaries, start, end, chunk_start, chunk_end)
    ordered = sorted(boundaries)
    return [
        (
            local_start,
            local_end,
            _syntax_role_at(chunk_start + local_start, spans),
            _range_overlaps_any(chunk_start + local_start, chunk_start + local_end, search_ranges),
        )
        for local_start, local_end in zip(ordered, ordered[1:])
        if local_start < local_end
    ]


def _add_local_boundaries(boundaries: set[int], start: int, end: int, chunk_start: int, chunk_end: int) -> None:
    local_start = max(start, chunk_start) - chunk_start
    local_end = min(end, chunk_end) - chunk_start
    if local_start < local_end:
        boundaries.add(local_start)
        boundaries.add(local_end)


def _syntax_role_at(source_index: int, spans: list[tuple[int, int, str]]) -> str:
    for start, end, role in spans:
        if start <= source_index < end:
            return role
    return "plain"


def _range_overlaps_any(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(range_start < end and start < range_end for range_start, range_end in ranges)


def _terminal_color(preferred_256: int, fallback: int) -> int:
    try:
        if curses.has_colors() and getattr(curses, "COLORS", 0) > preferred_256:
            return preferred_256
    except curses.error:
        pass
    return fallback


def _scroll_footer_text(above: int, below: int) -> str:
    parts: list[str] = []
    if above:
        parts.append(f"^ {above} above")
    if below:
        parts.append(f"v {below} more below")
    return "  ".join(parts)


def _mouse_mask(name: str) -> int:
    return getattr(curses, name, 0)


def _mouse_primary_down(button: int) -> bool:
    return bool(button & (_mouse_mask("BUTTON1_PRESSED") | _mouse_mask("BUTTON1_CLICKED")))


def _mouse_drag_or_release(button: int) -> bool:
    return bool(button & (_mouse_mask("REPORT_MOUSE_POSITION") | _mouse_mask("BUTTON1_RELEASED")))


def _is_enter(key: int | str) -> bool:
    return key in (curses.KEY_ENTER, 10, 13, "\n", "\r")


def _is_comment_newline(key: int | str) -> bool:
    return key in (10, "\n", "\x0a", 14, "\x0e")


def _is_comment_line_start_key(key: int | str) -> bool:
    return key in (1, "\x01")


def _is_comment_line_end_key(key: int | str) -> bool:
    return key in (5, "\x05")


def _is_comment_word_left_key(key: int | str) -> bool:
    return key in (COMMENT_WORD_LEFT_KEY, "\x1bb", "\x1bB") or _is_modified_left_key(key)


def _is_comment_word_right_key(key: int | str) -> bool:
    return key in (COMMENT_WORD_RIGHT_KEY, "\x1bf", "\x1bF") or _is_modified_right_key(key)


def _is_comment_word_delete_key(key: int | str) -> bool:
    return key in (23, "\x17")


def _is_ctrl_c(key: int | str) -> bool:
    return key in (3, "\x03")


def _is_escape(key: int | str) -> bool:
    return key in (27, "\x1b")


def _is_up_key(key: int | str) -> bool:
    return key in (curses.KEY_UP, "k", ord("k"))


def _is_down_key(key: int | str) -> bool:
    return key in (curses.KEY_DOWN, "j", ord("j"))


def _is_backspace(key: int | str) -> bool:
    return key in (curses.KEY_BACKSPACE, 127, 8, "\x7f", "\b")


def _is_comment_delete_key(key: int | str) -> bool:
    return key == curses.KEY_DC or _is_backspace(key)


def _printable_key(key: int | str) -> str | None:
    if isinstance(key, str) and len(key) == 1 and key.isprintable():
        return key
    if isinstance(key, int) and 32 <= key <= 126:
        return chr(key)
    return None


def _normalize_comment_key(key: int | str) -> int | str:
    if isinstance(key, str) and key.startswith("\x1b") and len(key) > 1:
        decoded = _decode_comment_escape_sequence(list(key[1:]))
        if decoded is not None:
            return decoded
    return key


def _escape_sequence_complete(sequence: list[int | str]) -> bool:
    if not sequence:
        return False
    text = _key_sequence_text(sequence)
    if text == "\x1b":
        return False
    if text in {"b", "B", "f", "F"}:
        return True
    if text.startswith("\x1b["):
        return len(text) >= 3 and (text[-1].isalpha() or text[-1] == "~")
    if text.startswith("\x1bO"):
        return len(text) >= 3
    if text.startswith("O"):
        return len(text) >= 2
    if text.startswith("["):
        return len(text) >= 2 and (text[-1].isalpha() or text[-1] == "~")
    return True


def _decode_comment_escape_sequence(sequence: list[int | str]) -> int | str | None:
    if len(sequence) == 1 and _is_modified_left_key(sequence[0]):
        return COMMENT_WORD_LEFT_KEY
    if len(sequence) == 1 and _is_modified_right_key(sequence[0]):
        return COMMENT_WORD_RIGHT_KEY
    if sequence == [curses.KEY_LEFT]:
        return COMMENT_WORD_LEFT_KEY
    if sequence == [curses.KEY_RIGHT]:
        return COMMENT_WORD_RIGHT_KEY
    text = _key_sequence_text(sequence)
    if text in {"b", "B"}:
        return COMMENT_WORD_LEFT_KEY
    if text in {"f", "F"}:
        return COMMENT_WORD_RIGHT_KEY
    if text in {"\x1b[D", "\x1bOD"}:
        return COMMENT_WORD_LEFT_KEY
    if text in {"\x1b[C", "\x1bOC"}:
        return COMMENT_WORD_RIGHT_KEY
    if text in {"[D", "OD"}:
        return curses.KEY_LEFT
    if text in {"[C", "OC"}:
        return curses.KEY_RIGHT
    if text in {"[A", "OA"}:
        return curses.KEY_UP
    if text in {"[B", "OB"}:
        return curses.KEY_DOWN
    if _is_modified_horizontal_escape(text, "D"):
        return COMMENT_WORD_LEFT_KEY
    if _is_modified_horizontal_escape(text, "C"):
        return COMMENT_WORD_RIGHT_KEY
    return None


def _is_modified_horizontal_escape(text: str, final: str) -> bool:
    if not text.startswith("[") or not text.endswith(final):
        return False
    return any(modifier in text for modifier in (";3", ";5", ";7", ";9"))


def _is_modified_left_key(key: int | str) -> bool:
    return _modified_arrow_suffix(key, "kLFT")


def _is_modified_right_key(key: int | str) -> bool:
    return _modified_arrow_suffix(key, "kRIT")


def _modified_arrow_suffix(key: int | str, prefix: str) -> bool:
    name = _curses_key_name(key)
    if name is None or not name.startswith(prefix):
        return False
    suffix = name.removeprefix(prefix)
    return suffix.isdigit() and int(suffix) >= 3


def _curses_key_name(key: int | str) -> str | None:
    if not isinstance(key, int):
        return None
    try:
        raw = curses.keyname(key)
    except curses.error:
        return None
    return raw.decode("ascii", "ignore")


def _key_sequence_text(sequence: list[int | str]) -> str:
    chars: list[str] = []
    for key in sequence:
        if isinstance(key, str):
            chars.append(key)
        elif 0 <= key <= 0x10FFFF:
            chars.append(chr(key))
    return "".join(chars)


def _body_width(width: int) -> int:
    return max(10, width - GUTTER_WIDTH - 1)


def _code_line_visual_height(text: str, width: int) -> int:
    return max(1, len(_wrap_text_segments(text, _body_width(width))))


def _comment_visual_height(text: str, width: int) -> int:
    return max(
        1,
        sum(max(1, len(_wrap_text_segments(line, _body_width(width)))) for line in _comment_display_lines(text)),
    )


def _line_number_text(number: int | None, visual_offset: int) -> str:
    if visual_offset > 0 or number is None:
        return "    "
    return str(number).rjust(4)


def _wrap_text(text: str, width: int) -> list[str]:
    return [chunk for chunk, _ in _wrap_text_segments(text, width)]


def _comment_display_lines(text: str) -> list[str]:
    if text == "":
        return [" "]
    return text.split("\n")


def _comment_line_bounds(text: str) -> list[tuple[int, int]]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    bounds: list[tuple[int, int]] = []
    for line_index, start in enumerate(starts):
        if line_index + 1 < len(starts):
            end = starts[line_index + 1] - 1
        else:
            end = len(text)
        bounds.append((start, end))
    return bounds


def _comment_cursor_line_column(text: str, cursor_index: int) -> tuple[int, int]:
    cursor_index = max(0, min(len(text), cursor_index))
    bounds = _comment_line_bounds(text)
    for line_index, (start, end) in enumerate(bounds):
        if cursor_index <= end:
            return line_index, cursor_index - start
    start, end = bounds[-1]
    return len(bounds) - 1, min(end - start, cursor_index - start)


def _comment_cursor_index_for_line_column(text: str, line_index: int, column: int) -> int:
    bounds = _comment_line_bounds(text)
    line_index = max(0, min(len(bounds) - 1, line_index))
    start, end = bounds[line_index]
    return min(end, start + max(0, column))


def _comment_cursor_line_bounds(text: str, cursor_index: int) -> tuple[int, int]:
    line_index, _ = _comment_cursor_line_column(text, cursor_index)
    return _comment_line_bounds(text)[line_index]


def _comment_word_cursor_index(text: str, cursor_index: int, direction: int) -> int:
    cursor_index = max(0, min(len(text), cursor_index))
    if direction < 0:
        return _previous_comment_word_index(text, cursor_index)
    if direction > 0:
        return _next_comment_word_index(text, cursor_index)
    return cursor_index


def _previous_comment_word_index(text: str, cursor_index: int) -> int:
    index = cursor_index
    while index > 0 and not _comment_word_char(text[index - 1]):
        index -= 1
    while index > 0 and _comment_word_char(text[index - 1]):
        index -= 1
    return index


def _next_comment_word_index(text: str, cursor_index: int) -> int:
    index = cursor_index
    length = len(text)
    while index < length and not _comment_word_char(text[index]):
        index += 1
    while index < length and _comment_word_char(text[index]):
        index += 1
    return index


def _comment_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _wrap_text_segments(text: str, width: int) -> list[tuple[str, int]]:
    if width <= 0:
        return [("", 0)]
    if text == "":
        return [("", 0)]
    chunks: list[tuple[str, int]] = []
    global_offset = 0
    for raw_line in text.splitlines() or [""]:
        if raw_line == "":
            chunks.append(("", global_offset))
        else:
            for start in range(0, len(raw_line), width):
                chunks.append((raw_line[start : start + width], global_offset + start))
        global_offset += len(raw_line) + 1
    return chunks


def _truncate(text: str, width: int) -> str:
    width = max(0, width)
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _comment_line_label(comment: ReviewComment) -> str:
    numbers = [line.primary_line for line in comment.selected_lines if line.primary_line is not None]
    if not numbers:
        return "?"
    start = min(numbers)
    end = max(numbers)
    if start == end:
        return str(start)
    return f"{start}-{end}"


def _comment_title(comment: ReviewComment) -> str:
    new_numbers = [line.new_line for line in comment.selected_lines if line.new_line is not None]
    old_numbers = [line.old_line for line in comment.selected_lines if line.old_line is not None]
    has_old_only = any(line.new_line is None and line.old_line is not None for line in comment.selected_lines)
    if new_numbers and not has_old_only:
        return _range_title("comment on line", "comment on lines", min(new_numbers), max(new_numbers))
    if old_numbers and not new_numbers:
        return _range_title("comment on old line", "comment on old lines", min(old_numbers), max(old_numbers))
    if old_numbers and new_numbers:
        old = _range_title("old", "old", min(old_numbers), max(old_numbers))
        new = _range_title("new", "new", min(new_numbers), max(new_numbers))
        return f"comment on {old}; {new}"
    return "comment"


def _range_title(single: str, plural: str, start: int, end: int) -> str:
    if start == end:
        return f"{single} {start}"
    return f"{plural} {start}-{end}"
