# Git Diff Model

## Goals

The Git layer provides a structured representation of changes without leaking raw command output into the TUI.

It must support both startup modes:

- uncommitted working tree review,
- PR-style branch comparison.

It must preserve enough information to render code context, line numbers, file status, and selected review context accurately.

## Repository Detection

The CLI should determine the repository root with:

```bash
git rev-parse --show-toplevel
```

It should run all subsequent Git commands from that root.

If the command fails, the CLI exits with a user-friendly "not a Git repository" message.

## Review Source: Uncommitted Changes

The uncommitted source should include staged and unstaged changes.

Recommended command strategy:

```bash
git diff --find-renames --find-copies --function-context
git diff --cached --find-renames --find-copies --function-context
git status --porcelain=v1 -z
```

The implementation must merge staged and unstaged records carefully if the same file appears in both.

Alternative command strategies are acceptable if tests prove they correctly handle staged-only, unstaged-only, and mixed changes.

### Untracked Files

Preferred behavior: include untracked text files as added files.

For untracked files, the implementation can synthesize an added-file diff from file contents.

Large or binary untracked files should be marked as binary or skipped with a clear indication.

## Review Source: Branch Comparison

The branch comparison source compares `HEAD` against the merge base with the selected target branch.

Recommended commands:

```bash
git merge-base HEAD <target-branch>
git diff --find-renames --find-copies --function-context <merge-base>...HEAD
```

The three-dot model is preferred because it matches pull-request style comparison.

The selected target branch should be recorded in the session source metadata.

## Branch Selection

When branch comparison is selected, the CLI should offer branches from:

```bash
git branch --format='%(refname:short)'
git branch -r --format='%(refname:short)'
```

The list should remove duplicates and symbolic refs such as `origin/HEAD`.

Default branch detection order:

1. upstream branch of the current branch,
2. remote default branch from `refs/remotes/origin/HEAD`,
3. `origin/main`,
4. `main`,
5. `origin/master`,
6. `master`,
7. first available branch.

## Diff Context

The default review view should show broad context around changes.

The Git layer should request function context where supported:

```bash
git diff -W
```

or:

```bash
git diff --function-context
```

The display layer may still hide portions of large files behind expansion rows. The important behavior is that initial visible context is broader than default three-line unified diff context.

## File Statuses

The model must represent:

- modified files,
- added files,
- deleted files,
- renamed files,
- copied files,
- binary files,
- mode-only changes,
- type changes,
- conflicted files if encountered.

Unsupported statuses should be displayed as metadata rather than causing crashes.

## Line Kinds

Parsed diff lines should become explicit line kinds:

| Kind | Meaning |
| --- | --- |
| `context` | Present in old and new file |
| `addition` | Added in new file |
| `deletion` | Removed from old file |
| `metadata` | File/hunk metadata |
| `expansion` | UI row for hidden context |
| `comment` | UI row for saved comment |

Only code rows should be commentable. Metadata rows are not commentable. Expansion rows are activatable.

## Line Numbers

For each code row:

- context lines have both old and new numbers,
- additions have only new numbers,
- deletions have only old numbers.

The formatter must preserve whether a comment targets old-side or new-side lines.

## Binary Files

Binary files should appear in the file pane and review pane with metadata such as:

```text
Binary file changed: path/to/file.png
```

Binary files are not line-commentable unless future support is explicitly added.

## Deleted Files

Deleted files should render deleted lines with old-side line numbers.

Comments may be allowed on deleted lines. If allowed, output must label the range as old-side lines.

If deleted-line comments are not supported in the first implementation, the UI must make deleted rows non-commentable and tests must cover this.

Preferred behavior: support comments on deleted lines.

## Renamed Files

Renamed files should show both old and new paths in the file header.

If content changed, render the diff normally.

If only renamed, show a metadata-only section and allow a file-level comment only if file-level comments are implemented. The initial line-comment workflow may simply display the rename with no commentable lines.

## Expansion Model

The parser should know the full file content when possible so expansion rows can reveal hidden context.

For uncommitted changes, full file content can come from the working tree and old content from Git object lookups.

For branch comparison, full new content can come from `HEAD:<path>`, and old content can come from `<merge-base>:<path>`.

Expansion rows should reveal unchanged context lines around changed regions without requiring a new Git diff command.

If full content cannot be loaded for a file, expansion rows for that file may be omitted and the reason should be test-covered.

## Ordering

File ordering should follow Git diff output by default.

Within each file, displayed code lines follow file order.

Comments in final output should follow the same file order and line order as the review pane.

## Encoding

Text files should be decoded as UTF-8 by default.

If decoding fails:

- try the locale-preferred encoding if reasonable,
- otherwise mark the file as binary or undecodable.

The UI should not crash on invalid byte sequences.

## Whitespace

The initial implementation should display whitespace exactly as present, except for normal terminal rendering constraints.

Future options may include ignoring whitespace changes or showing invisible characters.

## Large Files

Large files should not freeze the UI.

Recommended safeguards:

- cap initial visible context per file,
- mark very large generated files,
- avoid syntax highlighting extremely long lines if it hurts responsiveness,
- allow expansion in chunks.

The exact thresholds should be documented after implementation.
