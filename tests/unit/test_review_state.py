import unittest
from pathlib import Path

from review.diff_model import ReviewFile, ReviewLine, ReviewSource, VisibleInterval, create_review_file
from review.review_state import ReviewState


def make_state():
    old = [f"line {index}" for index in range(80)]
    new = old.copy()
    new[40] = "line 40 changed"
    file = create_review_file("src/app.ts", "modified", old, new)
    state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
    return state


class ReviewStateTests(unittest.TestCase):
    def test_initial_selection(self):
        state = make_state()
        self.assertEqual(state.selected_file_path, "src/app.ts")
        self.assertEqual(state.selection_kind, "code")

    def test_select_file_returns_header_index(self):
        state = make_state()
        self.assertEqual(state.select_file("src/app.ts"), 0)
        self.assertEqual(state.file_pane_index, 0)

    def test_extend_selection_and_add_comment(self):
        state = make_state()
        state.extend_selection(1)
        state.extend_selection(1)
        comment = state.add_comment("Please check this range.")
        self.assertIsNotNone(comment)
        self.assertEqual(comment.file_path, "src/app.ts")
        self.assertEqual(len(comment.selected_lines), 3)
        items = state.document_items()
        self.assertTrue(any(item.kind == "comment" for item in items))

    def test_expansion_rows_reveal_context(self):
        old = [f"line {index}" for index in range(260)]
        new = old.copy()
        new[130] = "changed"
        file = create_review_file("large.py", "modified", old, new)
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        items = state.document_items()
        expansions = [item.expansion for item in items if item.kind == "expansion"]
        self.assertTrue(expansions)
        first = expansions[0]
        before = sum(interval.end - interval.start + 1 for interval in file.visible_intervals)
        state.expand_context(first.id)
        after = sum(interval.end - interval.start + 1 for interval in file.visible_intervals)
        self.assertGreater(after, before)

    def test_expansion_selects_first_commentable_row_when_gap_starts_with_metadata(self):
        file = ReviewFile(
            path="mixed.ts",
            status="modified",
            lines=[
                ReviewLine(0, "metadata", "-- Staged changes --"),
                *[ReviewLine(index, "context", f"line {index}", index, index) for index in range(1, 10)],
                ReviewLine(10, "addition", "changed", None, 10),
            ],
            visible_intervals=[VisibleInterval(10, 10)],
        )
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        expansion = next(item.expansion for item in state.document_items() if item.kind == "expansion")
        state.expand_context(expansion.id)
        self.assertEqual(state.active_row, 1)
        self.assertEqual(state.activate_selection(), "comment")

    def test_selection_does_not_cross_file_boundary_when_extending(self):
        first = create_review_file("a.py", "modified", ["a"], ["A", "AA"])
        second = create_review_file("b.py", "modified", ["b"], ["B"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [first, second])
        state.select_file("a.py")
        for _ in range(10):
            state.extend_selection(1)
        self.assertEqual(state.selected_file_path, "a.py")
        self.assertEqual(state.file_pane_index, 0)

    def test_scroll_highlight_updates_file(self):
        first = create_review_file("a.py", "modified", ["a"], ["A"])
        second = create_review_file("b.py", "modified", ["b"], ["B"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [first, second])
        second_header = state.select_file("b.py")
        state.update_file_highlight_for_document_index(second_header)
        self.assertEqual(state.selected_file_path, "b.py")
        self.assertEqual(state.file_pane_index, 1)

    def test_scroll_highlight_does_not_corrupt_line_selection(self):
        first = create_review_file("a.py", "modified", ["a"], ["A"])
        second = create_review_file("b.py", "modified", ["b"], ["B"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [first, second])
        state.select_file("a.py")
        first_selection = state.selected_file_path, state.active_row
        second_header = state.select_file("b.py")
        state.select_file("a.py")
        state.update_file_highlight_for_document_index(second_header)
        self.assertEqual((state.selected_file_path, state.active_row), first_selection)
        self.assertEqual(state.file_pane_index, 1)

    def test_select_binary_file_keeps_metadata_view_without_selectable_fallback(self):
        text_file = create_review_file("a.py", "modified", ["a"], ["A"])
        binary_file = create_review_file("image.bin", "added", [], [], binary=True)
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [text_file, binary_file])
        header_index = state.select_file("image.bin")
        self.assertEqual(state.selection_kind, "metadata")
        self.assertIsNone(state.active_document_index())
        self.assertEqual(state.file_for_document_index(header_index), "image.bin")

    def test_move_selection_from_metadata_file_uses_file_position(self):
        first = create_review_file("a.py", "modified", ["a"], ["A"])
        binary_file = create_review_file("image.bin", "added", [], [], binary=True)
        third = create_review_file("c.py", "modified", ["c"], ["C"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [first, binary_file, third])
        state.select_file("image.bin")
        state.move_selection(1)
        self.assertEqual(state.selected_file_path, "c.py")
        self.assertEqual(state.file_pane_index, 2)
        state.select_file("image.bin")
        state.move_selection(-1)
        self.assertEqual(state.selected_file_path, "a.py")
        self.assertEqual(state.file_pane_index, 0)

    def test_initial_file_highlight_matches_first_selectable_file(self):
        binary_file = create_review_file("image.bin", "added", [], [], binary=True)
        text_file = create_review_file("a.py", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [binary_file, text_file])
        self.assertEqual(state.selected_file_path, "a.py")
        self.assertEqual(state.file_pane_index, 1)

    def test_initial_metadata_only_file_is_not_commentable(self):
        rename_only = create_review_file(
            "new.py",
            "renamed",
            [],
            [],
            old_path="old.py",
            metadata=["Renamed without content changes"],
        )
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [rename_only])
        self.assertEqual(state.selected_file_path, "new.py")
        self.assertEqual(state.file_pane_index, 0)
        self.assertEqual(state.selection_kind, "metadata")
        self.assertIsNone(state.active_row)
        self.assertEqual(state.activate_selection(), "none")

    def test_initial_selection_skips_visible_metadata_row(self):
        mixed_file = ReviewFile(
            path="mixed.ts",
            status="modified",
            lines=[
                ReviewLine(0, "metadata", "-- Staged changes --"),
                ReviewLine(1, "addition", "export const value = 1;", None, 1),
            ],
            visible_intervals=[VisibleInterval(0, 1)],
        )
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [mixed_file])
        self.assertEqual(state.selected_file_path, "mixed.ts")
        self.assertEqual(state.active_row, 1)
        self.assertEqual(state.selection_kind, "code")
        self.assertEqual(state.activate_selection(), "comment")

    def test_select_range_update_and_delete_comment(self):
        file = create_review_file("a.py", "modified", ["a", "b", "c"], ["A", "B", "c"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.select_range("a.py", 0, 3)
        self.assertEqual(state.selected_range(), (0, 3))
        comment = state.add_comment("Original")
        self.assertIsNotNone(comment)
        self.assertEqual(state.comment_for_selection().body, "Original")
        updated = state.update_comment(comment.id, "Updated")
        self.assertIsNotNone(updated)
        self.assertEqual(state.comment_for_selection().body, "Updated")
        self.assertTrue(state.delete_comment(comment.id))
        self.assertIsNone(state.comment_for_selection())

    def test_saved_comment_rows_are_selectable_for_edit_and_delete(self):
        file = create_review_file("a.py", "modified", ["a"], ["A"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        comment = state.add_comment("Original")
        self.assertIsNotNone(comment)

        comment_index = next(index for index, item in enumerate(state.document_items()) if item.kind == "comment")
        state.select_document_index(comment_index)

        self.assertEqual(state.selection_kind, "comment")
        self.assertEqual(state.active_document_index(), comment_index)
        self.assertEqual(state.comment_for_selection(), comment)
        self.assertEqual(state.activate_selection(), "edit-comment")
        self.assertTrue(state.delete_comment(comment.id))
        self.assertIsNone(state.comment_for_selection())

    def test_range_selection_does_not_cross_hidden_expansion_gap(self):
        old = [f"line {index}" for index in range(320)]
        new = old.copy()
        new[50] = "changed 50"
        new[260] = "changed 260"
        file = create_review_file("large.py", "modified", old, new)
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        self.assertGreaterEqual(len(file.visible_intervals), 2)
        first_interval = file.visible_intervals[0]
        second_interval = file.visible_intervals[1]
        state.select_range("large.py", first_interval.end, first_interval.end)
        state.extend_selection(1)
        self.assertEqual(state.selected_range(), (first_interval.end, first_interval.end))
        state.select_range("large.py", first_interval.end, second_interval.start)
        self.assertEqual(state.selected_range(), (first_interval.end, first_interval.end))

    def test_shift_selection_does_not_cross_metadata_section_separator(self):
        mixed_file = ReviewFile(
            path="mixed.ts",
            status="modified",
            lines=[
                ReviewLine(0, "metadata", "-- Staged changes --"),
                ReviewLine(1, "addition", "staged one", None, 1),
                ReviewLine(2, "addition", "staged two", None, 2),
                ReviewLine(3, "metadata", "-- Worktree changes --"),
                ReviewLine(4, "deletion", "staged two", 2, None),
                ReviewLine(5, "addition", "worktree two", None, 2),
            ],
            visible_intervals=[VisibleInterval(0, 5)],
        )
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [mixed_file])
        state.extend_selection(1)
        state.extend_selection(1)
        self.assertEqual(state.selected_range(), (1, 2))
        comment = state.add_comment("Keep this range within one section.")
        self.assertIsNotNone(comment)
        self.assertEqual(len(comment.selected_lines), 2)


if __name__ == "__main__":
    unittest.main()
