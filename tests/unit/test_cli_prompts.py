import io
import os
import unittest
from pathlib import Path
from unittest import mock

from review import cli
from review.diff_model import ReviewSource, create_review_file
from review.tmux import TmuxPane
from review.tui.menu import MenuOption, _decode_key, _read_key, _render_menu_lines


class CliPromptTests(unittest.TestCase):
    def test_prompt_source_uses_selectable_menu_options(self):
        with mock.patch.object(cli, "select_option", return_value="branch") as select_option:
            self.assertEqual(cli.prompt_source(), "branch")

        title, options = select_option.call_args.args
        self.assertEqual(title, "Review source")
        self.assertEqual([option.value for option in options], ["uncommitted", "branch"])
        self.assertIn("PR-style", options[1].label)
        self.assertFalse(select_option.call_args.kwargs.get("cancel_requires_double", False))

    def test_prompt_branch_uses_selectable_branch_menu(self):
        with (
            mock.patch.object(cli, "default_branch_candidates", return_value=["main", "develop"]),
            mock.patch.object(cli, "select_option", return_value="develop") as select_option,
        ):
            self.assertEqual(cli.prompt_branch(Path("/repo")), "develop")

        title, options = select_option.call_args.args
        self.assertEqual(title, "Target branch")
        self.assertEqual([option.value for option in options], ["main", "develop"])
        self.assertTrue(select_option.call_args.kwargs["cancel_requires_double"])

    def test_deliver_review_uses_selectable_tmux_menu(self):
        pane = TmuxPane("%1", "s", "0", "1", "agent", "bash")
        with (
            mock.patch.object(cli, "list_panes", return_value=[pane]),
            mock.patch.object(cli, "select_option", return_value="%1") as select_option,
            mock.patch.object(cli, "send_text") as send_text,
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            self.assertEqual(cli.deliver_review("review text\n"), 0)

        title, options = select_option.call_args.args
        self.assertEqual(title, "Delivery target")
        self.assertEqual([option.value for option in options], ["stdout", "%1"])
        self.assertTrue(select_option.call_args.kwargs["cancel_requires_double"])
        send_text.assert_called_once_with("%1", "review text\n")
        self.assertIn("Sent review to tmux pane %1.", stdout.getvalue())

    def test_deliver_review_stdout_option_prints_message(self):
        pane = TmuxPane("%1", "s", "0", "1", "agent", "bash")
        with (
            mock.patch.object(cli, "list_panes", return_value=[pane]),
            mock.patch.object(cli, "select_option", return_value="stdout"),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            self.assertEqual(cli.deliver_review("review text\n"), 0)

        self.assertEqual(stdout.getvalue(), "review text\n")

    def test_menu_rendering_is_inline_and_light_theme_safe(self):
        lines = _render_menu_lines(
            "Review source",
            [
                MenuOption("Review uncommitted changes", "uncommitted"),
                MenuOption("Review PR-style changes", "branch"),
            ],
            1,
            use_color=True,
        )

        self.assertEqual(len(lines), 3)
        self.assertIn("Review source", lines[0])
        self.assertIn("> Review PR-style changes", lines[2])
        self.assertNotIn("\x1b[7m", "\n".join(lines))

    def test_menu_decodes_arrow_escape_sequences_without_cancelling(self):
        self.assertEqual(_decode_key(b"\x1b[B"), "down")
        self.assertEqual(_decode_key(b"\x1bOB"), "down")
        self.assertEqual(_decode_key(b"\x1b[A"), "up")
        self.assertEqual(_decode_key(b"\x1b"), "escape")
        self.assertEqual(_decode_key(b"\x03"), "ctrl_c")

    def test_main_archives_non_empty_review_before_stdout_delivery(self):
        file = create_review_file("app.py", "modified", ["old"], ["new"])

        class TtyStringIO(io.StringIO):
            def isatty(self):
                return True

        class FakeReviewApp:
            def __init__(self, state):
                self.state = state

            def run(self):
                self.state.add_comment("Needs work.")
                return self.state

        stdout = TtyStringIO()
        with (
            mock.patch.object(cli, "repository_root", return_value=Path("/repo")),
            mock.patch.object(cli, "collect_uncommitted", return_value=(ReviewSource("uncommitted"), [file])),
            mock.patch.object(cli.sys.stdin, "isatty", return_value=True),
            mock.patch.object(cli.sys, "stdout", stdout),
            mock.patch.object(cli, "ReviewApp", FakeReviewApp),
            mock.patch.object(cli, "archive_review") as archive_review,
        ):
            self.assertEqual(cli.main(["--source", "uncommitted", "--stdout"]), 0)

        archive_review.assert_called_once()
        self.assertIn("Needs work.", stdout.getvalue())

    def test_menu_read_key_waits_for_complete_application_arrow_sequence(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"\x1bOB")
            os.close(write_fd)
            write_fd = -1
            self.assertEqual(_read_key(read_fd), "down")
        finally:
            os.close(read_fd)
            if write_fd != -1:
                os.close(write_fd)


if __name__ == "__main__":
    unittest.main()
