# Acceptance Checklist

Use this checklist to decide whether the initial implementation is complete.

## Documentation

- [ ] Product requirements are reflected in implementation.
- [ ] Architecture matches the final module boundaries or docs are updated.
- [ ] TUI behavior is documented and implemented.
- [ ] Testing scenarios are represented in automated and manual tests.
- [ ] Known limitations are documented.

## CLI

- [ ] `review` executable is available.
- [ ] Running outside Git shows a clear error.
- [ ] Running with no changes exits cleanly.
- [ ] Startup prompts for uncommitted or branch comparison review.
- [ ] Startup source selection supports arrow-key navigation and Enter.
- [ ] Startup and delivery menus are compact inline menus and remain readable in light terminals.
- [ ] Startup source menu exits on a single `Ctrl+C`.
- [ ] Branch comparison allows selecting a target branch.
- [ ] Branch target selection supports arrow-key navigation and Enter.
- [ ] Branch target selection requires double `Ctrl+C` to cancel.
- [ ] Canceling prompts exits cleanly.

## Git

- [ ] Unstaged changes are detected.
- [ ] Staged changes are detected.
- [ ] Staged and unstaged changes in the same file are handled.
- [ ] Untracked text files are included or documented otherwise.
- [ ] Added files render correctly.
- [ ] Modified files render correctly.
- [ ] Deleted files render correctly.
- [ ] Renamed files render correctly.
- [ ] Binary files do not crash the UI.
- [ ] Branch comparison uses merge-base PR-style semantics.
- [ ] Broad context is visible by default.

## TUI Layout

- [ ] Review pane is visible and focused by default.
- [ ] File pane is hidden by default.
- [ ] File pane can be shown with `T`.
- [ ] File pane lists changed files.
- [ ] File pane renders changed files as a collapsed directory tree.
- [ ] Left pane includes a comment list below the file tree.
- [ ] Comment list groups comments by file.
- [ ] Comment list entries include line number or range and a shortened comment preview.
- [ ] Selecting a comment list entry focuses the inline comment in the review pane.
- [ ] Review pane shows one continuous document for all files.
- [ ] Selecting a file scrolls the review pane.
- [ ] Scrolling review pane updates highlighted file.
- [ ] Sticky file header appears for long file sections.
- [ ] Line numbers are visible by default.
- [ ] Lines wrap by default.
- [ ] Changed lines are visually distinct.

## Syntax Highlighting

- [ ] Java highlighted.
- [ ] JavaScript highlighted.
- [ ] TypeScript highlighted.
- [ ] CSS highlighted.
- [ ] HTML highlighted.
- [ ] JSX highlighted.
- [ ] SQL highlighted.
- [ ] XML highlighted.
- [ ] JSON highlighted.
- [ ] Properties highlighted or reasonably styled.
- [ ] YAML highlighted.
- [ ] Python highlighted.
- [ ] Markdown highlighted.
- [ ] Nix highlighted.
- [ ] Gitignore-style files highlighted.
- [ ] Lock files with JSON content highlighted.
- [ ] Unknown files fall back to plain text.

## Navigation

- [ ] `Tab` switches panes.
- [ ] `Tab` cycles through review pane, file tree, and comment list when the left pane is visible.
- [ ] `T` shows and hides the file pane.
- [ ] File pane arrow navigation works.
- [ ] File pane arrow navigation follows rendered tree file order.
- [ ] File pane `Enter` scrolls to selected file.
- [ ] Review pane arrow navigation works.
- [ ] Review pane page navigation works.
- [ ] Review pane arrow navigation behaves like an editor and scrolls only when the selection reaches the viewport edge.
- [ ] Review pane page navigation preserves the selected line's screen offset when possible.
- [ ] Mouse click focuses panes.
- [ ] Mouse click selects files and lines.
- [ ] Mouse wheel scrolling works where supported.
- [ ] First `Ctrl+C` in the TUI shows confirmation instead of quitting.
- [ ] Second consecutive `Ctrl+C` in the TUI quits.

