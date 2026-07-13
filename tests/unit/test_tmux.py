import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from review.errors import TmuxSendError
from review.tmux import parse_panes, send_text


class TmuxTests(unittest.TestCase):
    def test_parse_panes_marks_current(self):
        output = "%1\tmain\t0\t0\ttitle\tbash\n%2\tmain\t0\t1\tagent\tcodex\n"
        panes = parse_panes(output, "%2")
        self.assertEqual(len(panes), 2)
        self.assertFalse(panes[0].current)
        self.assertTrue(panes[1].current)
        self.assertEqual(panes[1].display(), '%2  main:0.1  codex  title="agent" [current]')

    def test_send_text_uses_buffer_and_enter(self):
        calls = []

        def runner(command, input_text=None):
            calls.append((command, input_text))
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            mock.patch("review.tmux.tmux_available", return_value=True),
            mock.patch("review.tmux.uuid.uuid4", return_value=SimpleNamespace(hex="abc123")),
        ):
            send_text("%1", "hello\nworld", runner)

        self.assertEqual(calls[0], (["tmux", "load-buffer", "-b", "review-abc123", "-"], "hello\nworld"))
        self.assertEqual(calls[1], (["tmux", "paste-buffer", "-d", "-b", "review-abc123", "-t", "%1"], None))
        self.assertEqual(calls[2], (["tmux", "send-keys", "-t", "%1", "Enter"], None))

    def test_send_text_deletes_named_buffer_when_paste_fails(self):
        calls = []

        def runner(command, input_text=None):
            calls.append((command, input_text))
            returncode = 1 if command[1] == "paste-buffer" else 0
            return subprocess.CompletedProcess(command, returncode, "", "paste failed" if returncode else "")

        with (
            mock.patch("review.tmux.tmux_available", return_value=True),
            mock.patch("review.tmux.uuid.uuid4", return_value=SimpleNamespace(hex="failed")),
            self.assertRaisesRegex(TmuxSendError, "paste failed"),
        ):
            send_text("%1", "hello", runner)

        self.assertEqual(calls[-1], (["tmux", "delete-buffer", "-b", "review-failed"], None))


if __name__ == "__main__":
    unittest.main()
