import subprocess
import unittest
from unittest import mock

from review.tmux import TmuxPane, parse_panes, send_text


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

        with mock.patch("review.tmux.tmux_available", return_value=True):
            send_text("%1", "hello\nworld", runner)

        self.assertEqual(calls[0], (["tmux", "load-buffer", "-"], "hello\nworld"))
        self.assertEqual(calls[1], (["tmux", "paste-buffer", "-t", "%1"], None))
        self.assertEqual(calls[2], (["tmux", "send-keys", "-t", "%1", "Enter"], None))


if __name__ == "__main__":
    unittest.main()
