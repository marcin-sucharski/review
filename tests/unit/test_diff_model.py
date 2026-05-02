import unittest

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


if __name__ == "__main__":
    unittest.main()
