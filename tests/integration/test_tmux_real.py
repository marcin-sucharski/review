import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from review.tmux import send_text


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
class RealTmuxIntegrationTests(unittest.TestCase):
    def test_send_text_to_real_tmux_pane(self):
        session = f"review-test-{time.time_ns()}"
        with tempfile.TemporaryDirectory() as temp:
            capture = Path(temp) / "capture.txt"
            created = subprocess.run(
                ["tmux", "new-session", "-d", "-s", session, f"cat > {capture}"],
                text=True,
                capture_output=True,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest(created.stderr.strip() or "could not create tmux session")
            try:
                pane = subprocess.run(
                    ["tmux", "display-message", "-p", "-t", f"{session}:0.0", "#{pane_id}"],
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout.strip()
                send_text(pane, "hello from review")
                deadline = time.time() + 2
                while time.time() < deadline:
                    if capture.exists() and "hello from review\n" in capture.read_text(encoding="utf-8"):
                        return
                    time.sleep(0.05)
                self.fail("tmux target pane did not receive review text")
            finally:
                subprocess.run(["tmux", "kill-session", "-t", session], text=True, capture_output=True, check=False)


if __name__ == "__main__":
    unittest.main()
