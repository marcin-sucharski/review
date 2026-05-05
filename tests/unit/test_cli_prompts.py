import io
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from review import cli
from review.diff_model import ReviewSource, create_review_file
from review.tmux import TmuxPane
from review.tui.menu import (
    MenuOption,
    _clear_rendered_menu,
    _decode_key,
    _read_key,
    _rendered_row_count,
    _render_branch_menu_lines,
    _render_menu_lines,
    _select_branch_inline,
    _select_option_inline,
    _select_option_text,
)


class CliPromptTests(unittest.TestCase):
    def test_parser_defaults_to_markdown_output_format_and_accepts_short_options(self):
        parser = cli.build_parser()

        self.assertEqual(parser.parse_args([]).output_format, "md")
        self.assertEqual(parser.parse_args(["-o", "md"]).output_format, "md")
        self.assertEqual(parser.parse_args(["--output-format", "xml"]).output_format, "xml")

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
            mock.patch.object(cli, "current_branch", return_value="feature"),
            mock.patch.object(cli, "default_branch_candidates", return_value=["main", "feature", "develop"]),
            mock.patch.object(cli, "select_branch_target", return_value="develop") as select_branch_target,
        ):
            self.assertEqual(cli.prompt_branch(Path("/repo")), "develop")

        title, current, branches = select_branch_target.call_args.args
        self.assertEqual(title, "Target branch")
        self.assertEqual(current, "feature")
        self.assertEqual(branches, ["main", "develop"])
        self.assertTrue(select_branch_target.call_args.kwargs["cancel_requires_double"])

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
        self.assertEqual([option.value for option in options], ["file", "stdout", "%1"])
        self.assertEqual(options[0].label, "Save to file")
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

    def test_deliver_review_offers_file_and_terminal_when_tmux_is_unavailable(self):
        with (
            mock.patch.object(cli, "list_panes", side_effect=cli.TmuxUnavailable("tmux is not installed")),
            mock.patch.object(cli, "select_option", return_value="stdout") as select_option,
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            self.assertEqual(cli.deliver_review("review text\n"), 0)

        _title, options = select_option.call_args.args
        self.assertEqual([option.value for option in options], ["file", "stdout"])
        self.assertEqual(stdout.getvalue(), "review text\n")

    def test_deliver_review_save_to_file_writes_markdown_in_current_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with (
                mock.patch.object(cli, "list_panes", return_value=[]),
                mock.patch.object(cli, "select_option", return_value="file"),
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                result = cli.deliver_review(
                    "<review_feedback />\n",
                    markdown_message="# Review comments\n",
                    output_dir=output_dir,
                    now=datetime(2026, 5, 4, 9, 7),
                )

            self.assertEqual(result, 0)
            path = output_dir / "review-20260504-0907.md"
            self.assertEqual(path.read_text(encoding="utf-8"), "# Review comments\n")
            self.assertIn(f"Saved review to {path}.", stdout.getvalue())

    def test_save_review_file_avoids_overwriting_same_minute_reviews(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            now = datetime(2026, 5, 4, 9, 7)

            first = cli.save_review_file("first\n", output_dir, now)
            second = cli.save_review_file("second\n", output_dir, now)

            self.assertEqual(first.name, "review-20260504-0907.md")
            self.assertEqual(second.name, "review-20260504-0907-2.md")
            self.assertEqual(first.read_text(encoding="utf-8"), "first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "second\n")

    def test_deliver_review_save_to_file_failure_prints_markdown_fallback(self):
        with (
            mock.patch.object(cli, "list_panes", return_value=[]),
            mock.patch.object(cli, "select_option", return_value="file"),
            mock.patch.object(cli, "save_review_file", side_effect=OSError("read-only directory")),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            mock.patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            result = cli.deliver_review("<review_feedback />\n", markdown_message="# Review comments\n")

        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "# Review comments\n")
        self.assertIn("could not save review file", stderr.getvalue())
        self.assertIn("read-only directory", stderr.getvalue())

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
        self.assertIn("q/Esc cancels", lines[0])
        self.assertIn("> Review PR-style changes", lines[2])
        self.assertNotIn("\x1b[7m", "\n".join(lines))

    def test_branch_menu_renders_current_to_target_pairs_with_five_visible_rows(self):
        lines = _render_branch_menu_lines(
            "Target branch",
            "feature/current",
            ["master", "main", "release/5", "release/4", "release/3", "release/2", "release/1"],
            0,
            "",
            use_color=True,
        )

        branch_lines = [line for line in lines if "feature/current ->" in line]
        self.assertEqual(len(branch_lines), 5)
        self.assertIn("feature/current -> master", branch_lines[0])
        self.assertIn("2 below", "\n".join(lines))
        self.assertIn("Search:", lines[-1])
        self.assertNotIn("\x1b[7m", "\n".join(lines))

    def test_branch_inline_menu_filters_by_typing_without_focusing_search_box(self):
        class FakeInput(io.StringIO):
            def fileno(self):
                return 42

        output = io.StringIO()
        with (
            mock.patch("review.tui.menu.termios.tcgetattr", return_value="settings"),
            mock.patch("review.tui.menu.termios.tcsetattr"),
            mock.patch("review.tui.menu.tty.setraw"),
            mock.patch("review.tui.menu._read_key", side_effect=["x", "enter"]),
        ):
            choice = _select_branch_inline(
                "Target branch",
                "feature",
                ["main", "release/x", "release/y"],
                FakeInput(),
                output,
                True,
            )

        self.assertEqual(choice, "release/x")
        self.assertIn("Search: x", output.getvalue())

    def test_inline_menu_clear_erases_rendered_lines(self):
        output = io.StringIO()

        _clear_rendered_menu(output, 3)

        self.assertEqual(output.getvalue(), "\x1b[3F\r\x1b[2K\x1b[1E\r\x1b[2K\x1b[1E\r\x1b[2K\x1b[2F")

    def test_inline_menu_counts_wrapped_terminal_rows_without_counting_ansi_codes(self):
        self.assertEqual(_rendered_row_count("\x1b[1;34m> abcdef\x1b[0m", 4), 2)
        self.assertEqual(_rendered_row_count("", 4), 1)

    def test_inline_option_menu_clears_wrapped_rows_when_selection_changes(self):
        class FakeInput(io.StringIO):
            def fileno(self):
                return 42

        output = io.StringIO()
        options = [
            MenuOption("review-20260504T120000Z very-long-branch-name /repo/with/a/very/long/path", "0"),
            MenuOption("short", "1"),
        ]
        first_render_rows = sum(_rendered_row_count(line, 20) for line in _render_menu_lines("Saved reviews", options, 0, use_color=True))
        with (
            mock.patch("review.tui.menu.shutil.get_terminal_size", return_value=os.terminal_size((20, 24))),
            mock.patch("review.tui.menu.termios.tcgetattr", return_value="settings"),
            mock.patch("review.tui.menu.termios.tcsetattr"),
            mock.patch("review.tui.menu.tty.setraw"),
            mock.patch("review.tui.menu._read_key", side_effect=["down", "enter"]),
        ):
            choice = _select_option_inline("Saved reviews", options, 0, FakeInput(), output, False)

        self.assertEqual(choice, "1")
        self.assertIn(f"\x1b[{first_render_rows}F", output.getvalue())

    def test_inline_branch_menu_clears_wrapped_rows_when_selection_changes(self):
        class FakeInput(io.StringIO):
            def fileno(self):
                return 42

        output = io.StringIO()
        branches = ["feature/with-a-very-long-name", "main"]
        first_render_rows = sum(
            _rendered_row_count(line, 16)
            for line in _render_branch_menu_lines("Target branch", "current/long-name", branches, 0, "", use_color=True)
        )
        with (
            mock.patch("review.tui.menu.shutil.get_terminal_size", return_value=os.terminal_size((16, 24))),
            mock.patch("review.tui.menu.termios.tcgetattr", return_value="settings"),
            mock.patch("review.tui.menu.termios.tcsetattr"),
            mock.patch("review.tui.menu.tty.setraw"),
            mock.patch("review.tui.menu._read_key", side_effect=["down", "enter"]),
        ):
            choice = _select_branch_inline("Target branch", "current/long-name", branches, FakeInput(), output, False)

        self.assertEqual(choice, "main")
        self.assertIn(f"\x1b[{first_render_rows}F", output.getvalue())

    def test_inline_menu_clears_before_returning_selection(self):
        class FakeInput(io.StringIO):
            def fileno(self):
                return 42

        output = io.StringIO()
        with (
            mock.patch("review.tui.menu.termios.tcgetattr", return_value="settings"),
            mock.patch("review.tui.menu.termios.tcsetattr"),
            mock.patch("review.tui.menu.tty.setraw"),
            mock.patch("review.tui.menu._read_key", return_value="enter"),
        ):
            choice = _select_option_inline(
                "Review source",
                [MenuOption("Review uncommitted changes", "uncommitted")],
                0,
                FakeInput(),
                output,
                False,
            )

        self.assertEqual(choice, "uncommitted")
        self.assertIn("\x1b[2K", output.getvalue())
        self.assertTrue(output.getvalue().endswith("\x1b[1F"))

    def test_inline_option_menu_q_cancels_selection(self):
        class FakeInput(io.StringIO):
            def fileno(self):
                return 42

        output = io.StringIO()
        with (
            mock.patch("review.tui.menu.termios.tcgetattr", return_value="settings"),
            mock.patch("review.tui.menu.termios.tcsetattr"),
            mock.patch("review.tui.menu.tty.setraw"),
            mock.patch("review.tui.menu._read_key", return_value="q"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                _select_option_inline(
                    "Review source",
                    [MenuOption("Review uncommitted changes", "uncommitted")],
                    0,
                    FakeInput(),
                    output,
                    False,
                )

        self.assertIn("\x1b[2K", output.getvalue())

    def test_text_option_menu_q_cancels_selection(self):
        with self.assertRaises(KeyboardInterrupt):
            _select_option_text(
                "Saved reviews",
                [MenuOption("recent review", "0")],
                0,
                False,
                io.StringIO("q\n"),
                io.StringIO(),
            )

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
            mock.patch.object(cli, "reset_terminal_after_tui"),
            mock.patch.object(cli, "archive_review") as archive_review,
        ):
            self.assertEqual(cli.main(["--source", "uncommitted", "--stdout"]), 0)

        archive_review.assert_called_once()
        archived_message = archive_review.call_args.args[1]
        self.assertIn("# Review comments for /repo", stdout.getvalue())
        self.assertNotIn("<review_feedback>", stdout.getvalue())
        self.assertEqual(archived_message, stdout.getvalue())
        self.assertIn("Needs work.", stdout.getvalue())

    def test_main_uses_selected_xml_output_for_archive_and_stdout_delivery(self):
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
            mock.patch.object(cli, "reset_terminal_after_tui"),
            mock.patch.object(cli, "archive_review") as archive_review,
        ):
            self.assertEqual(cli.main(["--source", "uncommitted", "--stdout", "--output-format", "xml"]), 0)

        archived_message = archive_review.call_args.args[1]
        self.assertIn("<review_feedback>", stdout.getvalue())
        self.assertIn("<message>Needs work.</message>", stdout.getvalue())
        self.assertNotIn("# Review comments for /repo", stdout.getvalue())
        self.assertEqual(archived_message, stdout.getvalue())

    def test_main_provides_markdown_message_for_file_delivery_when_xml_is_selected(self):
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

        with (
            mock.patch.object(cli, "repository_root", return_value=Path("/repo")),
            mock.patch.object(cli, "collect_uncommitted", return_value=(ReviewSource("uncommitted"), [file])),
            mock.patch.object(cli.sys.stdin, "isatty", return_value=True),
            mock.patch.object(cli.sys, "stdout", TtyStringIO()),
            mock.patch.object(cli, "ReviewApp", FakeReviewApp),
            mock.patch.object(cli, "reset_terminal_after_tui"),
            mock.patch.object(cli, "archive_review"),
            mock.patch.object(cli, "deliver_review", return_value=0) as deliver_review,
        ):
            self.assertEqual(cli.main(["--source", "uncommitted", "--output-format", "xml"]), 0)

        delivered_message = deliver_review.call_args.args[0]
        delivered_markdown = deliver_review.call_args.kwargs["markdown_message"]
        self.assertIn("<review_feedback>", delivered_message)
        self.assertIn("# Review comments for /repo", delivered_markdown)
        self.assertNotIn("<review_feedback>", delivered_markdown)

    def test_main_archive_failure_does_not_block_stdout_delivery(self):
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
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "repository_root", return_value=Path("/repo")),
            mock.patch.object(cli, "collect_uncommitted", return_value=(ReviewSource("uncommitted"), [file])),
            mock.patch.object(cli.sys.stdin, "isatty", return_value=True),
            mock.patch.object(cli.sys, "stdout", stdout),
            mock.patch.object(cli.sys, "stderr", stderr),
            mock.patch.object(cli, "ReviewApp", FakeReviewApp),
            mock.patch.object(cli, "reset_terminal_after_tui"),
            mock.patch.object(cli, "archive_review", side_effect=OSError("disk full")),
        ):
            self.assertEqual(cli.main(["--source", "uncommitted", "--stdout"]), 0)

        self.assertIn("Needs work.", stdout.getvalue())
        self.assertIn("could not archive review", stderr.getvalue())
        self.assertIn("disk full", stderr.getvalue())

    def test_main_archive_failure_does_not_block_tmux_delivery(self):
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
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "repository_root", return_value=Path("/repo")),
            mock.patch.object(cli, "collect_uncommitted", return_value=(ReviewSource("uncommitted"), [file])),
            mock.patch.object(cli.sys.stdin, "isatty", return_value=True),
            mock.patch.object(cli.sys, "stdout", stdout),
            mock.patch.object(cli.sys, "stderr", stderr),
            mock.patch.object(cli, "ReviewApp", FakeReviewApp),
            mock.patch.object(cli, "reset_terminal_after_tui"),
            mock.patch.object(cli, "archive_review", side_effect=OSError("read-only file system")),
            mock.patch.object(cli, "deliver_review", return_value=0) as deliver_review,
        ):
            self.assertEqual(cli.main(["--source", "uncommitted"]), 0)

        deliver_review.assert_called_once()
        delivered_message = deliver_review.call_args.args[0]
        delivered_markdown = deliver_review.call_args.kwargs["markdown_message"]
        self.assertEqual(delivered_message, delivered_markdown)
        self.assertIn("read-only file system", stderr.getvalue())

    def test_reset_terminal_after_tui_clears_screen_and_moves_to_bottom(self):
        class TtyStringIO(io.StringIO):
            def isatty(self):
                return True

        output = TtyStringIO()
        with mock.patch.object(cli.shutil, "get_terminal_size", return_value=os.terminal_size((100, 40))):
            cli.reset_terminal_after_tui(output)

        self.assertEqual(output.getvalue(), "\x1b[0m\x1b[?25h\x1b[2J\x1b[40;1H")

    def test_reset_terminal_after_tui_is_skipped_for_non_tty_streams(self):
        output = io.StringIO()

        cli.reset_terminal_after_tui(output)

        self.assertEqual(output.getvalue(), "")

    def test_main_resets_terminal_after_tui_before_delivery_prompt(self):
        file = create_review_file("app.py", "modified", ["old"], ["new"])
        events = []

        class TtyStringIO(io.StringIO):
            def isatty(self):
                return True

        class FakeReviewApp:
            def __init__(self, state):
                self.state = state

            def run(self):
                self.state.add_comment("Needs work.")
                events.append("tui")
                return self.state

        def reset_terminal_after_tui():
            events.append("reset")

        def deliver_review(message, **_kwargs):
            events.append("deliver")
            return 0

        with (
            mock.patch.object(cli, "repository_root", return_value=Path("/repo")),
            mock.patch.object(cli, "collect_uncommitted", return_value=(ReviewSource("uncommitted"), [file])),
            mock.patch.object(cli.sys.stdin, "isatty", return_value=True),
            mock.patch.object(cli.sys, "stdout", TtyStringIO()),
            mock.patch.object(cli, "ReviewApp", FakeReviewApp),
            mock.patch.object(cli, "archive_review"),
            mock.patch.object(cli, "reset_terminal_after_tui", reset_terminal_after_tui),
            mock.patch.object(cli, "deliver_review", deliver_review),
        ):
            self.assertEqual(cli.main(["--source", "uncommitted"]), 0)

        self.assertEqual(events, ["tui", "reset", "deliver"])

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

    def test_review_ls_lists_recent_archived_reviews_without_git_repository(self):
        reviews = [
            cli.ArchivedReview(Path(f"/archive/{index}.json"), f"/repo/{index}", f"branch-{index}", f"message {index}\n")
            for index in range(11)
        ]
        with (
            mock.patch.object(cli, "list_archived_reviews", return_value=reviews[:10]) as list_archived_reviews,
            mock.patch.object(cli, "repository_root", side_effect=AssertionError("history command should not inspect git")),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            self.assertEqual(cli.main(["ls"]), 0)

        list_archived_reviews.assert_called_once_with(limit=10)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 10)
        self.assertIn("1. 0  branch-0  /repo/0", lines[0])
        self.assertIn("10. 9  branch-9  /repo/9", lines[-1])

    def test_review_ls_reports_when_no_archived_reviews_exist(self):
        with (
            mock.patch.object(cli, "list_archived_reviews", return_value=[]),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            self.assertEqual(cli.main(["ls"]), 0)

        self.assertEqual(stdout.getvalue(), "No saved reviews.\n")

    def test_review_display_selects_archived_review_and_prints_message(self):
        reviews = [
            cli.ArchivedReview(Path("/archive/first.json"), "/repo/one", "main", "first review\n"),
            cli.ArchivedReview(Path("/archive/second.json"), "/repo/two", "feature", "second review\n"),
        ]
        with (
            mock.patch.object(cli, "list_archived_reviews", return_value=reviews),
            mock.patch.object(cli, "select_option_on_stream", return_value="1") as select_option,
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            self.assertEqual(cli.main(["display"]), 0)

        title, options = select_option.call_args.args
        self.assertEqual(title, "Saved reviews")
        self.assertEqual([option.value for option in options], ["0", "1"])
        self.assertEqual(options[1].label, "second  feature  /repo/two")
        self.assertEqual(stdout.getvalue(), "second review\n")

    def test_review_display_file_saves_selected_archived_review(self):
        review = cli.ArchivedReview(Path("/archive/review.json"), "/repo", "main", "# Review\n")
        saved_path = Path("/tmp/review-20260504-0907.md")
        with (
            mock.patch.object(cli, "list_archived_reviews", return_value=[review]),
            mock.patch.object(cli, "select_option_on_stream", return_value="0"),
            mock.patch.object(cli, "save_review_file", return_value=saved_path) as save_review_file,
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            self.assertEqual(cli.main(["display", "--file"]), 0)

        save_review_file.assert_called_once_with("# Review\n")
        self.assertEqual(stdout.getvalue(), f"Saved review to {saved_path}.\n")

    def test_review_display_file_failure_prints_selected_review_as_fallback(self):
        review = cli.ArchivedReview(Path("/archive/review.json"), "/repo", "main", "# Review\n")
        with (
            mock.patch.object(cli, "list_archived_reviews", return_value=[review]),
            mock.patch.object(cli, "select_option_on_stream", return_value="0"),
            mock.patch.object(cli, "save_review_file", side_effect=OSError("read-only directory")),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            mock.patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            self.assertEqual(cli.main(["display", "-f"]), 1)

        self.assertEqual(stdout.getvalue(), "# Review\n")
        self.assertIn("could not save review file", stderr.getvalue())
        self.assertIn("read-only directory", stderr.getvalue())

    def test_review_display_text_prompt_goes_to_stderr_when_stdout_is_review_body(self):
        reviews = [
            cli.ArchivedReview(Path("/archive/new.json"), "/repo/new", "feature", "new review\n"),
            cli.ArchivedReview(Path("/archive/old.json"), "/repo/old", "main", "old review\n"),
        ]
        stdin = io.StringIO("2\n")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "list_archived_reviews", return_value=reviews),
            mock.patch.object(cli.sys, "stdin", stdin),
            mock.patch.object(cli.sys, "stdout", stdout),
            mock.patch.object(cli.sys, "stderr", stderr),
        ):
            self.assertEqual(cli.main(["display"]), 0)

        self.assertEqual(stdout.getvalue(), "old review\n")
        self.assertIn("Saved reviews:", stderr.getvalue())
        self.assertIn("Select option [1]:", stderr.getvalue())
        self.assertNotIn("Saved reviews:", stdout.getvalue())

    def test_review_display_q_cancels_without_printing_review_body(self):
        reviews = [
            cli.ArchivedReview(Path("/archive/new.json"), "/repo/new", "feature", "new review\n"),
        ]
        stdin = io.StringIO("q\n")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "list_archived_reviews", return_value=reviews),
            mock.patch.object(cli.sys, "stdin", stdin),
            mock.patch.object(cli.sys, "stdout", stdout),
            mock.patch.object(cli.sys, "stderr", stderr),
        ):
            self.assertEqual(cli.main(["display"]), 130)

        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Saved reviews:", stderr.getvalue())
        self.assertIn("review cancelled", stderr.getvalue())

    def test_review_source_q_cancels_before_collecting_changes(self):
        stdin = io.StringIO("q\n")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "repository_root", return_value=Path("/repo")),
            mock.patch.object(cli, "collect_uncommitted", side_effect=AssertionError("source selection should cancel first")),
            mock.patch.object(cli.sys, "stdin", stdin),
            mock.patch.object(cli.sys, "stdout", stdout),
            mock.patch.object(cli.sys, "stderr", stderr),
        ):
            self.assertEqual(cli.main(["--no-tui", "--stdout"]), 130)

        self.assertIn("Review source:", stdout.getvalue())
        self.assertIn("review cancelled", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
