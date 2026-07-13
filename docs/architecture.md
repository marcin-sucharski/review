# Architecture

## Design principles

The executable is organized around a small, testable domain core. Git collection, review state, formatting, persistence, and tmux delivery do not depend on terminal rendering. The TUI translates terminal events into explicit state mutations and renders a continuous review document.

The implementation is safe Rust (`unsafe_code = "forbid"`) and has no runtime language environment. External processes are limited to `git` for repository data and `tmux` for optional pane discovery and delivery.

## Package layout

```text
src/
  main.rs        process entry point
  lib.rs         public modules and version
  cli.rs         argument parsing and workflow orchestration
  error.rs       typed, user-facing failures
  git.rs         repository inspection and final-worktree collection
  model.rs       immutable file/line/comment domain records and diffing
  state.rs       mutable review session state
  tui.rs         alternate-screen renderer and event handling
  menu.rs        compact source, branch, delivery, and history menus
  syntax.rs      language detection and syntax highlighting
  file_tree.rs   collapsed modified-file tree
  format.rs      Markdown and XML feedback serialization
  archive.rs     atomic XDG archive and local-file delivery
  tmux.rs        pane discovery and buffered delivery
tests/
  rust_git_integration.rs
  rust_cli_integration.rs
docs/
```

## Module boundaries

`cli` owns startup, history commands, source selection, workflow sequencing, and friendly process exit behavior. Its argument parser is intentionally small because the public command surface has only a few options.

`git` executes Git with argument arrays, validates exit status, and turns repository state into `ReviewFile` records. It never parses human-formatted Git output. Path lists use NUL-delimited formats so whitespace and non-ASCII names remain safe.

`model` defines sources, statuses, files, lines, expansion ranges, and comments. It builds a patience diff from old and new file contents and preserves both old-side and new-side line numbers.

`state` owns selection, visible context, saved comments, editing and deletion, and the ordered document items consumed by the renderer. These transitions are unit-testable without a terminal.

`tui` uses crossterm directly. It owns the alternate screen, raw mode, terminal cleanup guard, keyboard and mouse decoding, layout, wrapping, sticky headers, search, command mode, and the inline comment editor. Syntax is highlighted lazily for visible rows and cached by file and line.

`menu` renders small inline menus without entering the full-screen TUI. Prompts are written to stderr so redirected review output remains clean.

`syntax` maps paths to output fence languages and selects an explicitly enabled tree-sitter grammar from Arborium. It covers all representative formats in the acceptance matrix, plus native Rust and Cargo TOML files, and falls back safely to plain text.

`format` is the single serializer for review feedback. It dynamically chooses safe Markdown fences, distinguishes old-side deleted-line references, escapes XML text, and splits embedded CDATA terminators.

`archive` writes completed reviews atomically beneath the XDG data directory and reads valid recent records defensively. It also creates timestamped Markdown delivery files.

`tmux` discovers panes from machine-readable fields. Delivery loads the full review into a named tmux buffer, pastes it literally, and then sends Enter, avoiding shell interpolation and command-length limits.

## Data flow

1. The CLI finds the repository root and resolves the requested review source.
2. The Git adapter reads the comparison base and final working-tree files.
3. The model constructs ordered line records and initial context windows.
4. Review state exposes one continuous document of headers, code, expansion rows, and inline comments.
5. The TUI renders the current viewport and applies navigation, selection, comment, search, and mouse events to state.
6. On `:q`, the formatter produces Markdown or XML from saved comments.
7. A non-empty review is archived before delivery.
8. The CLI writes stdout, creates a Markdown file, or sends the selected output to tmux.

## Git comparison model

Uncommitted review compares `HEAD` with the final working tree. Staged and unstaged changes are therefore unified, and untracked files are added explicitly.

Branch review finds `merge-base(target, HEAD)` and compares that base with the final working tree. The result contains committed branch work plus current staged, unstaged, and untracked changes exactly once.

For each path, collection reads the old blob from the base commit and the new bytes from the worktree. Deleted files have no new bytes. Symlinks are read as link targets without dereferencing. Binary or control-heavy files receive metadata records instead of terminal byte output. Rename/copy status and executable-mode changes are preserved.