## Selection And Comments

- [ ] Single-line selection is visible.
- [ ] `Shift+Up` and `Shift+Down` create multi-line selections.
- [ ] Selection does not cross file boundaries.
- [ ] `Enter` opens inline comment input for selected code.
- [ ] Single-line comments save correctly.
- [ ] Multi-line comments save correctly.
- [ ] `Ctrl+J` inserts comment newlines immediately without waiting for another character.
- [ ] Saved comments render inline.
- [ ] Comment range marker is visible between line numbers and change markers.
- [ ] Inline saved comments render only the comment body.
- [ ] Comments remain attached after scrolling and expansion.

## Expansion

- [ ] Expand-up rows appear when hidden context exists above.
- [ ] Expand-down rows appear when hidden context exists below.
- [ ] `Enter` on expand-up reveals up to 20 lines above.
- [ ] `Enter` on expand-down reveals up to 20 lines below.
- [ ] Exhausted expansion rows disappear.
- [ ] Expansion preserves selection and comments where possible.

## Command Mode

- [ ] `:` opens command mode.
- [ ] `q` quits.
- [ ] `quit` quits.
- [ ] Unknown commands show a clear error.
- [ ] Command dispatch is extensible.

## Delivery

- [ ] Quit flow prompts for delivery target when comments exist.
- [ ] Delivery target selection supports arrow-key navigation and Enter.
- [ ] Delivery target selection requires double `Ctrl+C` to cancel.
- [ ] Available tmux panes are listed with pane ID and title.
- [ ] Current pane is marked when detectable.
- [ ] No-pane/stdout option is always available.
- [ ] Stdout delivery prints the formatted review.
- [ ] Tmux delivery sends the same formatted review.
- [ ] Tmux delivery presses Enter.
- [ ] `--output-format` / `-o` supports `xml` and `md`, defaulting to `xml`.
- [ ] The selected output format is used for stdout, tmux delivery, and archive JSON.
- [ ] Tmux failures do not lose comments.
- [ ] Every non-empty review is archived before delivery.
- [ ] Empty reviews are not archived.

## Review Archive

- [ ] Archive files are written under `$XDG_DATA_HOME/review/reviews` when `XDG_DATA_HOME` is set.
- [ ] Archive files fall back to `~/.local/share/review/reviews` when `XDG_DATA_HOME` is unset.
- [ ] Archive JSON includes `path`.
- [ ] Archive JSON includes current `branch` or detached-head label.
- [ ] Archive JSON includes exact `review_message`.
- [ ] Each non-empty review creates a separate JSON file.

## Output Format

- [ ] Default non-empty output is XML-structured under `review_feedback`.
- [ ] Default non-empty output parses as XML.
- [ ] Markdown output is available with `--output-format md` and `-o md`.
- [ ] Markdown output uses stable headings and safe fenced blocks.
- [ ] Output includes review source.
- [ ] Branch output includes target branch.
- [ ] Comments are grouped by file.
- [ ] Comments are sorted by file and line.
- [ ] Each comment includes line range.
- [ ] Each comment includes selected context lines.
- [ ] Each comment includes comment body.
- [ ] Deleted-line comments are labeled as old-side lines.
- [ ] XML metacharacters in code and comments are escaped.
- [ ] Markdown fences in code and comments are preserved as text.

## Automated Tests

- [ ] Unit tests pass.
- [ ] Git integration tests pass.
- [ ] TUI interaction tests pass or documented limitations exist.
- [ ] Formatter tests pass.
- [ ] Tmux unit tests pass.
- [ ] Real tmux tests pass when environment supports them.

## Manual Verification

- [ ] Manual uncommitted review completed.
- [ ] Manual branch review completed.
- [ ] Manual stdout delivery completed.
- [ ] Manual tmux delivery completed.
- [ ] Manual archive verification completed.
- [ ] Manual narrow-terminal check completed.
- [ ] Manual large-file expansion check completed.
