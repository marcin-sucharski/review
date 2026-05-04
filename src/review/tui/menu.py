from __future__ import annotations

import select
import os
import sys
import termios
import tty
from dataclasses import dataclass
from typing import TextIO

KEY_SEQUENCES = {
    b"\x1b[A": "up",
    b"\x1bOA": "up",
    b"\x1b[B": "down",
    b"\x1bOB": "down",
    b"\x1b[H": "home",
    b"\x1bOH": "home",
    b"\x1b[F": "end",
    b"\x1bOF": "end",
    b"\x1b[1~": "home",
    b"\x1b[4~": "end",
}
BRANCH_PAGE_SIZE = 5


@dataclass(frozen=True)
class MenuOption:
    label: str
    value: str
    detail: str = ""


def select_option(title: str, options: list[MenuOption], *, default_index: int = 0, cancel_requires_double: bool = False) -> str:
    return select_option_on_stream(title, options, default_index=default_index, cancel_requires_double=cancel_requires_double)


def select_option_on_stream(
    title: str,
    options: list[MenuOption],
    *,
    default_index: int = 0,
    cancel_requires_double: bool = False,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> str:
    if not options:
        raise ValueError("menu requires at least one option")
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    default_index = max(0, min(default_index, len(options) - 1))
    if input_stream.isatty() and output_stream.isatty():
        try:
            return _select_option_inline(title, options, default_index, input_stream, output_stream, cancel_requires_double)
        except OSError:
            pass
    return _select_option_text(title, options, default_index, cancel_requires_double, input_stream, output_stream)


def select_branch_target(
    title: str,
    current_branch: str,
    branches: list[str],
    *,
    cancel_requires_double: bool = False,
) -> str:
    if not branches:
        raise ValueError("branch menu requires at least one branch")
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            return _select_branch_inline(title, current_branch, branches, sys.stdin, sys.stdout, cancel_requires_double)
        except OSError:
            pass
    return _select_branch_text(title, current_branch, branches, cancel_requires_double)


def _select_branch_inline(
    title: str,
    current_branch: str,
    branches: list[str],
    input_stream: TextIO,
    output_stream: TextIO,
    cancel_requires_double: bool,
) -> str:
    input_fd = input_stream.fileno()
    old_settings = termios.tcgetattr(input_fd)
    printed_lines = 0
    selected = 0
    query = ""
    cancel_armed = False
    while True:
        try:
            tty.setraw(input_fd)
            filtered = _filter_branches(branches, query)
            selected = _clamp_selected_branch(selected, filtered)
            lines = _render_branch_menu_lines(
                title,
                current_branch,
                filtered,
                selected,
                query,
                use_color=True,
                cancel_armed=cancel_armed,
            )
            if printed_lines:
                output_stream.write(f"\x1b[{printed_lines}F")
            render_count = max(printed_lines, len(lines))
            for index in range(render_count):
                line = lines[index] if index < len(lines) else ""
                output_stream.write("\r\x1b[2K" + line + "\n")
            output_stream.flush()
            printed_lines = render_count

            key = _read_key(input_fd)
            if key == "up":
                selected = max(0, selected - 1)
                cancel_armed = False
            elif key == "down":
                selected = min(max(0, len(filtered) - 1), selected + 1)
                cancel_armed = False
            elif key == "home":
                selected = 0
                cancel_armed = False
            elif key == "end":
                selected = max(0, len(filtered) - 1)
                cancel_armed = False
            elif key == "enter":
                if filtered:
                    _clear_rendered_menu(output_stream, printed_lines)
                    return filtered[selected]
            elif key == "ctrl_c":
                if cancel_requires_double and not cancel_armed:
                    cancel_armed = True
                else:
                    _clear_rendered_menu(output_stream, printed_lines)
                    raise KeyboardInterrupt
            elif key == "escape":
                _clear_rendered_menu(output_stream, printed_lines)
                raise KeyboardInterrupt
            elif _is_backspace_key(key):
                query = query[:-1]
                selected = 0
                cancel_armed = False
            elif _is_branch_filter_key(key):
                query += key
                selected = 0
                cancel_armed = False
            else:
                cancel_armed = False
        finally:
            termios.tcsetattr(input_fd, termios.TCSADRAIN, old_settings)


def _select_branch_text(title: str, current_branch: str, branches: list[str], cancel_requires_double: bool) -> str:
    print(f"{title}:")
    for index, branch in enumerate(branches, start=1):
        print(f"  {index}. {current_branch} -> {branch}")
    cancel_armed = False
    while True:
        try:
            choice = input("Select target branch [1]: ").strip()
        except KeyboardInterrupt:
            if cancel_requires_double and not cancel_armed:
                print("\nPress Ctrl+C again to cancel.")
                cancel_armed = True
                continue
            raise
        if not choice:
            return branches[0]
        if choice.isdigit() and 1 <= int(choice) <= len(branches):
            return branches[int(choice) - 1]
        exact = [branch for branch in branches if branch == choice]
        if exact:
            return exact[0]
        matches = _filter_branches(branches, choice)
        if len(matches) == 1:
            return matches[0]
        print("Please select a listed branch or a unique branch-name search.")
        cancel_armed = False


def _select_option_inline(
    title: str,
    options: list[MenuOption],
    selected: int,
    input_stream: TextIO,
    output_stream: TextIO,
    cancel_requires_double: bool,
) -> str:
    input_fd = input_stream.fileno()
    old_settings = termios.tcgetattr(input_fd)
    printed_lines = 0
    cancel_armed = False
    while True:
        try:
            tty.setraw(input_fd)
            lines = _render_menu_lines(title, options, selected, use_color=True, cancel_armed=cancel_armed)
            if printed_lines:
                output_stream.write(f"\x1b[{printed_lines}F")
            render_count = max(printed_lines, len(lines))
            for index in range(render_count):
                line = lines[index] if index < len(lines) else ""
                output_stream.write("\r\x1b[2K" + line + "\n")
            output_stream.flush()
            printed_lines = render_count

            key = _read_key(input_fd)
            if key in {"up", "k", "K"}:
                selected = max(0, selected - 1)
                cancel_armed = False
            elif key in {"down", "j", "J"}:
                selected = min(len(options) - 1, selected + 1)
                cancel_armed = False
            elif key == "home":
                selected = 0
                cancel_armed = False
            elif key == "end":
                selected = len(options) - 1
                cancel_armed = False
            elif key == "enter":
                _clear_rendered_menu(output_stream, printed_lines)
                return options[selected].value
            elif key == "ctrl_c":
                if cancel_requires_double and not cancel_armed:
                    cancel_armed = True
                else:
                    _clear_rendered_menu(output_stream, printed_lines)
                    raise KeyboardInterrupt
            elif key == "escape":
                _clear_rendered_menu(output_stream, printed_lines)
                raise KeyboardInterrupt
            else:
                cancel_armed = False
        finally:
            termios.tcsetattr(input_fd, termios.TCSADRAIN, old_settings)


def _clear_rendered_menu(output_stream: TextIO, line_count: int) -> None:
    if line_count <= 0:
        return
    output_stream.write(f"\x1b[{line_count}F")
    for index in range(line_count):
        output_stream.write("\r\x1b[2K")
        if index < line_count - 1:
            output_stream.write("\x1b[1E")
    if line_count > 1:
        output_stream.write(f"\x1b[{line_count - 1}F")
    else:
        output_stream.write("\r")
    output_stream.flush()


def _select_option_text(
    title: str,
    options: list[MenuOption],
    default_index: int,
    cancel_requires_double: bool,
    input_stream: TextIO,
    output_stream: TextIO,
) -> str:
    output_stream.write(f"{title}:\n")
    for index, option in enumerate(options, start=1):
        default = " (default)" if index - 1 == default_index else ""
        detail = f" - {option.detail}" if option.detail else ""
        output_stream.write(f"  {index}. {option.label}{detail}{default}\n")
    output_stream.flush()
    cancel_armed = False
    while True:
        try:
            choice = _read_text_menu_choice(input_stream, output_stream, f"Select option [{default_index + 1}]: ")
        except KeyboardInterrupt:
            if cancel_requires_double and not cancel_armed:
                output_stream.write("\nPress Ctrl+C again to cancel.\n")
                output_stream.flush()
                cancel_armed = True
                continue
            raise
        if not choice:
            return options[default_index].value
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1].value
        for option in options:
            if choice == option.value or choice == option.label:
                return option.value
        output_stream.write("Please select a listed option.\n")
        output_stream.flush()
        cancel_armed = False


