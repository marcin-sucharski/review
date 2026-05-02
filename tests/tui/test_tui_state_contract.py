import unittest
import curses
from pathlib import Path
from unittest import mock

from review.diff_model import ReviewSource, create_review_file
from review.review_state import ReviewState
from review.tui import app as tui_app
from review.tui.app import ReviewApp, _comment_title, _wrap_text, _wrap_text_segments
from review.tui.highlight import syntax_spans


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

    def test_empty_quit_exits_without_confirmation(self):
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [])
        app = ReviewApp(state)
        app.command_buffer = "q"
        app._handle_command_key(10)
        self.assertTrue(app.quit_requested)

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

    def test_ctrl_j_in_comment_input_inserts_newline(self):
        file = create_review_file("src/app.js", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.comment_mode = True

        for key in ["A", "\n", "B", "\r"]:
            app._handle_comment_key(key)

        self.assertEqual(state.comments[0].body, "A\nB")

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

        file = create_review_file("src/app.py", "modified", ["return 1"], ["return 2"])
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
            app._draw_code_line(FakeScreen(), 0, 0, 80, added_item, False)

        self.assertIn(("plain", "addition", curses.A_NORMAL), drawn_styles)
        self.assertIn(("line-number", "addition", curses.A_NORMAL), drawn_styles)
        self.assertTrue(any(role in {"keyword", "number"} and background == "addition" for role, background, _ in drawn_styles))
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

    def test_file_pane_navigation_follows_rendered_tree_file_order(self):
        files = [
            create_review_file("src/review/app.py", "modified", ["a"], ["b"]),
            create_review_file("src/review/tui/menu.py", "modified", ["a"], ["b"]),
        ]
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), files)
        app = ReviewApp(state)
        app.content_height = 20
        state.file_pane_index = 1

        app._handle_file_key(curses.KEY_DOWN)
        self.assertEqual(state.file_pane_index, 0)

        app._handle_file_key(curses.KEY_UP)
        self.assertEqual(state.file_pane_index, 1)

    def test_t_shortcut_hides_and_shows_file_pane(self):
        file = create_review_file("src/app.py", "modified", ["a"], ["b"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        app = ReviewApp(state)
        app.focus = "file"

        app._handle_key("T")
        self.assertFalse(app.file_pane_visible)
        self.assertEqual(app.focus, "review")

        app._handle_key("T")
        self.assertTrue(app.file_pane_visible)

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

    def test_anchor_and_active_rows_have_distinct_selection_attrs(self):
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
        self.assertTrue(any(attr & curses.A_BOLD for _, attr in anchor_screen.calls))
        self.assertTrue(any(attr & curses.A_UNDERLINE for _, attr in active_screen.calls))


if __name__ == "__main__":
    unittest.main()
