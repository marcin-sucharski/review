from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .diff_model import ReviewFile, ReviewLine, ReviewSource, create_review_file
from .errors import GitCommandError, NoChangesFound, NotAGitRepository


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
INDEX_REF = "INDEX"
MERGED_UNCOMMITTED_METADATA = "Includes staged and unstaged changes"


@dataclass(frozen=True)
class NameStatus:
    status: str
    path: str
    old_path: str | None = None


def run_git(root: Path, args: list[str], *, binary: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    command = ["git", *args]
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=not binary, check=False)
    except FileNotFoundError as exc:
        raise GitCommandError(command, "git executable not found", None) from exc
    if check and result.returncode != 0:
        stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode("utf-8", "replace")
        raise GitCommandError(command, stderr.strip() or "Git command failed", result.returncode)
    return result


def repository_root(start: Path | str = ".") -> Path:
    start_path = Path(start)
    command = ["git", "rev-parse", "--show-toplevel"]
    try:
        result = subprocess.run(
            command,
            cwd=start_path,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitCommandError(command, "git executable not found", None) from exc
    if result.returncode != 0:
        raise NotAGitRepository("not a Git repository")
    return Path(result.stdout.strip())


def has_head(root: Path) -> bool:
    return run_git(root, ["rev-parse", "--verify", "HEAD"], check=False).returncode == 0


def default_branch_candidates(root: Path) -> list[str]:
    commit_dates = branch_commit_dates(root)
    return sorted(list_branches(root), key=lambda branch: _branch_sort_key(branch, commit_dates))


def branch_commit_dates(root: Path) -> dict[str, int]:
    result = run_git(
        root,
        ["for-each-ref", "--format=%(refname:short)\t%(committerdate:unix)", "refs/heads", "refs/remotes"],
        check=False,
    )
    if result.returncode != 0:
        return {}
    dates: dict[str, int] = {}
    for line in result.stdout.splitlines():
        name, _, timestamp = line.partition("\t")
        name = name.strip()
        if not name or name.endswith("/HEAD") or name == "HEAD":
            continue
        try:
            dates[name] = int(timestamp.strip())
        except ValueError:
            dates[name] = 0
    return dates


def _branch_sort_key(branch: str, commit_dates: dict[str, int]) -> tuple[int, int, str]:
    return _common_branch_priority(branch), -commit_dates.get(branch, 0), branch


def list_branches(root: Path) -> list[str]:
    branches: set[str] = set()
    for args in (["branch", "--format=%(refname:short)"], ["branch", "-r", "--format=%(refname:short)"]):
        result = run_git(root, args, check=False)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                branch = line.strip()
                if branch and not branch.endswith("/HEAD") and branch != "HEAD":
                    branches.add(branch)
    return sorted(branches)


def _common_branch_priority(branch: str) -> int:
    if branch == "main":
        return 0
    if branch == "master":
        return 1
    if branch.endswith("/main"):
        return 2
    if branch.endswith("/master"):
        return 3
    return 4


def current_branch(root: Path) -> str:
    branch = run_git(root, ["branch", "--show-current"], check=False)
    if branch.returncode == 0 and branch.stdout.strip():
        return branch.stdout.strip()
    commit = run_git(root, ["rev-parse", "--short", "HEAD"], check=False)
    if commit.returncode == 0 and commit.stdout.strip():
        return f"detached:{commit.stdout.strip()}"
    return "unknown"


def collect_uncommitted(root: Path) -> tuple[ReviewSource, list[ReviewFile]]:
    base = "HEAD" if has_head(root) else EMPTY_TREE
    staged_entries = _name_status(root, ["--cached", base, "--"])
    unstaged_entries = _name_status(root, ["--"])
    untracked_paths = _untracked_paths(root)
    files = _collect_uncommitted_files(root, base, staged_entries, unstaged_entries, untracked_paths)
    if not files:
        raise NoChangesFound("no uncommitted changes found")
    return ReviewSource("uncommitted", base_ref=base), files


def _collect_uncommitted_files(
    root: Path,
    base: str,
    staged_entries: list[NameStatus],
    unstaged_entries: list[NameStatus],
    untracked_paths: list[str],
) -> list[ReviewFile]:
    untracked_path_set = set(untracked_paths)
    consumed_untracked_paths: set[str] = set()
    staged_by_path = {entry.path: entry for entry in staged_entries}
    consumed_staged_paths: set[str] = set()
    seen: set[str] = set()
    files: list[ReviewFile | None] = []

    for entry in unstaged_entries:
        files.append(_build_unstaged_file(root, base, entry, staged_by_path, consumed_staged_paths))
        seen.add(entry.path)

    for entry in staged_entries:
        if entry.path not in consumed_staged_paths:
            files.append(_build_staged_file(root, base, entry, untracked_path_set, consumed_untracked_paths))
            seen.add(entry.path)

    for path in untracked_paths:
        if path not in seen and path not in consumed_untracked_paths:
            files.append(_build_untracked_file(root, path))

    return _present_files(files)


def _build_unstaged_file(
    root: Path,
    base: str,
    entry: NameStatus,
    staged_by_path: dict[str, NameStatus],
    consumed_staged_paths: set[str],
) -> ReviewFile | None:
    staged_entry = staged_by_path.get(entry.path)
    if staged_entry is not None:
        consumed_staged_paths.add(staged_entry.path)
        return _build_merged_uncommitted_file(root, staged_entry, entry, base)
    return _build_file_from_refs(root, entry, INDEX_REF, None)


def _build_staged_file(
    root: Path,
    base: str,
    entry: NameStatus,
    untracked_path_set: set[str],
    consumed_untracked_paths: set[str],
) -> ReviewFile | None:
    if _is_staged_delete_recreated_in_worktree(entry, untracked_path_set):
        consumed_untracked_paths.add(entry.path)
        return _build_merged_uncommitted_file(root, entry, NameStatus("A", entry.path), base)
    return _build_file_from_refs(root, entry, base, INDEX_REF)


def _is_staged_delete_recreated_in_worktree(entry: NameStatus, untracked_path_set: set[str]) -> bool:
    return entry.status.startswith("D") and entry.path in untracked_path_set


def _present_files(files: list[ReviewFile | None]) -> list[ReviewFile]:
    return [file for file in files if file is not None]


def collect_branch_comparison(root: Path, target_branch: str) -> tuple[ReviewSource, list[ReviewFile]]:
    merge_base = run_git(root, ["merge-base", "HEAD", target_branch]).stdout.strip()
    entries = _name_status(root, [merge_base, "HEAD", "--"])
    files = [_build_file_from_refs(root, entry, merge_base, "HEAD") for entry in entries]
    files = _present_files(files)
    if not files:
        raise NoChangesFound(f"no changes found against {target_branch}")
    return ReviewSource("branch", target_branch=target_branch, base_ref=merge_base), files


def _name_status(root: Path, refs_and_pathspec: list[str]) -> list[NameStatus]:
    result = run_git(
        root,
        ["diff", "--name-status", "-z", "--find-renames=20%", "--find-copies=20%", *refs_and_pathspec],
        binary=True,
    )
    return parse_name_status_z(result.stdout)


def parse_name_status_z(output: bytes) -> list[NameStatus]:
    if not output:
        return []
    tokens = output.decode("utf-8", "surrogateescape").split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    entries: list[NameStatus] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if not status:
            continue
        code = status[0]
        if code in {"R", "C"}:
            if index + 1 >= len(tokens):
                break
            old_path = tokens[index]
            new_path = tokens[index + 1]
            index += 2
            entries.append(NameStatus(code, new_path, old_path))
        else:
            if index >= len(tokens):
                break
            path = tokens[index]
            index += 1
            entries.append(NameStatus(code, path))
    return entries


def _untracked_paths(root: Path) -> list[str]:
    result = run_git(root, ["ls-files", "--others", "--exclude-standard", "-z"], binary=True, check=False)
    if result.returncode != 0 or not result.stdout:
        return []
    tokens = result.stdout.decode("utf-8", "surrogateescape").split("\0")
    return [token for token in tokens if token]


def _build_file_from_refs(root: Path, entry: NameStatus, old_ref: str, new_ref: str | None) -> ReviewFile | None:
    status = _status_name(entry.status)
    old_path = entry.old_path or entry.path
    new_path = entry.path
    old_bytes, new_bytes = _read_change_bytes(root, entry, old_ref, new_ref, old_path, new_path)
    metadata = _metadata_for(entry)

    if _is_binary(old_bytes) or _is_binary(new_bytes):
        return _create_review_file_from_bytes(new_path, status, old_bytes, new_bytes, old_path=entry.old_path, binary=True, metadata=metadata)
    if old_bytes == new_bytes:
        metadata_only = _metadata_only_file(new_path, status, entry, metadata)
        if metadata_only is not None:
            return metadata_only
    return _create_review_file_from_bytes(new_path, status, old_bytes, new_bytes, old_path=entry.old_path, metadata=metadata)


def _read_change_bytes(
    root: Path,
    entry: NameStatus,
    old_ref: str,
    new_ref: str | None,
    old_path: str,
    new_path: str,
) -> tuple[bytes, bytes]:
    old_bytes = b"" if entry.status == "A" else _read_tree_or_index(root, old_ref, old_path)
    if entry.status == "D":
        new_bytes = b""
    elif new_ref == INDEX_REF:
        new_bytes = _read_index(root, new_path)
    elif new_ref is None:
        new_bytes = _read_worktree(root, new_path)
    else:
        new_bytes = _read_ref(root, new_ref, new_path)
    return old_bytes or b"", new_bytes or b""


def _metadata_only_file(
    path: str,
    status: str,
    entry: NameStatus,
    metadata: list[str],
) -> ReviewFile | None:
    if status in {"renamed", "copied"}:
        action = "Renamed" if status == "renamed" else "Copied"
        return create_review_file(
            path,
            status,
            [],
            [],
            old_path=entry.old_path,
            metadata=[*metadata, f"{action} without content changes"],
        )
    if entry.status.startswith("M"):
        return create_review_file(path, "mode", [], [], old_path=entry.old_path, metadata=["Mode changed"])
    return None


def _create_review_file_from_bytes(
    path: str,
    status: str,
    old_bytes: bytes,
    new_bytes: bytes,
    *,
    old_path: str | None = None,
    binary: bool = False,
    metadata: list[str] | None = None,
) -> ReviewFile:
    if binary:
        return create_review_file(path, status, [], [], old_path=old_path, binary=True, metadata=metadata)
    return create_review_file(
        path,
        status,
        _decode_lines(old_bytes),
        _decode_lines(new_bytes),
        old_path=old_path,
        binary=binary,
        metadata=metadata,
    )


def _build_merged_uncommitted_file(root: Path, cached_entry: NameStatus, worktree_entry: NameStatus, base: str) -> ReviewFile | None:
    cached = _build_file_from_refs(root, cached_entry, base, INDEX_REF)
    worktree = _build_file_from_refs(root, worktree_entry, INDEX_REF, None)
    if cached is None:
        return worktree
    if worktree is None:
        return cached
    if cached.binary or worktree.binary:
        worktree.metadata = _merged_metadata(cached, worktree, MERGED_UNCOMMITTED_METADATA)
        return worktree
    unified_entry = _merged_uncommitted_entry(cached_entry, worktree_entry, cached, worktree)
    unified = _build_file_from_refs(root, unified_entry, base, None)
    lines = _merge_uncommitted_lines(unified.lines if unified else [], cached.lines, worktree.lines)
    return ReviewFile(
        path=worktree.path,
        old_path=cached.old_path or worktree.old_path,
        status=unified.status if unified is not None else _merged_uncommitted_status(cached, worktree),
        language=worktree.language,
        lines=lines,
        binary=False,
        metadata=_merged_metadata(cached, worktree, MERGED_UNCOMMITTED_METADATA),
    )


def _merged_metadata(cached: ReviewFile, worktree: ReviewFile, summary: str) -> list[str]:
    return [*cached.metadata, *worktree.metadata, summary]


def _merged_uncommitted_status(cached: ReviewFile, worktree: ReviewFile) -> str:
    if cached.status in {"renamed", "copied", "added"}:
        return cached.status
    if cached.status == "deleted" and worktree.status == "added":
        return "modified"
    return worktree.status


def _merged_uncommitted_entry(
    cached_entry: NameStatus,
    worktree_entry: NameStatus,
    cached: ReviewFile,
    worktree: ReviewFile,
) -> NameStatus:
    if cached.status == "deleted" and worktree.status == "added":
        return NameStatus("M", worktree_entry.path, cached_entry.old_path)
    if cached.status in {"renamed", "copied", "added"}:
        return NameStatus(cached_entry.status, worktree_entry.path, cached_entry.old_path)
    return NameStatus(worktree_entry.status, worktree_entry.path, cached_entry.old_path or worktree_entry.old_path)


def _merge_uncommitted_lines(
    unified_lines: list[ReviewLine],
    cached_lines: list[ReviewLine],
    worktree_lines: list[ReviewLine],
) -> list[ReviewLine]:
    merged = _reindex_lines(unified_lines)
    represented = Counter(_line_signature(line) for line in merged if line.kind in {"addition", "deletion"})
    for line in [*cached_lines, *worktree_lines]:
        if line.kind not in {"addition", "deletion"} or _consume_represented(line, represented):
            continue
        _insert_line_by_position(merged, line)
    return _reindex_lines(merged)


def _consume_represented(
    line: ReviewLine,
    represented: Counter[tuple[str, str, int | None, int | None]],
) -> bool:
    signature = _line_signature(line)
    if represented[signature] > 0:
        represented[signature] -= 1
        return True
    return False


def _insert_line_by_position(lines: list[ReviewLine], line: ReviewLine) -> None:
    key = _line_position(line)
    insert_at = len(lines)
    for index, existing in enumerate(lines):
        if _line_position(existing) > key:
            insert_at = index
            break
    lines.insert(insert_at, _copy_line(line, 0))


def _line_signature(line: ReviewLine) -> tuple[str, str, int | None, int | None]:
    return line.kind, line.text, line.old_line, line.new_line


def _line_position(line: ReviewLine) -> int:
    return line.primary_line or line.old_line or line.new_line or 0


def _reindex_lines(lines: list[ReviewLine]) -> list[ReviewLine]:
    combined: list[ReviewLine] = []
    for line in lines:
        combined.append(_copy_line(line, len(combined)))
    return combined


def _copy_line(line: ReviewLine, index: int) -> ReviewLine:
    return ReviewLine(
        index=index,
        kind=line.kind,
        text=line.text,
        old_line=line.old_line,
        new_line=line.new_line,
    )


def _build_untracked_file(root: Path, path: str) -> ReviewFile | None:
    data = _read_worktree(root, path)
    if data is None:
        return None
    if _is_binary(data):
        return _create_review_file_from_bytes(path, "added", b"", data, binary=True, metadata=["Untracked binary file"])
    return _create_review_file_from_bytes(path, "added", b"", data, metadata=["Untracked file"])


def _read_ref(root: Path, ref: str, path: str) -> bytes | None:
    result = run_git(root, ["show", f"{ref}:{path}"], binary=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def _read_tree_or_index(root: Path, ref: str, path: str) -> bytes | None:
    if ref == INDEX_REF:
        return _read_index(root, path)
    return _read_ref(root, ref, path)


def _read_index(root: Path, path: str) -> bytes | None:
    result = run_git(root, ["show", f":{path}"], binary=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def _read_worktree(root: Path, path: str) -> bytes | None:
    full_path = root / path
    if not full_path.exists() or not full_path.is_file():
        return None
    return full_path.read_bytes()


def _decode_lines(data: bytes) -> list[str]:
    if not data:
        return []
    text = data.decode("utf-8", "replace")
    return text.splitlines()


def _is_binary(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:8192]
    if b"\0" in sample:
        return True
    control = sum(1 for byte in sample if byte < 9 or (13 < byte < 32))
    return control > max(8, len(sample) // 20)


def _status_name(status: str):
    code = status[0]
    return {
        "M": "modified",
        "A": "added",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "T": "type",
    }.get(code, "modified")


def _metadata_for(entry: NameStatus) -> list[str]:
    if entry.status.startswith("R") and entry.old_path:
        return [f"Renamed from {entry.old_path} to {entry.path}"]
    if entry.status.startswith("C") and entry.old_path:
        return [f"Copied from {entry.old_path} to {entry.path}"]
    return []
