import json
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

    def run_review(
        self,
        root: Path,
        *args: str,
        input_text: str | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        src = Path(__file__).resolve().parents[2] / "src"
        env["PYTHONPATH"] = str(src)
        if env_overrides:
            env.update(env_overrides)
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

    def test_history_ls_and_display_work_outside_git_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "not-a-repo"
            root.mkdir()
            xdg_data = Path(temp) / "xdg"
            archive_dir = xdg_data / "review" / "reviews"
            archive_dir.mkdir(parents=True)
            old = archive_dir / "20260504T120000000000Z-old.json"
            new = archive_dir / "20260504T130000000000Z-new.json"
            old.write_text(
                json.dumps({"path": "/old/repo", "branch": "main", "review_message": "older review\n"}),
                encoding="utf-8",
            )
            new.write_text(
                json.dumps({"path": "/new/repo", "branch": "feature", "review_message": "newer review\n"}),
                encoding="utf-8",
            )
            os.utime(old, (100, 100))
            os.utime(new, (200, 200))

            env = {"XDG_DATA_HOME": str(xdg_data)}
            listed = self.run_review(root, "ls", env_overrides=env)
            displayed = self.run_review(root, "display", input_text="2\n", env_overrides=env)

            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(len(listed.stdout.splitlines()), 2)
            self.assertIn("1. 20260504T130000000000Z  feature  /new/repo", listed.stdout)
            self.assertIn("2. 20260504T120000000000Z  main  /old/repo", listed.stdout)
            self.assertEqual(displayed.returncode, 0, displayed.stderr)
            self.assertEqual(displayed.stdout, "older review\n")
            self.assertIn("Saved reviews:", displayed.stderr)


if __name__ == "__main__":
    unittest.main()
