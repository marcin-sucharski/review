import unittest
from unittest import mock

from review import diff_model
from review.diff_model import build_review_lines, create_review_file, initial_visible_intervals


class DiffModelTests(unittest.TestCase):
    def test_build_review_lines_modified_file(self):
        rows = build_review_lines(["a", "b", "c"], ["a", "B", "c", "d"])
        kinds = [row.kind for row in rows]
        self.assertEqual(kinds, ["context", "deletion", "addition", "context", "addition"])
        self.assertEqual(rows[1].old_line, 2)
        self.assertIsNone(rows[1].new_line)
        self.assertEqual(rows[2].new_line, 2)

    def test_added_file_lines_have_new_numbers(self):
        rows = build_review_lines([], ["one", "two"])
        self.assertEqual([row.kind for row in rows], ["addition", "addition"])
        self.assertEqual([row.new_line for row in rows], [1, 2])

    def test_deleted_file_lines_have_old_numbers(self):
        rows = build_review_lines(["one", "two"], [])
        self.assertEqual([row.kind for row in rows], ["deletion", "deletion"])
        self.assertEqual([row.old_line for row in rows], [1, 2])

    def test_initial_visibility_full_for_small_files(self):
        rows = build_review_lines([str(i) for i in range(10)], [str(i) for i in range(10)])
        intervals = initial_visible_intervals(rows, full_file_threshold=20)
        self.assertEqual([(interval.start, interval.end) for interval in intervals], [(0, 9)])

    def test_initial_visibility_around_change_for_large_files(self):
        old = [f"line {i}" for i in range(300)]
        new = old.copy()
        new[150] = "changed"
        rows = build_review_lines(old, new)
        intervals = initial_visible_intervals(rows, context_radius=20, full_file_threshold=100)
        self.assertEqual(len(intervals), 1)
        self.assertLessEqual(intervals[0].start, 150)
        self.assertGreaterEqual(intervals[0].end, 150)
        self.assertGreater(intervals[0].start, 0)
        self.assertLess(intervals[0].end, len(rows) - 1)

    def test_create_review_file_sets_language_and_status(self):
        file = create_review_file("src/Main.java", "modified", ["class A {}"], ["class B {}"])
        self.assertEqual(file.language, "java")
        self.assertEqual(file.status_marker(), "M")

    def test_large_repetitive_middle_enables_sequence_matcher_autojunk(self):
        old = ["old start", *(["same"] * 1_000), "old end"]
        new = ["new start", *(["same"] * 1_000), "new end"]

        with mock.patch.object(diff_model, "SequenceMatcher", wraps=diff_model.SequenceMatcher) as matcher:
            rows = build_review_lines(old, new)

        self.assertTrue(matcher.call_args.kwargs["autojunk"])
        self.assertEqual([row.kind for row in rows].count("context"), 1_000)
        self.assertEqual([row.kind for row in rows].count("deletion"), 2)
        self.assertEqual([row.kind for row in rows].count("addition"), 2)

    def test_large_repetitive_middle_resynchronizes_after_insert(self):
        old = ["old start", *(["same"] * 1_000), "old end"]
        new = ["new start", "inserted", *(["same"] * 1_000), "new end"]

        rows = build_review_lines(old, new)

        self.assertEqual([row.kind for row in rows].count("context"), 1_000)
        self.assertIn("inserted", [row.text for row in rows if row.kind == "addition"])

    def test_very_large_rewrite_uses_linear_coarse_diff(self):
        with (
            mock.patch.object(diff_model, "COARSE_DIFF_LINE_THRESHOLD", 4),
            mock.patch.object(diff_model, "SequenceMatcher", side_effect=AssertionError("exact matcher should be skipped")),
        ):
            rows = build_review_lines(["a", "b", "c"], ["x", "y", "z"])

        self.assertEqual([row.kind for row in rows], ["deletion"] * 3 + ["addition"] * 3)


if __name__ == "__main__":
    unittest.main()
