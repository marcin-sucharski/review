import unittest
from pathlib import Path
from unittest import mock

from review.errors import GitCommandError
from review.git import parse_name_status_z, repository_root, run_git


class GitParseTests(unittest.TestCase):
    def test_parse_modified_path_with_spaces(self):
        entries = parse_name_status_z(b"M\x00path with spaces/file.txt\x00")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, "M")
        self.assertEqual(entries[0].path, "path with spaces/file.txt")

    def test_parse_rename(self):
        entries = parse_name_status_z(b"R087\x00old name.js\x00new name.js\x00")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, "R")
        self.assertEqual(entries[0].old_path, "old name.js")
        self.assertEqual(entries[0].path, "new name.js")

    def test_parse_copy(self):
        entries = parse_name_status_z(b"C100\x00source.json\x00copy.json\x00")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, "C")
        self.assertEqual(entries[0].old_path, "source.json")
        self.assertEqual(entries[0].path, "copy.json")

    def test_run_git_missing_executable_is_user_facing_error(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(GitCommandError) as ctx:
                run_git(Path("/tmp"), ["status"])
        self.assertIn("git executable not found", str(ctx.exception))

    def test_repository_root_missing_git_is_user_facing_error(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(GitCommandError) as ctx:
                repository_root(Path("/tmp"))
        self.assertIn("git executable not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