def _read_text_menu_choice(input_stream: TextIO, output_stream: TextIO, prompt: str) -> str:
    output_stream.write(prompt)
    output_stream.flush()
    choice = input_stream.readline()
    if choice == "":
        raise EOFError
    return choice.strip()


def _render_menu_lines(title: str, options: list[MenuOption], selected: int, *, use_color: bool, cancel_armed: bool = False) -> list[str]:
    lines = [f"{title}  (Use Up/Down and Enter; Esc cancels)"]
    for index, option in enumerate(options):
        prefix = ">" if index == selected else " "
        text = f"{prefix} {option.label}"
        if option.detail:
            text += f" - {option.detail}"
        if use_color and index == selected:
            text = f"\x1b[1;34m{text}\x1b[0m"
        lines.append(text)
    if cancel_armed:
        warning = "Press Ctrl+C again to cancel."
        if use_color:
            warning = f"\x1b[1;33m{warning}\x1b[0m"
        lines.append(warning)
    return lines


def _render_branch_menu_lines(
    title: str,
    current_branch: str,
    branches: list[str],
    selected: int,
    query: str,
    *,
    use_color: bool,
    cancel_armed: bool = False,
) -> list[str]:
    lines = [f"{title}  (type to filter; Up/Down and Enter; Esc cancels)"]
    if branches:
        selected = max(0, min(selected, len(branches) - 1))
        window_start = _branch_window_start(selected, len(branches))
        visible = branches[window_start : window_start + BRANCH_PAGE_SIZE]
        for offset, branch in enumerate(visible, start=window_start):
            prefix = ">" if offset == selected else " "
            text = f"{prefix} {current_branch} -> {branch}"
            if use_color and offset == selected:
                text = f"\x1b[1;34m{text}\x1b[0m"
            lines.append(text)
        above = window_start
        below = max(0, len(branches) - window_start - len(visible))
        if above or below:
            parts = []
            if above:
                parts.append(f"{above} above")
            if below:
                parts.append(f"{below} below")
            lines.append("  " + ", ".join(parts))
    else:
        lines.append("  No branches match.")
    lines.append(f"Search: {query}")
    if cancel_armed:
        warning = "Press Ctrl+C again to cancel."
        if use_color:
            warning = f"\x1b[1;33m{warning}\x1b[0m"
        lines.append(warning)
    return lines


