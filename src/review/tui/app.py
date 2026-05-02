from __future__ import annotations

import curses
from typing import Literal

from ..diff_model import ReviewComment, ReviewLine
from ..review_state import DocumentItem, ReviewState
from .file_tree import FileTreeRow, build_file_tree, file_tree_row_index
from .highlight import syntax_spans


Focus = Literal["file", "review"]
GUTTER_WIDTH = 8


class ReviewApp:
    def __init__(self, state: ReviewState):
        self.state = state
        self.focus: Focus = "file"
        self.file_scroll = 0
        self.review_scroll = 0
        self.command_mode = False
        self.command_buffer = ""
        self.comment_mode = False
        self.comment_buffer = ""
        self.editing_comment_id: str | None = None
        self.status_message = "Tab switches panes. T toggles files. z centers code. :q quits."
        self.quit_requested = False
        self.confirm_empty_quit = False
        self.file_pane_visible = True
        self.mouse_drag_anchor: tuple[str, int] | None = None
        self.screen_map: dict[int, int] = {}
        self.left_width = 32
        self.content_height = 0
        self._color_pairs: dict[tuple[int, int], int] = {}
        self._next_color_pair = 1
        self.command_handlers = self._build_command_handlers()

    def run(self) -> ReviewState:
        curses.wrapper(self._main)
        return self.state

    def _main(self, stdscr) -> None:
        curses.curs_set(0)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
        try:
            curses.nonl()
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
            key = stdscr.get_wch()
            self._handle_key(key)

    def _init_colors(self) -> None:
        if not curses.has_colors():
            return
        self._color_pairs = {}
        self._next_color_pair = 1

    def _draw(self, stdscr) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 12 or width < 60:
            self._safe_addnstr(stdscr, 0, 0, "Terminal is too small for review. Resize to at least 60x12.", width - 1, curses.A_BOLD)
            stdscr.refresh()
            return

        self.content_height = height - 1
        self.left_width = max(24, min(42, width // 3)) if self.file_pane_visible else 0
        if self.file_pane_visible:
            for y in range(self.content_height):
                self._safe_addnstr(stdscr, y, self.left_width, "|", 1, self._style("muted"))
            self._draw_file_pane(stdscr, height, width)
        elif self.focus == "file":
            self.focus = "review"
        self._draw_review_pane(stdscr, height, width)
        self._draw_status(stdscr, height, width)
        stdscr.refresh()

    def _draw_file_pane(self, stdscr, height: int, width: int) -> None:
        title = " Modified files "
        attr = curses.A_BOLD | (curses.A_REVERSE if self.focus == "file" else self._style("header"))
        self._safe_addnstr(stdscr, 0, 0, title.ljust(self.left_width), self.left_width, attr)
        visible_height = self.content_height - 1
        rows = build_file_tree(self.state.files)
        selected_row = file_tree_row_index(rows, self.state.file_pane_index) if rows else 0
        self.file_scroll = max(0, min(self.file_scroll, max(0, len(rows) - visible_height)))
        if selected_row < self.file_scroll:
            self.file_scroll = selected_row
        elif selected_row >= self.file_scroll + visible_height:
            self.file_scroll = selected_row - visible_height + 1
        for screen_row, row_index in enumerate(range(self.file_scroll, min(len(rows), self.file_scroll + visible_height)), start=1):
            row = rows[row_index]
            text = self._file_tree_text(row)
            selected = row.kind == "file" and row.file_index == self.state.file_pane_index
            row_attr = curses.A_REVERSE if selected else curses.A_NORMAL
            if self.focus == "file" and selected:
                row_attr |= curses.A_BOLD
            if row.kind == "directory":
                row_attr |= curses.A_BOLD | self._style("muted")
            self._safe_addnstr(stdscr, screen_row, 0, text.ljust(self.left_width), self.left_width, row_attr)

    def _file_tree_text(self, row: FileTreeRow) -> str:
        indent = "  " * row.depth
        if row.kind == "directory":
            return f" {indent}{row.label}"
        if row.file_index is None:
            return ""
        file = self.state.files[row.file_index]
        comments = len(self.state.comments_for_file(file.path))
        comment_text = f" [{comments}]" if comments else ""
        return f" {indent}{file.status_marker()} {row.label}{comment_text}"

    def _draw_review_pane(self, stdscr, height: int, width: int) -> None:
        right_x = self.left_width + 1 if self.file_pane_visible else 0
        right_width = width - right_x
        items = self.state.document_items()
        if not items:
            self._safe_addnstr(stdscr, 0, right_x, " No changes ", right_width - 1, curses.A_BOLD)
            return
        self._ensure_selected_visible()
        if self.focus == "review":
            self.state.update_file_highlight_for_document_index(self.review_scroll)
        sticky = self._sticky_header(items)
        attr = curses.A_BOLD | (curses.A_REVERSE if self.focus == "review" else self._style("header"))
        self._safe_addnstr(stdscr, 0, right_x, f" {sticky} ".ljust(right_width), right_width - 1, attr)

        self.screen_map = {}
        y = 1
        index = self.review_scroll
        active_index = self.state.active_document_index()
        while y < self.content_height and index < len(items):
            item = items[index]
            used = self._draw_review_item(stdscr, y, right_x, right_width, item, index, index == active_index)
            for screen_y in range(y, min(self.content_height, y + used)):
                self.screen_map[screen_y] = index
            y += max(1, used)
            index += 1

    def _draw_review_item(
        self,
        stdscr,
        y: int,
        x: int,
        width: int,
        item: DocumentItem,
        document_index: int,
        selected: bool,
    ) -> int:
        if item.kind == "file_header":
            attr = curses.A_BOLD | self._style("header")
            self._safe_addnstr(stdscr, y, x, f" {item.text} ".ljust(width), width - 1, attr)
            return 1
        if item.kind == "metadata":
            self._safe_addnstr(stdscr, y, x, f"   {item.text}".ljust(width), width - 1, self._style("warning"))
            return 1
        if item.kind == "expansion" and item.expansion is not None:
            attr = self._style("link") | (curses.A_REVERSE if selected else curses.A_NORMAL)
            text = f"   ... {item.expansion.label()} ..."
            self._safe_addnstr(stdscr, y, x, text.ljust(width), width - 1, attr)
            return 1
        if item.kind == "comment" and item.comment is not None:
            return self._draw_comment(stdscr, y, x, width, item.comment.body, saved=True, selected=selected)
        if item.kind == "code" and item.line is not None and item.row_index is not None:
            used = self._draw_code_line(stdscr, y, x, width, item, selected)
            if self.comment_mode and self.state.is_row_in_selection(item.file_path, item.row_index):
                selected_range = self.state.selected_range()
                if selected_range and item.row_index == selected_range[1]:
                    used += self._draw_comment(
                        stdscr,
                        y + used,
                        x,
                        width,
                        self.comment_buffer or " ",
                        saved=False,
                    )
            return used
        return 1

    def _draw_code_line(self, stdscr, y: int, x: int, width: int, item: DocumentItem, selected: bool) -> int:
        line = item.line
        assert line is not None
        number = line.primary_line
        body_width = max(10, width - GUTTER_WIDTH - 1)
        chunks = _wrap_text_segments(line.text, body_width)
        selected_range = item.row_index is not None and self.state.is_row_in_selection(item.file_path, item.row_index)
        range_rail = selected_range or self._row_has_comment_range(item.file_path, item.row_index)
        background = self._line_background(line, selected or selected_range)
        modifiers = curses.A_NORMAL
        if selected_range:
            if item.row_index == self.state.anchor_row:
                modifiers |= curses.A_BOLD
            if item.row_index == self.state.active_row:
                modifiers |= curses.A_UNDERLINE
        elif selected:
            modifiers |= curses.A_UNDERLINE
        file = self.state.file_by_path(item.file_path)
        spans = syntax_spans(line.text, file.language)
        for visual_offset, (chunk, source_offset) in enumerate(chunks):
            if y + visual_offset >= self.content_height:
                break
            number_text = "    " if visual_offset > 0 or number is None else str(number).rjust(4)
            marker = line.marker if visual_offset == 0 else " "
            row_attr = self._style("plain", background, modifiers)
            self._safe_addnstr(stdscr, y + visual_offset, x, " " * max(0, width - 1), width - 1, row_attr)
            self._draw_code_gutter(stdscr, y + visual_offset, x, number_text, marker, range_rail, background, modifiers)
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
            )
        return max(1, len(chunks))

    def _draw_code_gutter(
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

    def _draw_comment(self, stdscr, y: int, x: int, width: int, text: str, *, saved: bool, selected: bool = False) -> int:
        lines = text.splitlines() or [" "]
        modifiers = curses.A_UNDERLINE if selected else curses.A_NORMAL
        attr = self._style("warning", "selection" if selected else None, modifiers) | (curses.A_BOLD if not saved else curses.A_NORMAL)
        used = 0
        for line in lines:
            if y + used >= self.content_height:
                break
            chunks = _wrap_text(line, max(10, width - GUTTER_WIDTH - 1))
            for offset, chunk in enumerate(chunks):
                if y + used >= self.content_height:
                    break
                self._draw_comment_gutter(stdscr, y + used, x, selected=selected)
                self._safe_addnstr(stdscr, y + used, x + GUTTER_WIDTH, chunk, width - GUTTER_WIDTH - 1, attr)
                used += 1
        return max(1, used)

    def _draw_comment_gutter(self, stdscr, y: int, x: int, *, selected: bool = False) -> None:
        background = "selection" if selected else None
        modifiers = curses.A_UNDERLINE if selected else curses.A_NORMAL
        self._safe_addnstr(stdscr, y, x, "     ", 5, self._style("line-number", background, modifiers))
        self._safe_addnstr(stdscr, y, x + 5, "|", 1, self._style("rail", background, modifiers))
        self._safe_addnstr(stdscr, y, x + 6, "  ", 2, self._style("line-number", background, modifiers))

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
    ) -> None:
        cursor = 0
        plain_attr = self._style("plain", background, modifiers)
        for start, end, role in spans:
            local_start = max(start, source_offset) - source_offset
            local_end = min(end, source_offset + len(text)) - source_offset
            if local_end <= 0 or local_start >= len(text) or local_start >= local_end:
                continue
            if local_start > cursor:
                self._safe_addnstr(stdscr, y, x + cursor, text[cursor:local_start], width - cursor, plain_attr)
            attr = self._style(role, background, modifiers)
            self._safe_addnstr(stdscr, y, x + local_start, text[local_start:local_end], width - local_start, attr)
            cursor = local_end
        if cursor < len(text):
            self._safe_addnstr(stdscr, y, x + cursor, text[cursor:], width - cursor, plain_attr)

    def _draw_status(self, stdscr, height: int, width: int) -> None:
        y = height - 1
        if self.command_mode:
            text = ":" + self.command_buffer
            attr = curses.A_REVERSE
        elif self.comment_mode:
            action = "Edit comment" if self.editing_comment_id else "New comment"
            text = f"{action}: Enter saves, Ctrl+J inserts newline, Esc cancels"
            attr = curses.A_REVERSE
        else:
            text = self.status_message
            attr = self._style("muted")
        self._safe_addnstr(stdscr, y, 0, text.ljust(width), width - 1, attr)

    def _handle_key(self, key: int | str) -> None:
        if self.comment_mode:
            self._handle_comment_key(key)
            return
        if self.command_mode:
            self._handle_command_key(key)
            return

        if key in (9, "\t"):
            self.confirm_empty_quit = False
            if self.file_pane_visible:
                self.focus = "review" if self.focus == "file" else "file"
            else:
                self.focus = "review"
            return
        if key in ("t", "T", ord("t"), ord("T")):
            self._toggle_file_pane()
            return
        if key in (ord(":"), ":"):
            self.command_mode = True
            self.command_buffer = ""
            return
        self.confirm_empty_quit = False
        if key in ("z", ord("z")):
            self._center_review_on_selection()
            return
        if key == curses.KEY_MOUSE:
            self._handle_mouse()
            return
        if self.focus == "file":
            self._handle_file_key(key)
        else:
            self._handle_review_key(key)

    def _handle_file_key(self, key: int | str) -> None:
        if key == curses.KEY_UP:
            self.review_scroll = self._move_file_tree_selection(-1)
        elif key == curses.KEY_DOWN:
            self.review_scroll = self._move_file_tree_selection(1)
        elif key == curses.KEY_PPAGE:
            self.review_scroll = self._move_file_tree_selection(-max(1, self.content_height - 3))
        elif key == curses.KEY_NPAGE:
            self.review_scroll = self._move_file_tree_selection(max(1, self.content_height - 3))
        elif _is_enter(key):
            if self.state.files:
                self.review_scroll = self.state.select_file(self.state.files[self.state.file_pane_index].path)
                self.focus = "review"

    def _handle_review_key(self, key: int | str) -> None:
        shift_up = {getattr(curses, "KEY_SR", -1000), getattr(curses, "KEY_SUP", -1001)}
        shift_down = {getattr(curses, "KEY_SF", -1002), getattr(curses, "KEY_SDOWN", -1003)}
        if key == curses.KEY_UP:
            self.state.move_selection(-1)
            self._keep_selection_in_editor_view()
        elif key == curses.KEY_DOWN:
            self.state.move_selection(1)
            self._keep_selection_in_editor_view()
        elif key == curses.KEY_PPAGE:
            self._page_review_selection(-1)
        elif key == curses.KEY_NPAGE:
            self._page_review_selection(1)
        elif key in shift_up:
            self.state.extend_selection(-1)
            self._keep_selection_in_editor_view()
        elif key in shift_down:
            self.state.extend_selection(1)
            self._keep_selection_in_editor_view()
        elif key == curses.KEY_DC:
            self._delete_selected_comment()
        elif _is_enter(key):
            action = self.state.activate_selection()
            if action == "comment":
                self.comment_mode = True
                self.comment_buffer = ""
            elif action == "edit-comment":
                self._start_edit_selected_comment()
            elif action == "expanded":
                self._keep_selection_in_editor_view()

    def _handle_command_key(self, key: int | str) -> None:
        if key in (27, "\x1b"):
            self.command_mode = False
            return
        if _is_backspace(key):
            self.command_buffer = self.command_buffer[:-1]
            return
        if _is_enter(key):
            command = self.command_buffer.strip()
            self.command_mode = False
            handler = self.command_handlers.get(command)
            if handler is not None:
                handler()
            else:
                self.status_message = f"Unknown command: {command}"
            return
        if isinstance(key, str) and len(key) == 1 and key.isprintable():
            self.command_buffer += key
        elif isinstance(key, int) and 32 <= key <= 126:
            self.command_buffer += chr(key)

    def _handle_comment_key(self, key: int | str) -> None:
        if key in (27, "\x1b"):
            self.comment_mode = False
            self.comment_buffer = ""
            self.editing_comment_id = None
            return
        if _is_backspace(key):
            self.comment_buffer = self.comment_buffer[:-1]
            return
        if key in (10, "\n", "\x0a", 14, "\x0e"):
            self.comment_buffer += "\n"
            return
        if _is_enter(key):
            if self.editing_comment_id:
                if self.state.update_comment(self.editing_comment_id, self.comment_buffer):
                    self.status_message = "Comment updated."
                else:
                    self.status_message = "Empty comments are ignored."
            elif self.state.add_comment(self.comment_buffer):
                self.status_message = "Comment saved."
                self.confirm_empty_quit = False
            else:
                self.status_message = "Empty comments are ignored."
            self.comment_mode = False
            self.comment_buffer = ""
            self.editing_comment_id = None
            return
        if key in (9, "\t"):
            self.comment_buffer += "\t"
            return
        if isinstance(key, str) and len(key) == 1 and key.isprintable():
            self.comment_buffer += key
        elif isinstance(key, int) and 32 <= key <= 126:
            self.comment_buffer += chr(key)

    def _build_command_handlers(self):
        return {
            "q": self._command_quit,
            "quit": self._command_quit,
            "q!": self._command_force_quit,
            "quit!": self._command_force_quit,
            "e": self._command_edit_comment,
            "edit": self._command_edit_comment,
            "edit-comment": self._command_edit_comment,
            "d": self._command_delete_comment,
            "delete": self._command_delete_comment,
            "delete-comment": self._command_delete_comment,
            "c": self._command_center,
            "center": self._command_center,
            "centre": self._command_center,
        }

    def _command_quit(self) -> None:
        if self.state.comments or self.confirm_empty_quit:
            self.quit_requested = True
        else:
            self.confirm_empty_quit = True
            self.status_message = "No comments were added. Type :q again to quit without comments."

    def _command_force_quit(self) -> None:
        self.quit_requested = True

    def _command_center(self) -> None:
        self._center_review_on_selection()

    def _command_edit_comment(self) -> None:
        self._start_edit_selected_comment()

    def _start_edit_selected_comment(self) -> None:
        comment = self.state.comment_for_selection()
        if comment is None:
            self.status_message = "No comment is attached to the selected line."
            return
        self.comment_mode = True
        self.editing_comment_id = comment.id
        self.comment_buffer = comment.body

    def _command_delete_comment(self) -> None:
        self._delete_selected_comment()

    def _delete_selected_comment(self) -> None:
        comment = self.state.comment_for_selection()
        if comment is None:
            self.status_message = "No comment is attached to the selected line."
        elif self.state.delete_comment(comment.id):
            self.status_message = "Comment deleted."
        else:
            self.status_message = "Comment was already deleted."

    def _toggle_file_pane(self) -> None:
        self.file_pane_visible = not self.file_pane_visible
        if not self.file_pane_visible:
            self.focus = "review"
            self.status_message = "Files pane hidden. Press T to show it."
        else:
            self.status_message = "Files pane shown. Press T to hide it."

    def _move_file_tree_selection(self, delta: int) -> int:
        rows = build_file_tree(self.state.files)
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
        try:
            _, x, y, _, button = curses.getmouse()
        except curses.error:
            return
        if y >= self.content_height:
            return
        wheel_up = getattr(curses, "BUTTON4_PRESSED", 0)
        wheel_down = getattr(curses, "BUTTON5_PRESSED", 0)
        if button & wheel_up:
            self.review_scroll = max(0, self.review_scroll - 3)
            self.state.update_file_highlight_for_document_index(self.review_scroll)
            self._select_first_visible_selectable()
            return
        if button & wheel_down:
            self.review_scroll = min(max(0, len(self.state.document_items()) - 1), self.review_scroll + 3)
            self.state.update_file_highlight_for_document_index(self.review_scroll)
            self._select_first_visible_selectable()
            return
        button1_pressed = getattr(curses, "BUTTON1_PRESSED", 0)
        button1_clicked = getattr(curses, "BUTTON1_CLICKED", 0)
        button1_released = getattr(curses, "BUTTON1_RELEASED", 0)
        report_position = getattr(curses, "REPORT_MOUSE_POSITION", 0)
        if x < self.left_width:
            self.focus = "file"
            self.mouse_drag_anchor = None
            rows = build_file_tree(self.state.files)
            row_index = self.file_scroll + y - 1
            if 0 <= row_index < len(rows):
                row = rows[row_index]
                if row.kind == "file" and row.file_index is not None:
                    self.review_scroll = self.state.select_file(self.state.files[row.file_index].path)
            return
        self.focus = "review"
        document_index = self.screen_map.get(y)
        if document_index is not None:
            item = self.state.document_items()[document_index]
            if item.kind == "code" and item.row_index is not None:
                if button & (button1_pressed | button1_clicked):
                    self.state.select_document_index(document_index)
                    self.mouse_drag_anchor = (item.file_path, item.row_index)
                elif button & (report_position | button1_released):
                    if self.mouse_drag_anchor and self.mouse_drag_anchor[0] == item.file_path:
                        self.state.select_range(self.mouse_drag_anchor[0], self.mouse_drag_anchor[1], item.row_index)
                    if button & button1_released:
                        self.mouse_drag_anchor = None
                else:
                    self.state.select_document_index(document_index)
            elif item.kind == "expansion" and item.expansion is not None and button & (button1_pressed | button1_clicked):
                self.mouse_drag_anchor = None
                self.review_scroll = self.state.expand_context(item.expansion.id)
            else:
                self.mouse_drag_anchor = None
                self.state.select_document_index(document_index)
                self.review_scroll = document_index

    def _ensure_selected_visible(self) -> None:
        active = self.state.active_document_index()
        items = self.state.document_items()
        viewport = self._review_viewport_height()
        max_scroll = max(0, len(items) - viewport)
        if active is None:
            self.review_scroll = max(0, min(self.review_scroll, max_scroll))
            return
        selected = active
        if selected < self.review_scroll:
            self.review_scroll = selected
        elif selected >= self.review_scroll + viewport:
            self.review_scroll = selected - viewport + 1
        self.review_scroll = max(0, min(self.review_scroll, max_scroll))

    def _select_first_visible_selectable(self) -> None:
        items = self.state.document_items()
        if not items:
            return
        viewport = self._review_viewport_height()
        end = min(len(items), self.review_scroll + viewport)
        for index in range(self.review_scroll, end):
            if items[index].selectable:
                self.state.select_document_index(index)
                return
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
        viewport = self._review_viewport_height()
        bottom_guard = 3
        target_offset = max(0, viewport - 1 - bottom_guard)
        max_scroll = max(0, len(items) - viewport)
        if active < self.review_scroll:
            self.review_scroll = active
        elif active > self.review_scroll + target_offset:
            self.review_scroll = active - target_offset
        self.review_scroll = max(0, min(max_scroll, self.review_scroll))
        self.state.update_file_highlight_for_document_index(active)

    def _review_viewport_height(self) -> int:
        return max(1, self.content_height - 1)

    def _row_has_comment_range(self, file_path: str, row_index: int | None) -> bool:
        if row_index is None:
            return False
        for comment in self.state.comments_for_file(file_path):
            start, end = comment.sorted_rows
            if start <= row_index <= end:
                return True
        return False

    @staticmethod
    def _line_background(line: ReviewLine | None, selected: bool) -> str | None:
        if line is not None and line.kind in {"addition", "deletion"}:
            return line.kind
        if selected:
            return "selection"
        return None

    def _style(self, role: str, background: str | None = None, modifiers: int = curses.A_NORMAL) -> int:
        return modifiers | self._role_modifier(role) | self._color_pair_attr(role, background)

    @staticmethod
    def _role_modifier(role: str) -> int:
        if role in {"keyword", "tag", "heading", "error", "rail"}:
            return curses.A_BOLD
        if role == "emphasis":
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
            return {
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
                "error": curses.COLOR_RED,
            }.get(role, curses.COLOR_BLACK)
        return {
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
        }.get(role, -1)

    @staticmethod
    def _background_color(background: str | None) -> int:
        if background == "addition":
            return _terminal_color(194, curses.COLOR_GREEN)
        if background == "deletion":
            return _terminal_color(224, curses.COLOR_RED)
        if background == "selection":
            return _terminal_color(153, curses.COLOR_CYAN)
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


def _terminal_color(preferred_256: int, fallback: int) -> int:
    try:
        if curses.has_colors() and getattr(curses, "COLORS", 0) > preferred_256:
            return preferred_256
    except curses.error:
        pass
    return fallback


def _is_enter(key: int | str) -> bool:
    return key in (curses.KEY_ENTER, 10, 13, "\n", "\r")


def _is_backspace(key: int | str) -> bool:
    return key in (curses.KEY_BACKSPACE, 127, 8, "\x7f", "\b")


def _wrap_text(text: str, width: int) -> list[str]:
    return [chunk for chunk, _ in _wrap_text_segments(text, width)]


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
