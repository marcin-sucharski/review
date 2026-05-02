from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .diff_model import ReviewFile, ReviewLine, ReviewSource, create_review_file
from .errors import GitCommandError, NoChangesFound, NotAGitRepository


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


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
    candidates: list[str] = []
    upstream = run_git(root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], check=False)
    if upstream.returncode == 0 and upstream.stdout.strip():
        candidates.append(upstream.stdout.strip())
    origin_head = run_git(root, ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], check=False)
    if origin_head.returncode == 0 and origin_head.stdout.strip():
        candidates.append(origin_head.stdout.strip())
    candidates.extend(["origin/main", "main", "origin/master", "master"])
    available = set(list_branches(root))
    ordered: list[str] = []
    for candidate in candidates:
        if candidate in available and candidate not in ordered:
            ordered.append(candidate)
    for branch in sorted(available):
        if branch not in ordered:
            ordered.append(branch)
    return ordered


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


def collect_uncommitted(root: Path) -> tuple[ReviewSource, list[ReviewFile]]:
    base = "HEAD" if has_head(root) else EMPTY_TREE
    staged_entries = _name_status(root, ["--cached", base, "--"])
    unstaged_entries = _name_status(root, ["--"])
    untracked_paths = _untracked_paths(root)
    untracked_path_set = set(untracked_paths)
    consumed_untracked_paths: set[str] = set()
    staged_by_path = {entry.path: entry for entry in staged_entries}
    consumed_staged_paths: set[str] = set()
    seen: set[str] = set()
    files: list[ReviewFile | None] = []
    for entry in unstaged_entries:
        staged_entry = staged_by_path.get(entry.path)
        if staged_entry is not None:
            files.append(_build_merged_uncommitted_file(root, staged_entry, entry, base))
            consumed_staged_paths.add(staged_entry.path)
        else:
            files.append(_build_file_from_refs(root, entry, "INDEX", None))
        seen.add(entry.path)
    for entry in staged_entries:
        if entry.path not in consumed_staged_paths:
            if entry.status.startswith("D") and entry.path in untracked_path_set:
                files.append(_build_merged_uncommitted_file(root, entry, NameStatus("A", entry.path), base))
                consumed_untracked_paths.add(entry.path)
            else:
                files.append(_build_file_from_refs(root, entry, base, "INDEX"))
            seen.add(entry.path)
    for path in untracked_paths:
        if path not in seen and path not in consumed_untracked_paths:
            files.append(_build_untracked_file(root, path))
    files = [file for file in files if file is not None]
    if not files:
        raise NoChangesFound("no uncommitted changes found")
    return ReviewSource("uncommitted", base_ref=base), files


def collect_branch_comparison(root: Path, target_branch: str) -> tuple[ReviewSource, list[ReviewFile]]:
    merge_base = run_git(root, ["merge-base", "HEAD", target_branch]).stdout.strip()
    entries = _name_status(root, [merge_base, "HEAD", "--"])
    files = [_build_file_from_refs(root, entry, merge_base, "HEAD") for entry in entries]
    files = [file for file in files if file is not None]
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

    old_bytes = b"" if entry.status == "A" else _read_tree_or_index(root, old_ref, old_path)
    if entry.status == "D":
        new_bytes = b""
    elif new_ref == "INDEX":
        new_bytes = _read_index(root, new_path)
    elif new_ref is None:
        new_bytes = _read_worktree(root, new_path)
    else:
        new_bytes = _read_ref(root, new_ref, new_path)

    old_bytes = old_bytes or b""
    new_bytes = new_bytes or b""
    binary = _is_binary(old_bytes) or _is_binary(new_bytes)
    metadata = _metadata_for(entry)
    if binary:
        return create_review_file(new_path, status, [], [], old_path=entry.old_path, binary=True, metadata=metadata)
    if old_bytes == new_bytes:
        if status in {"renamed", "copied"}:
            action = "Renamed" if status == "renamed" else "Copied"
            return create_review_file(
                new_path,
                status,
                [],
                [],
                old_path=entry.old_path,
                metadata=[*metadata, f"{action} without content changes"],
            )
        if entry.status.startswith("M"):
            return create_review_file(new_path, "mode", [], [], old_path=entry.old_path, metadata=["Mode changed"])
    return create_review_file(
        new_path,
        status,
        _decode_lines(old_bytes),
        _decode_lines(new_bytes),
        old_path=entry.old_path,
        metadata=metadata,
    )


def _build_merged_uncommitted_file(root: Path, cached_entry: NameStatus, worktree_entry: NameStatus, base: str) -> ReviewFile | None:
    cached = _build_file_from_refs(root, cached_entry, base, "INDEX")
    worktree = _build_file_from_refs(root, worktree_entry, "INDEX", None)
    if cached is None:
        return worktree
    if worktree is None:
        return cached
    if cached.binary or worktree.binary:
        worktree.metadata = [*cached.metadata, *worktree.metadata, "Includes staged and worktree changes"]
        return worktree
    combined_metadata = [
        *cached.metadata,
        *worktree.metadata,
        "Contains separate staged and worktree changes",
    ]
    if cached.status in {"renamed", "copied", "added"}:
        status = cached.status
    elif cached.status == "deleted" and worktree.status == "added":
        status = "modified"
    else:
        status = worktree.status
    old_path = cached.old_path or worktree.old_path
    return ReviewFile(
        path=worktree.path,
        old_path=old_path,
        status=status,
        language=worktree.language,
        lines=_combine_line_sections(("Staged changes", cached.lines), ("Worktree changes", worktree.lines)),
        binary=False,
        metadata=combined_metadata,
    )


def _combine_line_sections(*sections: tuple[str, list[ReviewLine]]) -> list[ReviewLine]:
    combined: list[ReviewLine] = []
    for title, lines in sections:
        if not lines:
            continue
        combined.append(ReviewLine(len(combined), "metadata", f"-- {title} --"))
        for line in lines:
            combined.append(
                ReviewLine(
                    index=len(combined),
                    kind=line.kind,
                    text=line.text,
                    old_line=line.old_line,
                    new_line=line.new_line,
                )
            )
    return combined


def _build_untracked_file(root: Path, path: str) -> ReviewFile | None:
    data = _read_worktree(root, path)
    if data is None:
        return None
    binary = _is_binary(data)
    if binary:
        return create_review_file(path, "added", [], [], binary=True, metadata=["Untracked binary file"])
    return create_review_file(path, "added", [], _decode_lines(data), metadata=["Untracked file"])


def _read_ref(root: Path, ref: str, path: str) -> bytes | None:
    result = run_git(root, ["show", f"{ref}:{path}"], binary=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def _read_tree_or_index(root: Path, ref: str, path: str) -> bytes | None:
    if ref == "INDEX":
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
