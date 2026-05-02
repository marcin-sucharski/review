import unittest

from review.diff_model import create_review_file
from review.tui.file_tree import build_file_tree, file_tree_row_index


class FileTreeTests(unittest.TestCase):
    def test_collapses_single_directory_chains_and_keeps_files_selectable(self):
        files = [
            create_review_file("src/review/cli.py", "modified", ["a"], ["b"]),
            create_review_file("src/review/tui/app.py", "modified", ["a"], ["b"]),
            create_review_file("tests/unit/test_cli.py", "modified", ["a"], ["b"]),
            create_review_file("pyproject.toml", "modified", ["a"], ["b"]),
        ]

        rows = build_file_tree(files)
        labels = [(row.kind, row.label, row.depth, row.file_index) for row in rows]

        self.assertIn(("directory", "src/review/", 0, None), labels)
        self.assertIn(("directory", "tui/", 1, None), labels)
        self.assertIn(("file", "cli.py", 1, 0), labels)
        self.assertIn(("file", "app.py", 2, 1), labels)
        self.assertIn(("directory", "tests/unit/", 0, None), labels)
        self.assertIn(("file", "test_cli.py", 1, 2), labels)
        self.assertIn(("file", "pyproject.toml", 0, 3), labels)
        self.assertEqual(file_tree_row_index(rows, 1), labels.index(("file", "app.py", 2, 1)))

    def test_renamed_file_uses_compact_leaf_label(self):
        files = [
            create_review_file(
                "src/new_name.py",
                "renamed",
                ["a"],
                ["b"],
                old_path="src/old_name.py",
            )
        ]

        rows = build_file_tree(files)

        self.assertEqual(rows[-1].kind, "file")
        self.assertEqual(rows[-1].label, "old_name.py -> new_name.py")


if __name__ == "__main__":
    unittest.main()
