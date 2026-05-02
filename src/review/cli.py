from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .errors import GitCommandError, NoChangesFound, NotAGitRepository, ReviewError, TmuxSendError, TmuxUnavailable
from .format_review import format_review
from .git import collect_branch_comparison, collect_uncommitted, default_branch_candidates, repository_root
from .review_state import ReviewState
from .tmux import list_panes, send_text
from .tui.app import ReviewApp
from .tui.menu import MenuOption, select_option


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="review", description="Review Git changes in a terminal UI.")
    parser.add_argument("--version", action="version", version=f"review {__version__}")
    parser.add_argument("--source", choices=["uncommitted", "branch"], help="Review source. If omitted, prompt interactively.")
    parser.add_argument("--target", help="Target branch for --source branch.")
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

        message = format_review(state)
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


def prompt_source() -> str:
    return select_option(
        "Review source",
        [
            MenuOption("Review uncommitted changes", "uncommitted", "working tree and staged changes"),
            MenuOption("Review PR-style changes", "branch", "compare current HEAD against a branch"),
        ],
    )


def prompt_branch(root: Path) -> str:
    branches = default_branch_candidates(root)
    if not branches:
        raise ReviewError("no branches are available for comparison")
    return select_option("Target branch", [MenuOption(branch, branch) for branch in branches])


def deliver_review(message: str) -> int:
    try:
        panes = list_panes()
    except TmuxUnavailable as exc:
        print(f"tmux unavailable: {exc}")
        print(message, end="")
        return 0

    options = [MenuOption("No tmux pane", "stdout", "print review to stdout")]
    options.extend(MenuOption(pane.display(), pane.pane_id) for pane in panes)
    choice = select_option("Delivery target", options)
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
