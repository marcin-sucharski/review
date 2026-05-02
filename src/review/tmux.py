from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from .errors import TmuxSendError, TmuxUnavailable


Runner = Callable[[list[str], str | None], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class TmuxPane:
    pane_id: str
    session_name: str
    window_index: str
    pane_index: str
    pane_title: str
    current_command: str
    current: bool = False

    def location(self) -> str:
        return f"{self.session_name}:{self.window_index}.{self.pane_index}"

    def display(self) -> str:
        title = self.pane_title or "(no title)"
        command = self.current_command or "unknown"
        marker = " [current]" if self.current else ""
        return f"{self.pane_id}  {self.location()}  {command}  title=\"{title}\"{marker}"


def default_runner(command: list[str], input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, input=input_text, text=True, capture_output=True, check=False)


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def inside_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def parse_panes(output: str, current_pane_id: str | None = None) -> list[TmuxPane]:
    panes: list[TmuxPane] = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        parts += [""] * (6 - len(parts))
        pane_id, session, window, pane, title, command = parts[:6]
        panes.append(
            TmuxPane(
                pane_id=pane_id,
                session_name=session,
                window_index=window,
                pane_index=pane,
                pane_title=title,
                current_command=command,
                current=pane_id == current_pane_id,
            )
        )
    return panes


def current_pane(runner: Runner = default_runner) -> str | None:
    if not tmux_available():
        return None
    result = runner(["tmux", "display-message", "-p", "#{pane_id}"], None)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def list_panes(runner: Runner = default_runner) -> list[TmuxPane]:
    if not tmux_available():
        raise TmuxUnavailable("tmux is not installed")
    current = current_pane(runner)
    result = runner(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{pane_id}\t#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_title}\t#{pane_current_command}",
        ],
        None,
    )
    if result.returncode != 0:
        raise TmuxUnavailable(result.stderr.strip() or "tmux panes could not be listed")
    return parse_panes(result.stdout, current)


def send_text(pane_id: str, text: str, runner: Runner = default_runner) -> None:
    if not tmux_available():
        raise TmuxUnavailable("tmux is not installed")
    load = runner(["tmux", "load-buffer", "-"], text)
    if load.returncode != 0:
        raise TmuxSendError(load.stderr.strip() or "tmux load-buffer failed")
    paste = runner(["tmux", "paste-buffer", "-t", pane_id], None)
    if paste.returncode != 0:
        raise TmuxSendError(paste.stderr.strip() or f"tmux paste-buffer failed for {pane_id}")
    enter = runner(["tmux", "send-keys", "-t", pane_id, "Enter"], None)
    if enter.returncode != 0:
        raise TmuxSendError(enter.stderr.strip() or f"tmux send-keys failed for {pane_id}")
