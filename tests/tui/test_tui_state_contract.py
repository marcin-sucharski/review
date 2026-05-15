import unittest
import curses
from pathlib import Path
from unittest import mock

from review.diff_model import ReviewSource, create_review_file
from review.review_state import ReviewState
from review.tui import app as tui_app
from review.tui.app import (
    ReviewApp,
    _comment_title,
    _decode_sgr_mouse_sequence,
    _literal_match_ranges,
    _syntax_segments,
    _wrap_text,
    _wrap_text_segments,
)
from review.tui.highlight import syntax_spans


def _empty_draw_frame():
    return tui_app.DrawFrame(None, None, frozenset(), None, {})


class TuiStateContractTests(unittest.TestCase):
    def test_document_contains_file_headers_code_expansions_and_comments(self):
        old = [f"line {index}" for index in range(240)]
        new = old.copy()
        new[120] = "changed"
        file = create_review_file("src/large.js", "modified", old, new)
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.add_comment("Check this.")
        kinds = [item.kind for item in state.document_items()]
        self.assertIn("file_header", kinds)
        self.assertIn("code", kinds)
        self.assertIn("expansion", kinds)
        self.assertIn("comment", kinds)

    def test_wrapping_preserves_long_logical_line(self):
        wrapped = _wrap_text("abcdefghij", 4)
        self.assertEqual(wrapped, ["abcd", "efgh", "ij"])
        self.assertEqual(_wrap_text_segments("abcdefghij", 4), [("abcd", 0), ("efgh", 4), ("ij", 8)])

    def test_initial_file_focus_keeps_first_selectable_file_highlight(self):
        class FakeScreen:
            def addnstr(self, y, x, text, n, attr):
                return None

        binary_file = create_review_file("image.bin", "added", [], [], binary=True)
        text_file = create_review_file("src/app.js", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [binary_file, text_file])
        app = ReviewApp(state)
        app.content_height = 20
        app.left_width = 28
        app._draw_review_pane(FakeScreen(), 20, 100)
        self.assertEqual(state.file_pane_index, 1)
        app._handle_file_key("\n")
        self.assertEqual(state.selected_file_path, "src/app.js")
        self.assertEqual(state.selection_kind, "code")

    def test_mouse_wheel_can_land_on_metadata_only_viewport(self):
        first = create_review_file("a.py", "modified", ["a"], ["A"])
        binary_file = create_review_file("image.bin", "added", [], [], binary=True)
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [first, binary_file])
        app = ReviewApp(state)
        items = state.document_items()
        binary_header_index = next(
            index
            for index, item in enumerate(items)
            if item.kind == "file_header" and item.file_path == "image.bin"
        )
        app.content_height = 3
        app.review_scroll = binary_header_index
        app._select_first_visible_selectable()
        self.assertEqual(state.selected_file_path, "image.bin")
        self.assertEqual(state.selection_kind, "metadata")
        self.assertIsNone(state.active_document_index())
        app._ensure_selected_visible()
        self.assertEqual(app.review_scroll, binary_header_index)

    def test_sgr_mouse_wheel_decodes_large_columns(self):
        event = _decode_sgr_mouse_sequence(list("[<65;260;7M"))

        self.assertEqual(event, (259, 6, tui_app.curses.BUTTON5_PRESSED))

    def test_pending_mouse_wheel_scrolls_from_far_right_review_pane(self):
        file = create_review_file("src/app.py", "added", [], [f"line {index}" for index in range(80)])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.content_height = 20
        app.left_width = 32
        app.file_pane_visible = True
        app._pending_mouse_event = (260, 8, tui_app.curses.BUTTON5_PRESSED)

        app._handle_mouse()

        self.assertEqual(app.review_scroll, tui_app.MOUSE_SCROLL_LINES)

    def test_comment_escape_reader_preserves_raw_sgr_mouse_event(self):
        class FakeScreen:
            def __init__(self):
                self.keys = iter("[<65;260;7M")

            def timeout(self, _value):
                return None

            def get_wch(self):
                try:
                    return next(self.keys)
                except StopIteration as exc:
                    raise curses.error from exc

        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))

        key = app._read_comment_escape_key(FakeScreen())

        self.assertEqual(key, tui_app.curses.KEY_MOUSE)
        self.assertEqual(app._pending_mouse_event, (259, 6, tui_app.curses.BUTTON5_PRESSED))

    def test_empty_quit_exits_without_confirmation(self):
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [])
        app = ReviewApp(state)
        app.command_buffer = "q"
        app._handle_command_key(10)
        self.assertTrue(app.quit_requested)

    def test_slash_search_starts_from_current_selection(self):
        file = create_review_file("notes.md", "added", [], ["alpha", "needle one", "middle", "needle two"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.content_height = 10
        state.move_selection(2)

        app._handle_key("/")
        for key in "needle":
            app._handle_key(key)
        app._handle_key("\r")

        self.assertEqual(app.search_query, "needle")
        self.assertEqual(file.lines[state.active_row].text, "needle two")
        self.assertIn("Match 2/2", app.status_message)

    def test_search_next_previous_and_empty_search_clear(self):
        file = create_review_file("notes.md", "added", [], ["needle one", "middle", "needle two"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.content_height = 10

        app._handle_key("/")
        for key in "needle":
            app._handle_key(key)
        app._handle_key("\r")
        self.assertEqual(file.lines[state.active_row].text, "needle one")

        app._handle_key("n")
        self.assertEqual(file.lines[state.active_row].text, "needle two")

        app._handle_key("p")
        self.assertEqual(file.lines[state.active_row].text, "needle one")

        app._handle_key("/")
        app._handle_key("\r")
        self.assertEqual(app.search_query, "")
        self.assertEqual(app.status_message, "Search cleared.")

    def test_search_escape_cancels_without_clearing_active_search(self):
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [create_review_file("notes.md", "added", [], ["needle"])])
        app = ReviewApp(state)
        app.search_query = "needle"

        app._handle_key("/")
        for key in "other":
            app._handle_key(key)
        app._handle_key("\x1b")

        self.assertFalse(app.search_mode)
        self.assertEqual(app.search_query, "needle")
        self.assertEqual(app.status_message, tui_app.SEARCH_CANCELLED_MESSAGE)

    def test_search_match_helpers_are_literal_and_split_syntax_for_highlight(self):
        self.assertEqual(_literal_match_ranges("needle needle", "needle"), [(0, 6), (7, 13)])
        segments = _syntax_segments("needle()", 0, [(0, 6, "function"), (6, 8, "punctuation")], [(0, 6)])

        self.assertEqual(
            segments,
            [(0, 6, "function", True), (6, 8, "punctuation", False)],
        )

    def test_file_pane_is_hidden_by_default(self):
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [])
        app = ReviewApp(state)

        self.assertFalse(app.file_pane_visible)
        self.assertEqual(app.focus, "review")

    def test_ctrl_c_requires_double_press_in_tui(self):
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [])
        app = ReviewApp(state)

        app._handle_key("\x03")

        self.assertFalse(app.quit_requested)
        self.assertTrue(app.interrupt_armed)
        self.assertEqual(app._status_line()[0], tui_app.INTERRUPT_CONFIRMATION_MESSAGE)

        app._handle_key("\x03")

        self.assertTrue(app.quit_requested)

    def test_ctrl_c_confirmation_resets_after_other_key(self):
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [])
        app = ReviewApp(state)

        app._handle_key("\x03")
        app._handle_key("z")
        app._handle_key("\x03")

        self.assertFalse(app.quit_requested)
        self.assertTrue(app.interrupt_armed)

    def test_comment_title_includes_referenced_range(self):
        file = create_review_file("src/app.js", "modified", ["a", "b"], ["A", "B"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.extend_selection(1)
        comment = state.add_comment("Range comment")
        self.assertIsNotNone(comment)
        self.assertEqual(_comment_title(comment), "comment on old lines 1-2")

    def test_command_mode_edits_and_deletes_comment_at_selection(self):
        file = create_review_file("src/app.js", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        comment = state.add_comment("Original")
        self.assertIsNotNone(comment)
        app = ReviewApp(state)
        self.assertIn("edit", app.command_handlers)
        self.assertIn("delete", app.command_handlers)
        app.command_buffer = "edit"
        app._handle_command_key(10)
        self.assertTrue(app.comment_mode)
        self.assertEqual(app.comment_buffer, "Original")
        app.comment_buffer = "Updated"
        app._handle_comment_key("\r")
        self.assertEqual(state.comments[0].body, "Updated")
        app.command_buffer = "delete"
        app._handle_command_key(10)
        self.assertEqual(state.comments, [])

    def test_comment_input_accepts_unicode_characters(self):
        file = create_review_file("src/app.js", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.comment_mode = True
        for char in "Zażółć 😀":
            app._handle_comment_key(char)
        app._handle_comment_key("\r")
        self.assertEqual(state.comments[0].body, "Zażółć 😀")

    def test_new_comment_is_focused_after_submit_so_enter_edits_it(self):
        file = create_review_file("src/app.js", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)

        app._handle_review_key(curses.KEY_ENTER)
        for key in ["F", "i", "x", "\r"]:
            app._handle_comment_key(key)

        comment = state.comments[0]
        self.assertFalse(app.comment_mode)
        self.assertEqual(state.selection_kind, "comment")
        self.assertEqual(state.selected_comment_id, comment.id)
        self.assertEqual(state.comment_for_selection(), comment)

        app._handle_review_key(curses.KEY_ENTER)

        self.assertTrue(app.comment_mode)
        self.assertEqual(app.editing_comment_id, comment.id)
        self.assertEqual(app.comment_buffer, "Fix")

    def test_ctrl_j_in_comment_input_inserts_newline(self):
        file = create_review_file("src/app.js", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.comment_mode = True

        for key in ["A", "\n", "B", "\r"]:
            app._handle_comment_key(key)

        self.assertEqual(state.comments[0].body, "A\nB")

    def test_comment_input_left_right_arrows_edit_at_cursor(self):
        file = create_review_file("src/app.js", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.comment_mode = True

        for key in ["a", "c", curses.KEY_LEFT, "b", curses.KEY_RIGHT, "d", "\r"]:
            app._handle_comment_key(key)

        self.assertEqual(state.comments[0].body, "abcd")

    def test_comment_input_up_down_arrows_preserve_column_across_lines(self):
        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.comment_mode = True
        app.comment_buffer = "abc\ndef\nxy"
        app.comment_cursor_index = len(app.comment_buffer)

        app._handle_comment_key(curses.KEY_UP)
        app._handle_comment_key("Z")
        app._handle_comment_key(curses.KEY_DOWN)
        app._handle_comment_key("!")

        self.assertEqual(app.comment_buffer, "abc\ndeZf\nxy!")

    def test_comment_input_ctrl_a_moves_to_line_then_message_start(self):
        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.comment_mode = True
        app.comment_buffer = "one two\nthree four"
        app.comment_cursor_index = len("one two\nthr")

        app._handle_comment_key("\x01")
        self.assertEqual(app.comment_cursor_index, len("one two\n"))

        app._handle_comment_key("\x01")
        self.assertEqual(app.comment_cursor_index, 0)

    def test_comment_input_ctrl_e_moves_to_line_then_message_end(self):
        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.comment_mode = True
        app.comment_buffer = "one two\nthree four"
        app.comment_cursor_index = len("one")

        app._handle_comment_key("\x05")
        self.assertEqual(app.comment_cursor_index, len("one two"))

        app._handle_comment_key("\x05")
        self.assertEqual(app.comment_cursor_index, len(app.comment_buffer))

    def test_comment_input_option_arrows_move_by_word(self):
        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.comment_mode = True
        app.comment_buffer = "one two\nthree_four!"
        app.comment_cursor_index = len(app.comment_buffer)

        app._handle_comment_key(tui_app.COMMENT_WORD_LEFT_KEY)
        self.assertEqual(app.comment_cursor_index, len("one two\n"))

        app._handle_comment_key(tui_app.COMMENT_WORD_LEFT_KEY)
        self.assertEqual(app.comment_cursor_index, len("one "))

        app._handle_comment_key(tui_app.COMMENT_WORD_RIGHT_KEY)
        self.assertEqual(app.comment_cursor_index, len("one two"))

        app._handle_comment_key(tui_app.COMMENT_WORD_RIGHT_KEY)
        self.assertEqual(app.comment_cursor_index, len("one two\nthree_four"))

    def test_comment_input_full_option_arrow_sequences_move_by_word(self):
        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.comment_mode = True
        app.comment_buffer = "one two three"
        app.comment_cursor_index = len(app.comment_buffer)

        app._handle_comment_key("\x1b[1;3D")
        self.assertEqual(app.comment_cursor_index, len("one two "))

        with mock.patch.object(tui_app.curses, "keyname", return_value=b"kLFT3"):
            app._handle_comment_key(555)
        self.assertEqual(app.comment_cursor_index, len("one "))

        app._handle_comment_key("\x1b[1;3C")
        self.assertEqual(app.comment_cursor_index, len("one two"))

        with mock.patch.object(tui_app.curses, "keyname", return_value=b"kRIT3"):
            app._handle_comment_key(570)
        self.assertEqual(app.comment_cursor_index, len("one two three"))

    def test_comment_input_meta_normal_arrow_sequences_move_by_word(self):
        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.comment_mode = True
        app.comment_buffer = "one two three"
        app.comment_cursor_index = len(app.comment_buffer)

        app._handle_comment_key("\x1b\x1b[D")
        self.assertEqual(app.comment_cursor_index, len("one two "))

        app.comment_cursor_index = len("one ")
        app._handle_comment_key("\x1b\x1b[C")
        self.assertEqual(app.comment_cursor_index, len("one two"))

    def test_comment_input_reads_option_arrow_escape_sequences(self):
        class FakeScreen:
            def __init__(self, keys):
                self.keys = list(keys)
                self.nodelay_values = []

            def get_wch(self):
                if not self.keys:
                    raise curses.error()
                return self.keys.pop(0)

            def nodelay(self, value):
                self.nodelay_values.append(value)

        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.comment_mode = True

        self.assertEqual(app._read_key(FakeScreen(["\x1b", "b"])), tui_app.COMMENT_WORD_LEFT_KEY)
        self.assertEqual(
            app._read_key(FakeScreen(["\x1b", "[", "1", ";", "3", "C"])),
            tui_app.COMMENT_WORD_RIGHT_KEY,
        )
        self.assertEqual(app._read_key(FakeScreen(["\x1b", curses.KEY_LEFT])), tui_app.COMMENT_WORD_LEFT_KEY)
        self.assertEqual(
            app._read_key(FakeScreen(["\x1b", "\x1b", "[", "D"])),
            tui_app.COMMENT_WORD_LEFT_KEY,
        )
        self.assertEqual(app._read_key(FakeScreen(["\x1b"])), "\x1b")

    def test_comment_input_backspace_deletes_before_cursor(self):
        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.comment_mode = True
        app.comment_buffer = "abc"
        app.comment_cursor_index = 1

        app._handle_comment_key("\b")

        self.assertEqual(app.comment_buffer, "bc")
        self.assertEqual(app.comment_cursor_index, 0)

    def test_comment_input_ctrl_w_deletes_previous_word(self):
        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.comment_mode = True
        app.comment_buffer = "one two   "
        app.comment_cursor_index = len(app.comment_buffer)

        app._handle_comment_key("\x17")

        self.assertEqual(app.comment_buffer, "one ")
        self.assertEqual(app.comment_cursor_index, len("one "))

    def test_comment_input_ctrl_w_deletes_word_before_cursor_without_touching_suffix(self):
        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.comment_mode = True
        app.comment_buffer = "one two three"
        app.comment_cursor_index = len("one two")

        app._handle_comment_key(23)

        self.assertEqual(app.comment_buffer, "one  three")
        self.assertEqual(app.comment_cursor_index, len("one "))

    def test_comment_input_trailing_newline_draws_blank_line_immediately(self):
        class FakeScreen:
            def __init__(self):
                self.calls = []

            def addnstr(self, y, x, text, n, attr):
                self.calls.append((y, x, text[:n]))

        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [])
        app = ReviewApp(state)
        app.content_height = 20
        screen = FakeScreen()

        used = app._draw_comment(screen, 0, 0, 80, "alpha\n", saved=False)

        rails = [(y, text) for y, x, text in screen.calls if x == 5 and text == "|"]
        self.assertEqual(used, 2)
        self.assertEqual(rails, [(0, "|"), (1, "|")])

    def test_comment_input_cursor_tracks_multiline_buffer_end(self):
        class FakeScreen:
            def __init__(self):
                self.calls = []

            def addnstr(self, y, x, text, n, attr):
                self.calls.append((y, x, text[:n]))

        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.content_height = 20
        screen = FakeScreen()

        app._draw_comment(screen, 3, 4, 80, "alpha\n", saved=False, cursor_index=len("alpha\n"))

        self.assertEqual(app.comment_cursor, (4, 4 + tui_app.GUTTER_WIDTH))

    def test_draw_makes_comment_input_cursor_visible(self):
        class FakeScreen:
            def __init__(self):
                self.moves = []

            def move(self, y, x):
                self.moves.append((y, x))

        app = ReviewApp(ReviewState(Path("/repo"), ReviewSource("uncommitted"), []))
        app.comment_mode = True
        app.comment_cursor = (5, 12)
        screen = FakeScreen()

        with mock.patch.object(tui_app.curses, "curs_set") as curs_set:
            app._apply_cursor(screen, 20, 80)

        curs_set.assert_called_once_with(1)
        self.assertEqual(screen.moves, [(5, 12)])

    def test_comment_mode_draw_uses_cached_selection_snapshot(self):
        class FakeScreen:
            def addnstr(self, y, x, text, n, attr):
                return None

        old = [f"line {index}" for index in range(40)]
        new = old.copy()
        new[10] = "changed"
        file = create_review_file("src/app.py", "modified", old, new)
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.extend_selection(1)
        app = ReviewApp(state)
        app.focus = "review"
        app.comment_mode = True
        app.comment_buffer = "one\ntwo"
        app.content_height = 20
        app.left_width = 24

        with mock.patch.object(state, "is_row_in_selection", side_effect=AssertionError("selection should be cached")):
            app._draw_review_pane(FakeScreen(), 20, 100)

    def test_selected_comment_enter_edits_and_delete_removes(self):
        file = create_review_file("src/app.js", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        comment = state.add_comment("Original")
        self.assertIsNotNone(comment)
        app = ReviewApp(state)
        comment_index = next(index for index, item in enumerate(state.document_items()) if item.kind == "comment")

        state.select_document_index(comment_index)
        app._handle_review_key(curses.KEY_ENTER)

        self.assertTrue(app.comment_mode)
        self.assertEqual(app.editing_comment_id, comment.id)
        self.assertEqual(app.comment_buffer, "Original")

        app._handle_comment_key("\x1b")
        state.select_document_index(comment_index)
        app._handle_review_key(curses.KEY_DC)

        self.assertEqual(state.comments, [])

    def test_selected_comment_backspace_removes(self):
        file = create_review_file("src/app.js", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        comment = state.add_comment("Original")
        self.assertIsNotNone(comment)
        app = ReviewApp(state)
        comment_index = next(index for index, item in enumerate(state.document_items()) if item.kind == "comment")

        state.select_document_index(comment_index)
        app._handle_review_key("\b")

        self.assertEqual(state.comments, [])
        self.assertEqual(app.status_message, "Comment deleted.")

    def test_comment_pane_backspace_removes_selected_comment(self):
        file = create_review_file("src/app.js", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        comment = state.add_comment("Original")
        self.assertIsNotNone(comment)
        app = ReviewApp(state)
        app.file_pane_visible = True
        app.focus = "comments"
        app._focus_comment_pane_selection()

        app._handle_comment_pane_key(curses.KEY_BACKSPACE)

        self.assertEqual(state.comments, [])
        self.assertEqual(app.status_message, "Comment deleted.")

    def test_j_and_k_navigate_panes_but_not_comment_edit(self):
        file = create_review_file("src/app.js", "modified", ["a", "b"], ["A", "B"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        first_index = state.active_document_index()
        app = ReviewApp(state)

        app._handle_review_key("j")
        self.assertNotEqual(state.active_document_index(), first_index)
        app._handle_review_key("k")
        self.assertEqual(state.active_document_index(), first_index)

        app.comment_mode = True
        app.comment_buffer = ""
        app._handle_key("j")
        app._handle_key("k")

        self.assertEqual(app.comment_buffer, "jk")

    def test_mouse_drag_extends_selection_range(self):
        file = create_review_file("src/app.js", "modified", ["a", "b"], ["A", "B"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.focus = "review"
        app.left_width = 24
        app.content_height = 20
        code_indices = [index for index, item in enumerate(state.document_items()) if item.kind == "code"]
        app.screen_map = {1: code_indices[0], 2: code_indices[-1]}
        with mock.patch.object(tui_app.curses, "getmouse", return_value=(0, 25, 1, 0, tui_app.curses.BUTTON1_PRESSED)):
            app._handle_mouse()
        self.assertEqual(app.review_scroll, 0)
        move_button = getattr(tui_app.curses, "REPORT_MOUSE_POSITION", tui_app.curses.BUTTON1_RELEASED)
        with mock.patch.object(tui_app.curses, "getmouse", return_value=(0, 25, 2, 0, move_button)):
            app._handle_mouse()
        self.assertEqual(state.selected_range(), (0, 3))

    def test_escape_cancels_multiline_selection(self):
        file = create_review_file("src/app.js", "modified", ["a", "b"], ["A", "B"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        state.extend_selection(1)

        active_row = state.active_row
        app._handle_key("\x1b")

        self.assertEqual(state.selected_range(), (active_row, active_row))
        self.assertEqual(app.status_message, "Selection cleared.")

    def test_syntax_renderer_preserves_selection_attr_on_code_body(self):
        class FakeScreen:
            def __init__(self):
                self.calls = []

            def addnstr(self, y, x, text, n, attr):
                self.calls.append((text, attr))

        file = create_review_file("src/app.js", "modified", ["const a = 1;"], ["const a = 2;"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        item = next(item for item in state.document_items() if item.kind == "code")
        screen = FakeScreen()
        app._draw_syntax(
            screen,
            0,
            0,
            item.line.text,
            80,
            0,
            syntax_spans(item.line.text, "javascript"),
            None,
            curses.A_REVERSE,
        )
        self.assertTrue(screen.calls)
        self.assertTrue(all(attr & curses.A_REVERSE for _, attr in screen.calls))

    def test_diff_background_is_kept_while_syntax_roles_are_drawn(self):
        class FakeScreen:
            def addnstr(self, y, x, text, n, attr):
                return None

        file = create_review_file(".gitignore", "modified", ["old"], ["*.py"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.content_height = 20
        drawn_styles = []

        def fake_style(role, background=None, modifiers=curses.A_NORMAL):
            drawn_styles.append((role, background, modifiers))
            return modifiers

        added_item = next(
            item
            for item in state.document_items()
            if item.kind == "code" and item.line is not None and item.line.kind == "addition"
        )
        with mock.patch.object(app, "_style", side_effect=fake_style):
            app._draw_code_line(FakeScreen(), 0, 0, 80, added_item, False, _empty_draw_frame())

        self.assertIn(("plain", "addition", curses.A_NORMAL), drawn_styles)
        self.assertIn(("line-number", "addition", curses.A_NORMAL), drawn_styles)
        self.assertTrue(any(role in {"operator", "string"} and background == "addition" for role, background, _ in drawn_styles))
        self.assertNotEqual(app._foreground_color("string", "addition"), curses.COLOR_GREEN)

    def test_center_command_scrolls_selection_to_middle_of_review_view(self):
        old = [f"line {index}" for index in range(80)]
        new = old.copy()
        new[60] = "changed"
        file = create_review_file("src/app.py", "modified", old, new)
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.move_selection(55)
        app = ReviewApp(state)
        app.content_height = 20
        app.review_scroll = 0

        active = state.active_document_index()
        self.assertIsNotNone(active)
        app._center_review_on_selection()

        viewport = app._review_viewport_height()
        self.assertEqual(app.review_scroll, max(0, active - viewport // 2))
        self.assertIn("center", app.command_handlers)

    def test_review_navigation_places_selection_three_lines_above_bottom(self):
        file = create_review_file("src/app.py", "modified", [f"line {index}" for index in range(100)], [f"line {index}" for index in range(100)])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.focus = "review"
        app.content_height = 20

        for _ in range(40):
            app._handle_review_key(curses.KEY_DOWN)

        active = state.active_document_index()
        self.assertIsNotNone(active)
        self.assertEqual(active - app.review_scroll, app._review_viewport_height() - 4)

    def test_review_navigation_keeps_wrapped_selection_inside_visible_rows_at_file_end(self):
        long_text = " ".join(["wrapped"] * 18)
        file = create_review_file("src/app.py", "added", [], [f"{index}: {long_text}" for index in range(8)])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.focus = "review"
        app.content_height = 8
        app.review_width = 34

        for _ in range(20):
            app._handle_review_key(curses.KEY_DOWN)

        items = state.document_items()
        active = state.active_document_index()
        self.assertIsNotNone(active)
        self.assertEqual(active, len(items) - 1)
        active_top = app._review_rows_between(items, app.review_scroll, active, app.review_width)
        active_height = app._review_item_height(items[active], app.review_width)

        self.assertGreater(active_height, 1)
        self.assertLessEqual(active_top + active_height, app._review_viewport_height())

    def test_page_navigation_keeps_screen_offset(self):
        file = create_review_file("src/app.py", "modified", [f"line {index}" for index in range(120)], [f"line {index}" for index in range(120)])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.focus = "review"
        app.content_height = 20
        for _ in range(8):
            app._handle_review_key(curses.KEY_DOWN)
        offset = state.active_document_index() - app.review_scroll

        app._handle_review_key(curses.KEY_NPAGE)

        self.assertEqual(state.active_document_index() - app.review_scroll, offset)

    def test_file_pane_renders_collapsed_directory_tree(self):
        class FakeScreen:
            def __init__(self):
                self.calls = []

            def addnstr(self, y, x, text, n, attr):
                self.calls.append((y, text[:n]))

        files = [
            create_review_file("src/review/cli.py", "modified", ["a"], ["b"]),
            create_review_file("src/review/tui/app.py", "modified", ["a"], ["b"]),
            create_review_file("tests/unit/test_cli.py", "modified", ["a"], ["b"]),
        ]
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), files)
        app = ReviewApp(state)
        app.content_height = 20
        app.left_width = 42
        screen = FakeScreen()

        app._draw_file_pane(screen, 20, 120)

        rendered = [text.strip() for _, text in screen.calls]
        self.assertIn("src/review/", rendered)
        self.assertIn("M cli.py", rendered)
        self.assertIn("tui/", rendered)
        self.assertIn("M app.py", rendered)
        self.assertIn("tests/unit/", rendered)
        self.assertIn("M test_cli.py", rendered)

    def test_left_pane_renders_grouped_comment_navigator(self):
        class FakeScreen:
            def __init__(self):
                self.calls = []

            def addnstr(self, y, x, text, n, attr):
                self.calls.append((y, x, text[:n]))

        first = create_review_file("src/app.py", "added", [], ["alpha", "beta"])
        second = create_review_file("tests/test_app.py", "added", [], ["case"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [first, second])
        state.add_comment("First comment that is deliberately long enough to truncate")
        state.select_range("src/app.py", 1, 1)
        state.add_comment("Second comment")
        state.select_file("tests/test_app.py")
        state.add_comment("Third comment")
        app = ReviewApp(state)
        app.content_height = 12
        app.left_width = 28
        screen = FakeScreen()

        app._draw_file_pane(screen, 12, 100)

        rendered = [(y, text.strip()) for y, _, text in screen.calls]
        self.assertIn((6, "Review comments"), rendered)
        self.assertTrue(any(y > 6 and text == "src/app.py" for y, text in rendered))
        self.assertTrue(any(y > 6 and text.startswith("1 First comment") and text.endswith("...") for y, text in rendered))
        self.assertTrue(any(y > 6 and text.startswith("2 Second comment") for y, text in rendered))
        self.assertTrue(any(y > 6 and text == "tests/test_app.py" for y, text in rendered))

    def test_file_pane_draws_bottom_scroll_indicator_when_more_files_follow(self):
        class FakeScreen:
            def __init__(self):
                self.calls = []

            def addnstr(self, y, x, text, n, attr):
                self.calls.append((y, text[:n]))

        files = [create_review_file(f"file_{index}.py", "modified", ["a"], ["b"]) for index in range(6)]
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), files)
        app = ReviewApp(state)
        app.left_width = 28
        screen = FakeScreen()

        app._draw_file_tree_region(screen, 0, 4)

        rendered = [(y, text.strip()) for y, text in screen.calls]
        self.assertIn((3, "v 4 more below"), rendered)

    def test_comment_pane_draws_bottom_scroll_indicator_when_more_comments_follow(self):
        class FakeScreen:
            def __init__(self):
                self.calls = []

            def addnstr(self, y, x, text, n, attr):
                self.calls.append((y, text[:n]))

        file = create_review_file("src/app.py", "added", [], [f"line {index}" for index in range(6)])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        for index in range(6):
            state.select_range("src/app.py", index, index)
            state.add_comment(f"Comment {index}")
        app = ReviewApp(state)
        app.left_width = 28
        screen = FakeScreen()

        app._draw_comment_list_region(screen, 0, 4)

        rendered = [(y, text.strip()) for y, text in screen.calls]
        self.assertIn((3, "v 5 more below"), rendered)

    def test_comment_pane_navigation_focuses_comment_in_review_pane(self):
        file = create_review_file("src/app.py", "added", [], ["alpha", "beta"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        first = state.add_comment("First comment")
        state.select_range("src/app.py", 1, 1)
        second = state.add_comment("Second comment")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        app = ReviewApp(state)
        app.file_pane_visible = True
        app.focus = "comments"
        app.content_height = 12

        app._focus_comment_pane_selection()
        self.assertEqual(state.selected_comment_id, first.id)
        app._handle_key("j")

        self.assertEqual(state.selected_comment_id, second.id)
        active_index = state.active_document_index()
        self.assertIsNotNone(active_index)
        self.assertEqual(state.document_items()[active_index].comment.id, second.id)

        app._handle_key("k")
        self.assertEqual(state.selected_comment_id, first.id)

    def test_tab_cycles_review_file_comments_when_left_pane_is_visible(self):
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [])
        app = ReviewApp(state)
        app.file_pane_visible = True

        app._handle_key("\t")
        self.assertEqual(app.focus, "file")
        app._handle_key("\t")
        self.assertEqual(app.focus, "comments")
        app._handle_key("\t")
        self.assertEqual(app.focus, "review")

    def test_file_pane_navigation_follows_rendered_tree_file_order(self):
        files = [
            create_review_file("src/review/app.py", "modified", ["a"], ["b"]),
            create_review_file("src/review/tui/menu.py", "modified", ["a"], ["b"]),
        ]
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), files)
        app = ReviewApp(state)
        app.content_height = 20
        state.file_pane_index = 1

        app._handle_file_key("j")
        self.assertEqual(state.file_pane_index, 0)

        app._handle_file_key("k")
        self.assertEqual(state.file_pane_index, 1)

    def test_t_shortcut_hides_and_shows_file_pane(self):
        file = create_review_file("src/app.py", "modified", ["a"], ["b"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)

        app._handle_key("T")
        self.assertTrue(app.file_pane_visible)

        app._handle_key("T")
        self.assertFalse(app.file_pane_visible)
        self.assertEqual(app.focus, "review")

    def test_range_selection_draws_visible_rail_on_each_selected_line(self):
        class FakeScreen:
            def __init__(self):
                self.calls = []

            def addnstr(self, y, x, text, n, attr):
                self.calls.append((y, x, text[:n]))

        file = create_review_file("plain.txt", "modified", ["a", "b"], ["A", "B"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.extend_selection(1)
        app = ReviewApp(state)
        app.content_height = 20
        items = [item for item in state.document_items() if item.kind == "code"]
        screen = FakeScreen()

        app._draw_code_line(screen, 0, 0, 80, items[0], False)
        app._draw_code_line(screen, 1, 0, 80, items[1], True)

        rails = [(y, x, text) for y, x, text in screen.calls if x == 5 and text == "|"]
        self.assertEqual([y for y, _, _ in rails], [0, 1])

    def test_saved_comment_display_is_body_only_with_aligned_rail(self):
        class FakeScreen:
            def __init__(self):
                self.calls = []

            def addnstr(self, y, x, text, n, attr):
                self.calls.append((y, x, text[:n]))

        file = create_review_file("plain.txt", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.add_comment("Only the comment body.")
        app = ReviewApp(state)
        app.content_height = 20
        screen = FakeScreen()
        comment_item = next(item for item in state.document_items() if item.kind == "comment")

        app._draw_review_item(screen, 0, 0, 80, comment_item, 0, False)

        rendered = "".join(text for _, _, text in screen.calls)
        self.assertIn("Only the comment body.", rendered)
        self.assertNotIn("comment on", rendered)
        self.assertTrue(any(x == 5 and text == "|" for _, x, text in screen.calls))

    def test_comment_edit_draws_live_keyboard_changes_before_submit(self):
        class FakeScreen:
            def __init__(self):
                self.calls = []

            def addnstr(self, y, x, text, n, attr):
                self.calls.append((y, x, text[:n]))

        file = create_review_file("plain.txt", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        comment = state.add_comment("Original")
        self.assertIsNotNone(comment)
        comment_index = next(index for index, item in enumerate(state.document_items()) if item.kind == "comment")
        state.select_document_index(comment_index)
        app = ReviewApp(state)
        app.content_height = 20

        app._handle_review_key(curses.KEY_ENTER)
        app._handle_comment_key("\b")
        comment_item = next(item for item in state.document_items() if item.kind == "comment")
        screen = FakeScreen()

        app._draw_review_item(screen, 0, 0, 80, comment_item, comment_index, True)

        rendered = "".join(text for _, _, text in screen.calls)
        self.assertIn("Origina", rendered)
        self.assertNotIn("Original", rendered)
        self.assertEqual(state.comments[0].body, "Original")

    def test_saved_comment_rows_use_distinct_background(self):
        class FakeScreen:
            def addnstr(self, y, x, text, n, attr):
                return None

        file = create_review_file("plain.txt", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.add_comment("Visible comment.")
        app = ReviewApp(state)
        app.content_height = 20
        comment_item = next(item for item in state.document_items() if item.kind == "comment")
        styles = []

        def fake_style(role, background=None, modifiers=curses.A_NORMAL):
            styles.append((role, background, modifiers))
            return curses.A_NORMAL

        with mock.patch.object(app, "_style", side_effect=fake_style):
            app._draw_review_item(FakeScreen(), 0, 0, 80, comment_item, 0, False)

        self.assertIn(("plain", "comment", curses.A_NORMAL), styles)
        self.assertIn(("warning", "comment", curses.A_NORMAL), styles)
        self.assertIn(("rail", "comment", curses.A_NORMAL), styles)
        self.assertNotEqual(app._background_color("comment"), app._background_color("addition"))
        self.assertNotEqual(app._background_color("comment"), app._background_color("deletion"))
        self.assertNotEqual(app._background_color("comment"), app._background_color("selection"))

    def test_selected_multiline_comment_uses_background_without_underlines(self):
        class FakeScreen:
            def addnstr(self, y, x, text, n, attr):
                return None

        file = create_review_file("plain.txt", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        comment = state.add_comment("Line one\nLine two")
        self.assertIsNotNone(comment)
        state.select_comment(comment.id)
        app = ReviewApp(state)
        app.content_height = 20
        comment_item = next(item for item in state.document_items() if item.kind == "comment")
        styles = []

        def fake_style(role, background=None, modifiers=curses.A_NORMAL):
            styles.append((role, background, modifiers))
            return modifiers

        with mock.patch.object(app, "_style", side_effect=fake_style):
            app._draw_review_item(FakeScreen(), 0, 0, 80, comment_item, state.active_document_index() or 0, True)

        self.assertTrue(any(background == "selection" for _, background, _ in styles))
        self.assertTrue(all(not modifiers & curses.A_UNDERLINE for _, _, modifiers in styles))

    def test_selected_code_rows_use_background_without_underlines(self):
        class FakeScreen:
            def __init__(self):
                self.calls = []

            def addnstr(self, y, x, text, n, attr):
                self.calls.append((text, attr))

        file = create_review_file("plain.txt", "modified", ["a", "b"], ["A", "B"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.extend_selection(1)
        app = ReviewApp(state)
        app.content_height = 20
        items = [item for item in state.document_items() if item.kind == "code"]
        anchor_screen = FakeScreen()
        active_screen = FakeScreen()
        app._draw_code_line(anchor_screen, 0, 0, 80, items[0], False)
        app._draw_code_line(active_screen, 0, 0, 80, items[1], True)
        attrs = [attr for _, attr in anchor_screen.calls + active_screen.calls]
        self.assertTrue(attrs)
        self.assertTrue(all(not attr & curses.A_UNDERLINE for attr in attrs))

    def test_selected_modified_lines_use_diff_selection_backgrounds(self):
        class FakeScreen:
            def addnstr(self, y, x, text, n, attr):
                return None

        file = create_review_file("plain.txt", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.content_height = 20
        items = [item for item in state.document_items() if item.kind == "code"]
        styles = []

        def fake_style(role, background=None, modifiers=curses.A_NORMAL):
            styles.append((role, background, modifiers))
            return curses.A_NORMAL

        with mock.patch.object(app, "_style", side_effect=fake_style):
            for item in items:
                app._draw_code_line(FakeScreen(), 0, 0, 80, item, True)

        backgrounds = {background for _, background, _ in styles}
        self.assertIn("addition-selection", backgrounds)
        self.assertIn("deletion-selection", backgrounds)
        self.assertNotIn("addition", backgrounds)
        self.assertNotIn("deletion", backgrounds)
        self.assertNotEqual(tui_app.BACKGROUND_COLORS["selection"][0], 153)


if __name__ == "__main__":
    unittest.main()