def _branch_window_start(selected: int, count: int) -> int:
    if count <= BRANCH_PAGE_SIZE:
        return 0
    half_page = BRANCH_PAGE_SIZE // 2
    return max(0, min(count - BRANCH_PAGE_SIZE, selected - half_page))


def _filter_branches(branches: list[str], query: str) -> list[str]:
    query = query.casefold()
    if not query:
        return branches
    return [branch for branch in branches if query in branch.casefold()]


def _clamp_selected_branch(selected: int, branches: list[str]) -> int:
    if not branches:
        return 0
    return max(0, min(selected, len(branches) - 1))


def _read_key(input_fd: int) -> str:
    sequence = os.read(input_fd, 1)
    if sequence == b"\x1b":
        while select.select([input_fd], [], [], 0.15)[0]:
            sequence += os.read(input_fd, 1)
            if sequence in KEY_SEQUENCES:
                return KEY_SEQUENCES[sequence]
            if not _is_known_key_prefix(sequence) or len(sequence) >= 6:
                break
        return _decode_key(sequence)
    return _decode_key(sequence)


def _decode_key(sequence: bytes) -> str:
    if sequence in {b"\r", b"\n"}:
        return "enter"
    if sequence == b"\x03":
        return "ctrl_c"
    if sequence == b"\x1b":
        return "escape"
    return KEY_SEQUENCES.get(sequence, sequence.decode(errors="ignore"))


def _is_backspace_key(key: str) -> bool:
    return key in {"\x7f", "\b"}


def _is_branch_filter_key(key: str) -> bool:
    return len(key) == 1 and key.isprintable()


def _is_known_key_prefix(sequence: bytes) -> bool:
    return any(key.startswith(sequence) for key in KEY_SEQUENCES)
