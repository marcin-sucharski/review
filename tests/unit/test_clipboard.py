import io
import subprocess
import unittest
from unittest import mock

from review import clipboard


class ClipboardTests(unittest.TestCase):
    def test_tmux_clipboard_uses_load_buffer_w(self):
        calls = []

        def runner(command, input_text=None):
            calls.append((command, input_text))
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            mock.patch.object(clipboard, "inside_tmux", return_value=True),
            mock.patch.object(clipboard, "tmux_available", return_value=True),
        ):
            copied = clipboard.copy_text_to_clipboard("hello", runner=runner, stream=io.StringIO())

        self.assertTrue(copied)
        self.assertEqual(calls, [(["tmux", "load-buffer", "-w", "-"], "hello")])

    def test_osc52_clipboard_sequence_uses_base64_payload(self):
        stream = io.StringIO()

        copied = clipboard.write_osc52_clipboard("hello", stream=stream)

        self.assertTrue(copied)
        self.assertEqual(stream.getvalue(), "\x1b]52;c;aGVsbG8=\x07")

    def test_osc52_clipboard_sequence_wraps_for_tmux_passthrough(self):
        stream = io.StringIO()

        copied = clipboard.write_osc52_clipboard("hello", inside_tmux=True, stream=stream)

        self.assertTrue(copied)
        self.assertEqual(stream.getvalue(), "\x1bPtmux;\x1b\x1b]52;c;aGVsbG8=\x07\x1b\\")


if __name__ == "__main__":
    unittest.main()
