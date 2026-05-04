# Product Requirements

## Goal

Build a local CLI review tool that lets a developer inspect code changes in a rich terminal UI, leave line-level comments, and send those comments as actionable feedback to a coding agent.

The tool optimizes for local agent workflows where a human reviews code produced in one tmux pane and sends concise, structured feedback back to that pane.

## Primary User

The primary user is a developer working in a terminal, usually inside tmux, reviewing uncommitted or branch-based changes made by a coding agent.

The user expects keyboard-first navigation, mouse support when convenient, syntax-highlighted diffs, clear line references, and low friction when sending comments back to the agent.

## CLI Contract

The executable command is:

```bash
review
```

The command starts an interactive review session in the current working directory.

The first prompt asks for the review source:

- `Uncommitted changes`: review unstaged, staged, and optionally untracked files in the current working tree.
- `Branch comparison`: review changes against a selected branch, in pull-request style.

The prompt must be usable from a normal terminal and from inside tmux.

## Startup Behavior

The CLI must validate that the current directory is inside a Git repository.

If not inside a Git repository, it exits with a clear error message and a non-zero exit code.

If no changes are available for the selected review source, it reports that no changes were found and exits without opening the TUI.

If Git commands fail, the CLI displays the failing operation and a concise explanation.

When interactive source, branch, or delivery choices are needed, the CLI should render a compact inline menu using only a few terminal lines. Selection uses `Up`, `Down`, and `Enter`; the menu must avoid full-screen dark-background presentation so it remains readable in light terminals.

In the initial review-source menu, one `Ctrl+C` cancels the program. After that first source choice is accepted, cancellation is intentionally harder: branch selection, delivery selection, and the TUI require two consecutive `Ctrl+C` presses before exiting.

For branch comparison, the CLI must present or accept a target branch. Branch ordering should keep common merge targets easy to reach and make recent topic branches easy to find.

When selecting a PR-style target branch interactively, the branch picker shows the current branch on the left and the selected target branch on the right, for example `feature/change -> main`. It displays at most five target branches at once. `Up` and `Down` move the highlighted target. Typing any printable character filters the branch list by substring without requiring a separate search-field focus. `Backspace` edits the filter. Common target branches such as `main` and `master` should appear first when present; remaining branches are ordered by most recent commit date.

## Review Sources

### Uncommitted Changes

The uncommitted review source includes:

- unstaged modified files,
- staged modified files,
- added files,
- deleted files,
- renamed files,
- copied files if Git reports them,
- optionally untracked files if the implementation supports producing content for them.

Staged and unstaged changes for the same path must be shown as a single unified file entry, not as separate staged and unstaged review sections.

The implementation must define and test the exact untracked-file behavior. The preferred behavior is to include untracked text files as added files.

### Branch Comparison

The branch comparison review source compares the current `HEAD` against the merge base with the selected target branch.

Preferred Git model:

```bash
git merge-base HEAD <target-branch>
git diff --find-renames --find-copies <merge-base>...HEAD
```

The UI should describe this as PR-style comparison because it mirrors the usual "changes introduced by this branch" workflow.

## TUI Layout

The TUI opens with the review pane focused and the left navigation pane hidden by default.

Pressing `T` shows or hides the left navigation pane. When the left pane is visible, the TUI has two vertical panes.

The left pane is split vertically. The upper region lists modified files as a tree with collapsed directory chains when no modified file exists between the directories. The lower region lists saved review comments grouped by file.

Each comment-list row must show a line number or line range plus a shortened preview of the comment body. Selecting a comment from this list scrolls the right review pane to the corresponding inline comment.

If either left-pane region has more rows below the visible viewport, the bottom of that region must show a compact scroll indicator with a count, for example `v 10 more below`. When scrolled down, the footer may also show how many rows are above.

The right review pane shows a continuous view of all changed files. It is not a separate per-file detail page.

Selecting a file in the file pane scrolls the review pane to that file.

Scrolling the review pane updates the highlighted file in the file pane to match the file currently visible near the top of the review pane.

The currently focused pane must be visually distinct.

## Code Rendering

The review pane must show:

- file headers,
- syntax-highlighted code,
- line numbers by default,
- wrapped lines by default,
- changed-line markers,
- comment anchors and inline comment blocks,
- expansion rows for hidden context.

The default diff context should include surrounding logical code blocks, similar to `git diff -W`. The exact implementation may use Git function context plus additional heuristics, but the visible result should be broader than minimal hunk context.

Supported syntax highlighting must include at least:

- Python,
- Java,
- JavaScript,
- TypeScript,
- CSS,
- HTML,
- JSX,
- SQL,
- XML,
- JSON,
- `.properties`,
- YAML,
- Markdown,
- Nix,
- `.gitignore`-style ignore files,
- JSON-compatible lock files such as `package-lock.json` and `flake.lock`.

Unknown file types should still display as plain text.

## Navigation

`Tab` moves focus between the review pane, file tree, and comment list when the left pane is visible. `T` toggles the left pane between visible and hidden. When the left pane is hidden, focus remains in the review pane.

In the file pane:

- `Up`/`k` and `Down`/`j` move through files,
- `Enter` focuses the selected file in the review pane,
- mouse click selects and focuses a file.

In the comment list pane:

- `Up`/`k` and `Down`/`j` move through saved comments,
- `Enter` keeps the selected comment focused in the review pane,
- `Delete` and `Backspace` delete the selected saved comment,
- mouse click selects and focuses a comment.

In the review pane:

