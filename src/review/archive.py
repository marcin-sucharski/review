from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .git import current_branch
from .review_state import ReviewState


def archive_review(state: ReviewState, review_message: str, *, environ: Mapping[str, str] | None = None) -> Path:
    directory = review_archive_dir(environ)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _review_filename()
    payload = {
        "path": str(state.repository_root),
        "branch": current_branch(state.repository_root),
        "review_message": review_message,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def review_archive_dir(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    data_home = values.get("XDG_DATA_HOME")
    base = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "review" / "reviews"


def _review_filename() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid.uuid4().hex}.json"
