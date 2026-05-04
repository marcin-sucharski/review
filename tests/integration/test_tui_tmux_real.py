import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
class RealTmuxTuiIntegrationTests(unittest.TestCase):
    def test_meta_left_right_moves_comment_cursor_by_word_in_real_tmux_tui(self):
        session = f"review-tui-word-{time.time_ns()}"
        with tempfile.TemporaryDirectory() as repo_temp, tempfile.TemporaryDirectory() as data_temp:
            root = Path(repo_temp)
            self._make_changed_repo(root)
            command = self._review_command(root, Path(data_temp))
            created = subprocess.run(
                ["tmux", "new-session", "-d", "-s", session, "-x", "100", "-y", "32", command],
                text=True,
                capture_output=True,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest(created.stderr.strip() or "could not create tmux session")
            try:
                target = f"{session}:0.0"
                self._wait_for_capture(target, "app.py")
                self._send(target, "Enter")
                self._send_literal(target, "one two three")
                before = self._cursor_position(target)

                self._send(target, "M-Left")
                after_left = self._cursor_position(target)
                self.assertLess(after_left[0], before[0], (before, after_left, self._capture(target)))

                self._send_literal(target, "X")
                self._send(target, "M-Right")
                after_right = self._cursor_position(target)
                self.assertGreater(after_right[0], after_left[0], (after_left, after_right, self._capture(target)))

                self._send_literal(target, "Y")
                self._send(target, "Enter")
                self._send(target, ":", "q", "Enter")
                self._wait_for_capture(target, "REVIEW_EXIT:0")
                output = self._capture(target, history=120)

                self.assertIn("one two XthreeY", output)
                self.assertNotIn("one two threeXY", output)
                self.assertNotIn("one two XYthree", output)
            finally:
                subprocess.run(["tmux", "kill-session", "-t", session], text=True, capture_output=True, check=False)

    def _make_changed_repo(self, root: Path) -> None:
        git(root, "init", "-b", "main")
        git(root, "config", "user.email", "review@example.com")
        git(root, "config", "user.name", "Review Tests")
        (root / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-m", "initial")
        (root / "app.py").write_text("def alpha():\n    return 2\n", encoding="utf-8")

    def _review_command(self, root: Path, data_root: Path) -> str:
        src = Path(__file__).resolve().parents[2] / "src"
        script = (
            f"cd {shlex.quote(str(root))} && "
            f"XDG_DATA_HOME={shlex.quote(str(data_root))} "
            f"PYTHONPATH={shlex.quote(str(src))}:$PYTHONPATH "
            f"{shlex.quote(sys.executable)} -m review --source uncommitted --stdout; "
            "rc=$?; printf '\\nREVIEW_EXIT:%s\\n' \"$rc\"; sleep 5"
        )
        return f"bash -lc {shlex.quote(script)}"

    def _send(self, target: str, *keys: str) -> None:
        subprocess.run(["tmux", "send-keys", "-t", target, *keys], text=True, capture_output=True, check=True)
        time.sleep(0.1)

    def _send_literal(self, target: str, text: str) -> None:
        subprocess.run(["tmux", "send-keys", "-l", "-t", target, text], text=True, capture_output=True, check=True)
        time.sleep(0.1)

    def _cursor_position(self, target: str) -> tuple[int, int]:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", target, "#{cursor_x}\t#{cursor_y}"],
            text=True,
            capture_output=True,
            check=True,
        )
        x, y = result.stdout.strip().split("\t")
        return int(x), int(y)

    def _wait_for_capture(self, target: str, text: str, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if text in self._capture(target):
                return
            time.sleep(0.1)
        self.fail(f"timed out waiting for {text!r} in tmux pane:\n{self._capture(target, history=80)}")

    def _capture(self, target: str, *, history: int = 40) -> str:
        return subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", target, "-S", f"-{history}"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout


if __name__ == "__main__":
    unittest.main()