- `Up`/`k` and `Down`/`j` move the selected row or line,
- `PageUp` and `PageDown` scroll by page,
- mouse click selects a line or expansion row,
- `Enter` on code opens a comment input,
- `Enter` on expansion rows expands hidden context.

`Shift+Up` and `Shift+Down` extend or shrink the current line selection in the review pane.

While editing a comment message, `j` and `k` are inserted as text and do not navigate.

The UI must keep the selected line visible while navigating. Review-pane arrow navigation should move like an editor: the selection moves inside the viewport first, then scrolling begins near the lower edge with roughly three rows preserved below the selection. Page navigation moves by a viewport while preserving the selected line's screen offset when possible.

## Commenting

Pressing `Enter` on a selected code line opens an inline comment input below the selected line.

If multiple lines are selected, the comment input appears below the selected range.

The comment input behaves like a GitHub-style line comment:

- it is visually attached to the referenced line or range,
- it supports multi-line text,
- `Ctrl+J` inserts a newline and the blank line appears immediately,
- `Left` and `Right` move the insertion cursor by character,
- `Up` and `Down` move the insertion cursor between comment lines while preserving the intended column,
- typed text and Backspace edit at the current insertion cursor,
- `Enter` submits the comment,
- `Esc` cancels the comment,
- the saved comment appears inline below the referenced range.

Saved comments live in memory for the duration of the session.

Comment rendering must show:

- file path,
- start line,
- end line when different from start line,
- selected context lines,
- comment body.

The delivered non-empty review message defaults to Markdown and can be changed with `--output-format` / `-o`. Supported output formats are `md` and `xml`. Markdown output must use stable headings and safe fenced blocks. XML output must use tags to clearly separate metadata, files, review comments, line ranges, context lines, and comment bodies. The selected format must be used consistently for stdout, tmux delivery, and the archived review message.

The review pane must include a short visual element to the left of each inline comment indicating the referenced section of code.

## Expansion Rows

When visible context does not include the beginning or end of a file, the review pane shows selectable expansion rows.

Expansion row examples:

- `Show 20 lines above`
- `Show 20 lines below`
- `Show remaining lines above`
- `Show remaining lines below`

When selected and activated with `Enter` or mouse click, the row expands up to 20 hidden lines in the corresponding direction.

Expansion must preserve comments, selection, scroll position as much as practical, and file synchronization.

## Quit Flow

The user exits through Vim-style command mode:

1. Press `:`.
2. Type `q`.
3. Press `Enter`.

The command parser should be extensible. Initial supported commands:

- `q`
- `quit`
- `q!`
- `quit!`
- `e`
- `edit`
- `edit-comment`
- `d`
- `delete`
- `delete-comment`

If unsent comments exist, quitting proceeds to delivery target selection.

If no comments exist, the TUI exits immediately without confirmation and the CLI prints the empty-review message when using stdout/no-TUI paths.

Inside the TUI, `Ctrl+C` is not a single-key quit. The first press shows a warning, and only a second consecutive `Ctrl+C` exits. Any other key clears the pending interrupt.

After the TUI closes, the CLI must restore the terminal to a clean prompt state before rendering delivery selection or stdout output. Stale review panes, command text, and cursor positions from the curses screen must not overlap the delivery menu or generated review message.

## Delivery

At the end of the review, the tool prompts for a delivery target:

- one of the available tmux panes,
- no pane, meaning print the review text to stdout.

Tmux pane choices must include:

- tmux pane ID,
- pane title,
- session/window/pane location,
- current command when available.

When a tmux pane is selected, the tool sends the full review message and presses Enter.

When no pane is selected, the same review message is printed to stdout and the process exits.

Every non-empty review is saved before delivery, regardless of whether the user chooses tmux or stdout. The archive is a JSON file in:

```text
$XDG_DATA_HOME/review/reviews
```

If `XDG_DATA_HOME` is unset, the fallback directory is:

```text
~/.local/share/review/reviews
```

Each archive file contains:

- `path`: repository path where the review occurred,
- `branch`: current Git branch, or a detached-head label when applicable,
- `review_message`: the exact generated review text used for tmux/stdout delivery.

## Accessibility And Usability

The UI must remain usable in common terminal sizes such as 80x24.

Text must not overlap or become unreadable in narrow terminals.

Line wrapping must be predictable, and wrapped code lines should retain their relationship to the original line number.

Keyboard operations must have visible feedback.

Mouse support must enhance navigation without being required.

## Failure Behavior

The tool must handle:

- missing Git,
- missing tmux,
- running outside tmux,
- no available tmux panes,
- terminal too small,
- binary files,
- deleted files,
- renamed files,
- large diffs,
- unsupported encodings,
- cancelled prompts,
- interrupted sessions.

Failures should be reported clearly without tracebacks unless debug mode is enabled.

If tmux delivery fails after comments have been written, the archived JSON must still exist so the review is not lost.

## Acceptance Criteria

The initial version is complete when:

- `review` starts from a Git repository and prompts for review source,
- both review sources work,
- changed files render in a review-first TUI with a toggleable file pane,
- file selection and review scrolling stay synchronized,
- syntax highlighting works for the required file types,
- keyboard and mouse navigation work,
- line and multi-line comments can be created and displayed inline,
- expansion rows reveal hidden context,
- `:q` exits into delivery target selection,
- tmux delivery sends the formatted review and Enter,
- stdout delivery prints the same formatted review,
- non-empty reviews are archived as JSON before delivery,
- automated tests cover core behavior,
- manual tmux verification has been run.
