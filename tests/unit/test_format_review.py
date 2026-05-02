import unittest
from pathlib import Path

from review.diff_model import ReviewSource, create_review_file
from review.format_review import format_review
from review.review_state import ReviewState


class FormatReviewTests(unittest.TestCase):
    def test_empty_review_message(self):
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [])
        self.assertEqual(format_review(state), "No review comments.\n")

    def test_single_comment_output(self):
        file = create_review_file("src/app.ts", "modified", ["const a = 1;"], ["const a = 2;"])
        state = ReviewState(Path("/repo"), ReviewSource("branch", target_branch="main"), [file])
        state.add_comment("Use a named constant.")
        output = format_review(state)
        self.assertIn("Review comments for /repo", output)
        self.assertIn("Source: branch comparison against main", output)
        self.assertIn("File: src/app.ts", output)
        self.assertIn("Comment:\n~~~text\nUse a named constant.\n~~~", output)
        self.assertIn("```ts", output)

    def test_comment_output_includes_two_context_lines_around_selection(self):
        file = create_review_file(
            "src/app.py",
            "modified",
            ["line 1", "line 2", "old value", "line 4", "line 5", "line 6"],
            ["line 1", "line 2", "new value", "line 4", "line 5", "line 6"],
        )
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        added_row = next(line.index for line in file.lines if line.kind == "addition")
        state.select_range("src/app.py", added_row, added_row)
        state.add_comment("Comment with surrounding context.")

        output = format_review(state)

        self.assertIn("   2   line 2", output)
        self.assertIn("   3 - old value", output)
        self.assertIn("   3 + new value", output)
        self.assertIn("   4   line 4", output)
        self.assertIn("   5   line 5", output)
        self.assertNotIn("   1   line 1", output)

    def test_deleted_line_labels_old_side(self):
        file = create_review_file("src/app.js", "deleted", ["const a = 1;"], [])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.add_comment("This deletion needs explanation.")
        output = format_review(state)
        self.assertIn("Old line: 1", output)
        self.assertIn("   1 - const a = 1;", output)

    def test_fence_expands_for_backticks(self):
        file = create_review_file("README.md", "modified", ["```"], ["````"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.move_selection(1)
        state.add_comment("Fence should be safe.")
        output = format_review(state)
        self.assertIn("`````markdown", output)

    def test_comment_body_fence_expands_for_markdown_fences(self):
        file = create_review_file("README.md", "modified", ["old"], ["new"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.add_comment("Comment with a fence:\n~~~\n```")
        output = format_review(state)
        self.assertIn("Comment:\n~~~~text\nComment with a fence:\n~~~\n```\n~~~~", output)

    def test_multiple_comments_grouped_by_file(self):
        first = create_review_file("a.py", "modified", ["a", "b"], ["A", "b"])
        second = create_review_file("b.py", "modified", ["c"], ["C"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [first, second])
        state.select_file("a.py")
        state.add_comment("First")
        state.select_file("b.py")
        state.add_comment("Second")
        output = format_review(state)
        self.assertLess(output.index("File: a.py"), output.index("File: b.py"))


if __name__ == "__main__":
    unittest.main()
