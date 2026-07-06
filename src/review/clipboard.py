from __future__ import annotations

import base64
import sys
from typing import TextIO

from .tmux import Runner, default_runner, inside_tmux, tmux_available


def copy_text_to_clipboard(
    text: str,
    runner: Runner = default_runner,
    stream: TextIO | None = None,
) -> bool:
    if not text:
        return False
    in_tmux = inside_tmux()
    if in_tmux and tmux_available():
        result = runner(["tmux", "load-buffer", "-w", "-"], text)
        if result.returncode == 0:
            return True
    return write_osc52_clipboard(text, inside_tmux=in_tmux, stream=stream)


def write_osc52_clipboard(text: str, *, inside_tmux: bool = False, stream: TextIO | None = None) -> bool:
    if not text:
        return False
    target = sys.stdout if stream is None else stream
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    sequence = f"\x1b]52;c;{payload}\x07"
    if inside_tmux:
        sequence = f"\x1bPtmux;\x1b{sequence}\x1b\\"
    try:
        target.write(sequence)
        target.flush()
    except OSError:
        return False
    return True
