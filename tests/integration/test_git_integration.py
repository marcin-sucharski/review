import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from review.git import collect_branch_comparison, collect_uncommitted, default_branch_candidates, repository_root


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)


def git_with_dates(root: Path, date: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
    }
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True, env=env)


def write(root: Path, path: str, text: str) -> None:
    full = root / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(text, encoding="utf-8")


class GitIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.email", "review@example.com")
        git(self.root, "config", "user.name", "Review Tests")
        write(self.root, "src/Main.java", "class Main {\n  int value() { return 1; }\n}\n")
        write(self.root, "web/app.ts", "export const value = 1;\n")
        write(self.root, "config/settings.yaml", "enabled: true\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "initial")

    def tearDown(self):
        self.temp.cleanup()

    def test_repository_root(self):
        nested = self.root / "src"
        self.assertEqual(repository_root(nested), self.root)

    def test_collect_uncommitted_detects_modified_added_deleted_untracked(self):
        write(self.root, "web/app.ts", "export const value = 2;\n")
        write(self.root, "new.json", "{\"ok\": true}\n")
        os.remove(self.root / "config/settings.yaml")
        write(self.root, "notes.properties", "name=value\n")
        git(self.root, "add", "new.json")

        source, files = collect_uncommitted(self.root)
        paths = {file.path: file for file in files}
        self.assertEqual(source.kind, "uncommitted")
        self.assertIn("web/app.ts", paths)
        self.assertIn("new.json", paths)
        self.assertIn("config/settings.yaml", paths)
        self.assertIn("notes.properties", paths)
        self.assertEqual(paths["new.json"].status, "added")
        self.assertEqual(paths["config/settings.yaml"].status, "deleted")
        self.assertEqual(paths["notes.properties"].language, "properties")

    def test_collect_uncommitted_detects_rename(self):
        git(self.root, "mv", "web/app.ts", "web/app-renamed.ts")
        write(self.root, "web/app-renamed.ts", "export const value = 1;\nexport const extra = true;\n")
        _, files = collect_uncommitted(self.root)
        renamed = [file for file in files if file.path == "web/app-renamed.ts"][0]
        self.assertEqual(renamed.status, "renamed")
        self.assertEqual(renamed.old_path, "web/app.ts")

    def test_collect_uncommitted_rename_only_is_metadata_only(self):
        git(self.root, "mv", "web/app.ts", "web/app-renamed.ts")
        _, files = collect_uncommitted(self.root)
        renamed = [file for file in files if file.path == "web/app-renamed.ts"][0]
        self.assertEqual(renamed.status, "renamed")
        self.assertEqual(renamed.lines, [])
        self.assertIn("Renamed without content changes", renamed.metadata)

    def test_collect_branch_comparison_uses_merge_base(self):
        git(self.root, "checkout", "-b", "feature")
        write(self.root, "src/Main.java", "class Main {\n  int value() { return 2; }\n}\n")
        write(self.root, "db/schema.sql", "create table users(id int);\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "feature changes")

        source, files = collect_branch_comparison(self.root, "main")
        paths = {file.path for file in files}
        self.assertEqual(source.kind, "branch")
        self.assertEqual(source.target_branch, "main")
        self.assertIn("src/Main.java", paths)
        self.assertIn("db/schema.sql", paths)

    def test_default_branch_candidates_include_main(self):
        branches = default_branch_candidates(self.root)
        self.assertIn("main", branches)

    def test_default_branch_candidates_prioritize_main_master_then_recent_branches(self):
        git(self.root, "checkout", "-b", "old-topic")
        write(self.root, "old.txt", "old\n")
        git(self.root, "add", "old.txt")
        git_with_dates(self.root, "2001-01-01T00:00:00+0000", "commit", "-m", "old topic")

        git(self.root, "checkout", "main")
        git(self.root, "checkout", "-b", "recent-topic")
        write(self.root, "recent.txt", "recent\n")
        git(self.root, "add", "recent.txt")
        git_with_dates(self.root, "2020-01-01T00:00:00+0000", "commit", "-m", "recent topic")

        git(self.root, "checkout", "main")
        git(self.root, "branch", "master")

        branches = default_branch_candidates(self.root)

        self.assertEqual(branches[:2], ["main", "master"])
        self.assertLess(branches.index("recent-topic"), branches.index("old-topic"))

    def test_collect_uncommitted_marks_binary_file(self):
        (self.root / "image.bin").write_bytes(b"\x00\x01\x02\x03")
        git(self.root, "add", "image.bin")
        _, files = collect_uncommitted(self.root)
        binary = [file for file in files if file.path == "image.bin"][0]
        self.assertTrue(binary.binary)
        self.assertEqual(binary.status, "binary")

    def test_collect_uncommitted_includes_staged_change_hidden_by_worktree_revert(self):
        write(self.root, "web/app.ts", "export const value = 2;\n")
        git(self.root, "add", "web/app.ts")
        write(self.root, "web/app.ts", "export const value = 1;\n")
        _, files = collect_uncommitted(self.root)
        app = [file for file in files if file.path == "web/app.ts"][0]
        self.assertTrue(any(line.kind == "addition" and "2" in line.text for line in app.lines))

    def test_collect_uncommitted_merges_hidden_staged_and_visible_worktree_changes(self):
        write(self.root, "web/app.ts", "export const value = 2;\nexport const other = 1;\n")
        git(self.root, "add", "web/app.ts")
        write(self.root, "web/app.ts", "export const value = 1;\nexport const other = 2;\n")
        _, files = collect_uncommitted(self.root)
        app = [file for file in files if file.path == "web/app.ts"][0]
        additions = [line.text for line in app.lines if line.kind == "addition"]
        self.assertIn("export const value = 2;", additions)
        self.assertIn("export const other = 2;", additions)
        self.assertIn("Includes staged and unstaged changes", app.metadata)
        self.assertFalse([line for line in app.lines if line.kind == "metadata"])

    def test_collect_uncommitted_mixed_file_uses_single_unified_diff_body(self):
        write(self.root, "web/app.ts", "export const value = 1;\nexport const other = 1;\n")
        git(self.root, "add", "web/app.ts")
        git(self.root, "commit", "-m", "make app two lines")
        write(self.root, "web/app.ts", "export const value = 2;\nexport const other = 1;\n")
        git(self.root, "add", "web/app.ts")
        write(self.root, "web/app.ts", "export const value = 2;\nexport const other = 2;\n")

        _, files = collect_uncommitted(self.root)

        app = [file for file in files if file.path == "web/app.ts"][0]
        additions = [line.text for line in app.lines if line.kind == "addition"]
        self.assertEqual(additions, ["export const value = 2;", "export const other = 2;"])
        self.assertEqual([line.text for line in app.lines if line.kind == "context"], [])
        self.assertFalse([line for line in app.lines if line.kind == "metadata"])

    def test_collect_uncommitted_keeps_same_text_additions_at_different_lines(self):
        write(self.root, "same-text.txt", "foo\nx\n")
        git(self.root, "add", "same-text.txt")
        git(self.root, "commit", "-m", "add same-text fixture")
        write(self.root, "same-text.txt", "bar\nx\n")
        git(self.root, "add", "same-text.txt")
        write(self.root, "same-text.txt", "foo\nbar\n")

        _, files = collect_uncommitted(self.root)

        file = [file for file in files if file.path == "same-text.txt"][0]
        bar_additions = [line for line in file.lines if line.kind == "addition" and line.text == "bar"]
        self.assertEqual([(line.old_line, line.new_line) for line in bar_additions], [(None, 1), (None, 2)])

    def test_collect_uncommitted_staged_rename_with_unstaged_edit_is_one_renamed_file(self):
        write(self.root, "rename-source.txt", "".join(f"old line {i}\n" for i in range(80)))
        git(self.root, "add", "rename-source.txt")
        git(self.root, "commit", "-m", "add rename source")
        git(self.root, "mv", "rename-source.txt", "rename-target.txt")
        git(self.root, "add", "-A")
        write(self.root, "rename-target.txt", "".join(f"new line {i}\n" for i in range(80)))
        _, files = collect_uncommitted(self.root)
        paths = [file.path for file in files]
        self.assertIn("rename-target.txt", paths)
        self.assertNotIn("rename-source.txt", paths)
        renamed = [file for file in files if file.path == "rename-target.txt"][0]
        self.assertEqual(renamed.status, "renamed")
        self.assertEqual(renamed.old_path, "rename-source.txt")

    def test_collect_uncommitted_uses_index_as_base_for_unstaged_section(self):
        write(self.root, "rename-source.txt", "alpha\nbase\nomega\n")
        git(self.root, "add", "rename-source.txt")
        git(self.root, "commit", "-m", "add staged rename source")
        git(self.root, "mv", "rename-source.txt", "rename-target.txt")
        write(self.root, "rename-target.txt", "alpha\nstaged\nomega\n")
        git(self.root, "add", "-A")
        write(self.root, "rename-target.txt", "alpha\nunstaged\nomega\n")

        _, files = collect_uncommitted(self.root)

        renamed = [file for file in files if file.path == "rename-target.txt"][0]
        self.assertEqual(renamed.status, "renamed")
        self.assertEqual(renamed.old_path, "rename-source.txt")
        self.assertIn("Includes staged and unstaged changes", renamed.metadata)
        self.assertFalse([line for line in renamed.lines if line.kind == "metadata"])

        deletions = [line.text for line in renamed.lines if line.kind == "deletion"]
        additions = [line.text for line in renamed.lines if line.kind == "addition"]
        self.assertIn("staged", deletions)
        self.assertIn("base", deletions)
        self.assertIn("staged", additions)
        self.assertIn("unstaged", additions)

    def test_collect_uncommitted_keeps_recreated_file_after_staged_delete(self):
        write(self.root, "recreated.txt", "before\n")
        git(self.root, "add", "recreated.txt")
        git(self.root, "commit", "-m", "add recreated source")
        git(self.root, "rm", "recreated.txt")
        write(self.root, "recreated.txt", "after\n")

        _, files = collect_uncommitted(self.root)

        recreated = [file for file in files if file.path == "recreated.txt"][0]
        self.assertEqual(recreated.status, "modified")
        self.assertIn("Includes staged and unstaged changes", recreated.metadata)
        sections = [line.text for line in recreated.lines if line.kind == "metadata"]
        self.assertEqual(sections, [])
        self.assertTrue(any(line.kind == "deletion" and line.text == "before" for line in recreated.lines))
        self.assertTrue(any(line.kind == "addition" and line.text == "after" for line in recreated.lines))

    def test_collect_uncommitted_keeps_staged_add_status_after_unstaged_edit(self):
        write(self.root, "new-file.txt", "staged\n")
        git(self.root, "add", "new-file.txt")
        write(self.root, "new-file.txt", "unstaged\n")

        _, files = collect_uncommitted(self.root)

        added = [file for file in files if file.path == "new-file.txt"][0]
        self.assertEqual(added.status, "added")
        self.assertEqual(added.status_marker(), "A")
        self.assertIn("Includes staged and unstaged changes", added.metadata)
        self.assertFalse([line for line in added.lines if line.kind == "metadata"])
        self.assertTrue(any(line.kind == "addition" and line.text == "staged" for line in added.lines))
        self.assertTrue(any(line.kind == "deletion" and line.text == "staged" for line in added.lines))
        self.assertTrue(any(line.kind == "addition" and line.text == "unstaged" for line in added.lines))

    def test_collect_uncommitted_mode_only_change_is_metadata_only(self):
        if git(self.root, "config", "--get", "core.filemode").stdout.strip() == "false":
            self.skipTest("repository does not track file mode changes")
        os.chmod(self.root / "web/app.ts", 0o755)
        _, files = collect_uncommitted(self.root)
        app = [file for file in files if file.path == "web/app.ts"][0]
        self.assertEqual(app.status, "mode")
        self.assertEqual(app.lines, [])
        self.assertIn("Mode changed", app.metadata)


if __name__ == "__main__":
    unittest.main()