## Domain records

`ReviewSource` identifies uncommitted or branch comparison, including the selected target and merge base.

`ReviewFile` contains current and previous paths, status, language, optional mode information, line records, binary metadata, and mutable visible context intervals.

`ReviewLine` contains its diff kind, text, stable row identity, and optional old/new line numbers. A selected range cannot cross a file boundary.

`ReviewComment` contains a stable in-session ID, file, old/new line reference, selected source lines, and body. Deleted-line comments use old-side references in both output formats.

`ReviewState` contains repository/source metadata, ordered files, current focus and selection, expansion state, and comments. TUI-only viewport and editor state stays in `ReviewApp`.

## Rendering and performance

The default layout devotes the full terminal to the review. `T` reveals a bounded navigation column split between a collapsed file tree and grouped comment list.

The review pane uses physical rendered-row heights for scrolling and page movement, so wrapped source, inline comments, and expansion controls stay aligned. The current file header is sticky when its original header scrolls above the viewport.

The TUI builds a retained frame of styled row regions and compares it with the last presented frame. Identical frames emit no bytes, while changed frames overwrite only changed rows. Updates are synchronized when the terminal supports synchronized-output mode, and no redraw path clears the screen before repainting. Inline menus use the same principles at physical-row granularity, retain their rendered width across resize events, and overwrite complete changed rows without clear-screen commands.

Files with at most 180 lines are initially shown in full. Larger files show merged 20-line context windows around changes. Expansion controls reveal another 20 lines per activation. Syntax highlighting is computed only for visible source rows and retained in an in-session cache; startup does not tokenize every changed file.

The intended normal operating envelope is up to 200 changed files and 20,000 expanded lines. Binary detection, bounded initial context, collapsed paths, literal tmux buffers, and lazy highlighting keep memory and redraw costs predictable.

## Terminal safety

A guard restores mouse reporting, bracketed paste, raw mode, cursor visibility, and the alternate screen on every normal error path. `Ctrl+C` in the initial source menu exits immediately. After source selection, the first interrupt shows a warning and the second consecutive interrupt exits; any other action clears the warning.

Terminal dimensions, mouse coordinates, filenames, source text, comments, and pane labels are treated as untrusted values. Width arithmetic is saturating, Unicode display width is measured explicitly, far-right SGR mouse positions are mapped without narrowing overflow, and literal terminal control characters are rendered or delivered as visible safe glyphs.

## Dependencies

The direct dependency set is deliberately small:

| Crate | Purpose |
| --- | --- |
| `crossterm` | portable terminal input, raw mode, mouse events, and drawing primitives |
| `serde`, `serde_json` | stable archive schema and JSON persistence |
| `similar` | patience diff implementation |
| `arborium` | maintained tree-sitter grammars and syntax spans, feature-limited to supported review languages |
| `unicode-width` | correct terminal cell measurement |

Feature flags exclude all unused grammars and rendering facilities. No asynchronous runtime, general CLI framework, TUI widget framework, time library, temporary-file library, or random-number library is required.

## Testing seams

Unit tests cover diff construction, expansion windows, state transitions, safe formatting, archive uniqueness, menu ordering, path/language detection, editor wrapping, mouse coordinate arithmetic, and tmux parsing.

Integration tests create real temporary Git repositories and exercise combined worktree state, branch comparisons, untracked files, rename/delete/binary/mode changes, symlinks, CLI errors, no-change behavior, and history output separation.

The full acceptance pass additionally drives the actual executable in isolated real tmux servers. This covers terminal rendering, keyboard and mouse decoding, delivery, cleanup, and interaction sequences that cannot be proven by pure state tests.

## Extension points

Additional colon commands belong in the TUI command dispatcher. New delivery mechanisms consume the already formatted message. New output formats implement serialization over `ReviewState`; they do not inspect terminal state. Direct hosting-provider publication, draft persistence, file editing, and side-by-side rendering remain out of scope for the current release.
