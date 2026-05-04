from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__
from .archive import archive_review
from .errors import GitCommandError, NoChangesFound, NotAGitRepository, ReviewError, TmuxSendError, TmuxUnavailable
from .format_review import format_review
from .git import collect_branch_comparison, collect_uncommitted, current_branch, default_branch_candidates, repository_root
from .review_state import ReviewState
from .tmux import list_panes, send_text
from .tui.app import ReviewApp
from .tui.menu import MenuOption, select_branch_target, select_option


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="review", description="Review Git changes in a terminal UI.")
    parser.add_argument("--version", action="version", version=f"review {__version__}")
    parser.add_argument("--source", choices=["uncommitted", "branch"], help="Review source. If omitted, prompt interactively.")
    parser.add_argument("--target", help="Target branch for --source branch.")
    parser.add_argument(
        "-o",
        "--output-format",
        choices=["xml", "md"],
        default="md",
        help="Review message output format. Defaults to md.",
    )
    parser.add_argument("--stdout", action="store_true", help="Print review comments instead of prompting for tmux delivery.")
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = repository_root(Path.cwd())
        source_name = args.source or prompt_source()
        if source_name == "branch":
            target = args.target or prompt_branch(root)
            source, files = collect_branch_comparison(root, target)
        else:
            source, files = collect_uncommitted(root)

        state = ReviewState(root, source, files)
        if not args.no_tui:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ReviewError("interactive TUI requires a terminal")
            ReviewApp(state).run()
            reset_terminal_after_tui()

        message = format_review(state, args.output_format)
        if state.comments:
            archive_review_best_effort(state, message)
        if args.stdout:
            sys.stdout.write(message)
            return 0
        if not state.comments:
            sys.stdout.write(message)
            return 0
        return deliver_review(message)
    except (KeyboardInterrupt, EOFError):
        sys.stderr.write("review cancelled\n")
        return 130
    except NoChangesFound as exc:
        sys.stdout.write(f"review: {exc}\n")
        return 0
    except GitCommandError as exc:
        command = " ".join(exc.command)
        sys.stderr.write(f"review: {command} failed: {exc}\n")
        return 1
    except (NotAGitRepository, ReviewError) as exc:
        sys.stderr.write(f"review: {exc}\n")
        return 1


def archive_review_best_effort(state: ReviewState, message: str) -> None:
    try:
        archive_review(state, message)
    except (OSError, GitCommandError) as exc:
        sys.stderr.write(f"review: could not archive review: {exc}\n")


def reset_terminal_after_tui(output_stream=None) -> None:
    stream = output_stream or sys.stdout
    if not getattr(stream, "isatty", lambda: False)():
        return
    height = shutil.get_terminal_size(fallback=(80, 24)).lines
    stream.write(f"\x1b[0m\x1b[?25h\x1b[2J\x1b[{height};1H")
    stream.flush()


def prompt_source() -> str:
    return select_option(
        "Review source",
        [
            MenuOption("Review uncommitted changes", "uncommitted", "working tree and staged changes"),
            MenuOption("Review PR-style changes", "branch", "compare current HEAD against a branch"),
        ],
    )


def prompt_branch(root: Path) -> str:
    current = current_branch(root)
    branches = default_branch_candidates(root)
    branches = [branch for branch in branches if branch != current]
    if not branches:
        raise ReviewError("no branches are available for comparison")
    return select_branch_target("Target branch", current, branches, cancel_requires_double=True)


def deliver_review(message: str) -> int:
    try:
        panes = list_panes()
    except TmuxUnavailable as exc:
        print(f"tmux unavailable: {exc}")
        print(message, end="")
        return 0

    options = [MenuOption("No tmux pane", "stdout", "print review to stdout")]
    options.extend(MenuOption(pane.display(), pane.pane_id) for pane in panes)
    choice = select_option("Delivery target", options, cancel_requires_double=True)
    if choice == "stdout":
        print(message, end="")
        return 0
    for pane in panes:
        if pane.pane_id == choice:
            try:
                send_text(pane.pane_id, message)
            except (TmuxUnavailable, TmuxSendError) as exc:
                sys.stderr.write(f"review: tmux delivery failed: {exc}\n")
                print(message, end="")
                return 1
            print(f"Sent review to tmux pane {pane.pane_id}.")
            return 0
    print(message, end="")
    return 0
