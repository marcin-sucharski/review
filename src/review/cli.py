from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .archive import ArchivedReview, archive_review, list_archived_reviews
from .errors import GitCommandError, NoChangesFound, NotAGitRepository, ReviewError, TmuxSendError, TmuxUnavailable
from .format_review import format_review
from .git import collect_branch_comparison, collect_uncommitted, current_branch, default_branch_candidates, repository_root
from .review_state import ReviewState
from .tmux import list_panes, send_text
from .tui.app import ReviewApp
from .tui.menu import MenuOption, select_branch_target, select_option, select_option_on_stream


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


def build_history_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="review", description="Review Git changes in a terminal UI.")
    parser.add_argument("--version", action="version", version=f"review {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("ls", help="List recent saved reviews.")
    display = subcommands.add_parser("display", help="Select and print a saved review.")
    display.add_argument("-f", "--file", action="store_true", help="Save the selected review to a local file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    if raw_argv and raw_argv[0] in {"ls", "display"}:
        return run_history_command(raw_argv)

    parser = build_parser()
    args = parser.parse_args(raw_argv)
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
        markdown_message = message if args.output_format == "md" else format_review(state, "md")
        return deliver_review(message, markdown_message=markdown_message)
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


def run_history_command(argv: list[str]) -> int:
    parser = build_history_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "ls":
            return list_saved_reviews()
        if args.command == "display":
            return display_saved_review(save_to_file=args.file)
        parser.error(f"unknown command: {args.command}")
    except (KeyboardInterrupt, EOFError):
        sys.stderr.write("review cancelled\n")
        return 130
    except OSError as exc:
        sys.stderr.write(f"review: {exc}\n")
        return 1
    return 1


def list_saved_reviews() -> int:
    reviews = list_archived_reviews(limit=10)
    if not reviews:
        sys.stdout.write("No saved reviews.\n")
        return 0
    for index, review in enumerate(reviews, start=1):
        sys.stdout.write(_archived_review_label(review, index=index) + "\n")
    return 0


def display_saved_review(*, save_to_file: bool = False) -> int:
    reviews = list_archived_reviews(limit=10)
    if not reviews:
        sys.stdout.write("No saved reviews.\n")
        return 0
    options = [
        MenuOption(_archived_review_label(review), str(index - 1), review.archive_path.name)
        for index, review in enumerate(reviews, start=1)
    ]
    choice = select_option_on_stream(
        "Saved reviews",
        options,
        cancel_requires_double=True,
        output_stream=sys.stderr if not sys.stdout.isatty() else sys.stdout,
    )
    review = reviews[int(choice)]
    if save_to_file:
        try:
            path = save_review_file(review.review_message)
        except OSError as exc:
            sys.stderr.write(f"review: could not save review file: {exc}\n")
            sys.stdout.write(review.review_message)
            return 1
        print(f"Saved review to {path}.")
        return 0
    sys.stdout.write(review.review_message)
    return 0


def _archived_review_label(review: ArchivedReview, *, index: int | None = None) -> str:
    prefix = f"{index}. " if index is not None else ""
    return f"{prefix}{review.timestamp_label}  {review.branch}  {review.repository_path}"


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
            MenuOption("Review PR-style changes", "branch", "compare branch and current uncommitted changes"),
        ],
    )


def prompt_branch(root: Path) -> str:
    current = current_branch(root)
    branches = default_branch_candidates(root)
    branches = [branch for branch in branches if branch != current]
    if not branches:
        raise ReviewError("no branches are available for comparison")
    return select_branch_target("Target branch", current, branches, cancel_requires_double=True)


def timestamped_review_path(directory: Path, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    path = directory / f"review-{stamp}.md"
    if not path.exists():
        return path

    counter = 2
    while True:
        candidate = directory / f"review-{stamp}-{counter}.md"
        if not candidate.exists():
            return candidate
        counter += 1


def save_review_file(markdown_message: str, directory: Path | None = None, now: datetime | None = None) -> Path:
    path = timestamped_review_path(directory or Path.cwd(), now)
    path.write_text(markdown_message, encoding="utf-8")
    return path


def deliver_review(
    message: str,
    *,
    markdown_message: str | None = None,
    output_dir: Path | None = None,
    now: datetime | None = None,
) -> int:
    try:
        panes = list_panes()
    except TmuxUnavailable:
        panes = []

    options = [
        MenuOption("Save to file", "file", "write Markdown review to ./review-YYYYMMDD-HHMM.md"),
        MenuOption("Send to terminal", "stdout", "print review to stdout"),
    ]
    options.extend(MenuOption(pane.display(), pane.pane_id) for pane in panes)
    choice = select_option("Delivery target", options, cancel_requires_double=True)
    if choice == "file":
        file_message = markdown_message or message
        try:
            path = save_review_file(file_message, output_dir, now)
        except OSError as exc:
            sys.stderr.write(f"review: could not save review file: {exc}\n")
            print(file_message, end="")
            return 1
        print(f"Saved review to {path}.")
        return 0
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
