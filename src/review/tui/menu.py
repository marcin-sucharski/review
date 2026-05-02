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


@dataclass(frozen=True)
class MenuOption:
    label: str
    value: str
    detail: str = ""


def select_option(title: str, options: list[MenuOption], *, default_index: int = 0) -> str:
    if not options:
        raise ValueError("menu requires at least one option")
    default_index = max(0, min(default_index, len(options) - 1))
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            return _select_option_inline(title, options, default_index, sys.stdin, sys.stdout)
        except OSError:
            pass
    return _select_option_text(title, options, default_index)


def _select_option_inline(title: str, options: list[MenuOption], selected: int, input_stream: TextIO, output_stream: TextIO) -> str:
    input_fd = input_stream.fileno()
    old_settings = termios.tcgetattr(input_fd)
    printed_lines = 0
    while True:
        try:
            tty.setraw(input_fd)
            lines = _render_menu_lines(title, options, selected, use_color=True)
            if printed_lines:
                output_stream.write(f"\x1b[{printed_lines}F")
            for line in lines:
                output_stream.write("\r\x1b[2K" + line + "\n")
            output_stream.flush()
            printed_lines = len(lines)

            key = _read_key(input_fd)
            if key in {"up", "k", "K"}:
                selected = max(0, selected - 1)
            elif key in {"down", "j", "J"}:
                selected = min(len(options) - 1, selected + 1)
            elif key == "home":
                selected = 0
            elif key == "end":
                selected = len(options) - 1
            elif key == "enter":
                return options[selected].value
            elif key == "escape":
                raise KeyboardInterrupt
        finally:
            termios.tcsetattr(input_fd, termios.TCSADRAIN, old_settings)


def _select_option_text(title: str, options: list[MenuOption], default_index: int) -> str:
    print(f"{title}:")
    for index, option in enumerate(options, start=1):
        default = " (default)" if index - 1 == default_index else ""
        detail = f" - {option.detail}" if option.detail else ""
        print(f"  {index}. {option.label}{detail}{default}")
    while True:
        choice = input(f"Select option [{default_index + 1}]: ").strip()
        if not choice:
            return options[default_index].value
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1].value
        for option in options:
            if choice == option.value or choice == option.label:
                return option.value
        print("Please select a listed option.")


def _render_menu_lines(title: str, options: list[MenuOption], selected: int, *, use_color: bool) -> list[str]:
    lines = [f"{title}  (Use Up/Down and Enter; Esc cancels)"]
    for index, option in enumerate(options):
        prefix = ">" if index == selected else " "
        text = f"{prefix} {option.label}"
        if option.detail:
            text += f" - {option.detail}"
        if use_color and index == selected:
            text = f"\x1b[1;34m{text}\x1b[0m"
        lines.append(text)
    return lines


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
    if sequence == b"\x1b":
        return "escape"
    return KEY_SEQUENCES.get(sequence, sequence.decode(errors="ignore"))


def _is_known_key_prefix(sequence: bytes) -> bool:
    return any(key.startswith(sequence) for key in KEY_SEQUENCES)
