from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .git import current_branch
from .review_state import ReviewState


@dataclass(frozen=True)
class ArchivedReview:
    archive_path: Path
    repository_path: str
    branch: str
    review_message: str

    @property
    def timestamp_label(self) -> str:
        return self.archive_path.stem.split("-", 1)[0]


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


def list_archived_reviews(*, limit: int = 10, environ: Mapping[str, str] | None = None) -> list[ArchivedReview]:
    if limit <= 0:
        return []
    directory = review_archive_dir(environ)
    try:
        paths = [path for path in directory.glob("*.json") if path.is_file()]
    except OSError:
        return []
    paths.sort(key=_archive_sort_key, reverse=True)
    reviews: list[ArchivedReview] = []
    for path in paths:
        review = load_archived_review(path)
        if review is not None:
            reviews.append(review)
        if len(reviews) >= limit:
            break
    return reviews


def load_archived_review(path: Path) -> ArchivedReview | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    repository_path = _payload_string(payload, "path")
    branch = _payload_string(payload, "branch")
    review_message = _payload_string(payload, "review_message")
    if review_message is None:
        return None
    return ArchivedReview(
        archive_path=path,
        repository_path=repository_path or "unknown-path",
        branch=branch or "unknown-branch",
        review_message=review_message,
    )


def _archive_sort_key(path: Path) -> tuple[int, str]:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return mtime, path.name


def _payload_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _review_filename() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid.uuid4().hex}.json"
