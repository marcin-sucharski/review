import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)


class CliIntegrationTests(unittest.TestCase):
    def make_repo(self) -> tempfile.TemporaryDirectory:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        git(root, "init", "-b", "main")
        git(root, "config", "user.email", "review@example.com")
        git(root, "config", "user.name", "Review Tests")
        (root / "app.js").write_text("const value = 1;\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-m", "initial")
        return temp

    def run_review(self, root: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        src = Path(__file__).resolve().parents[2] / "src"
        env["PYTHONPATH"] = str(src)
        return subprocess.run(
            [sys.executable, "-m", "review", *args],
            cwd=root,
            env=env,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_no_tui_stdout_collects_changes(self):
        with self.make_repo() as temp:
            root = Path(temp)
            (root / "app.js").write_text("const value = 2;\n", encoding="utf-8")
            result = self.run_review(root, "--source", "uncommitted", "--no-tui", "--stdout")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "No review comments.\n")

    def test_no_tui_branch_review_collects_uncommitted_changes(self):
        with self.make_repo() as temp:
            root = Path(temp)
            git(root, "checkout", "-b", "feature")
            (root / "app.js").write_text("const value = 2;\n", encoding="utf-8")
            result = self.run_review(root, "--source", "branch", "--target", "main", "--no-tui", "--stdout")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "No review comments.\n")
            self.assertNotIn("no changes found", result.stdout)

    def test_no_changes_exits_cleanly(self):
        with self.make_repo() as temp:
            root = Path(temp)
            result = self.run_review(root, "--source", "uncommitted", "--no-tui", "--stdout")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no uncommitted changes found", result.stdout)

    def test_prompt_eof_exits_cleanly_without_traceback(self):
        with self.make_repo() as temp:
            root = Path(temp)
            result = self.run_review(root, "--no-tui", "--stdout", input_text="")
            self.assertEqual(result.returncode, 130)
            self.assertIn("review cancelled", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_git_command_error_includes_command(self):
        with self.make_repo() as temp:
            root = Path(temp)
            result = self.run_review(root, "--source", "branch", "--target", "missing", "--no-tui", "--stdout")
            self.assertEqual(result.returncode, 1)
            self.assertIn("git merge-base HEAD missing failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
