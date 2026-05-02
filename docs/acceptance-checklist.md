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
- [ ] Branch comparison allows selecting a target branch.
- [ ] Branch target selection supports arrow-key navigation and Enter.
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

- [ ] Two vertical panes are visible.
- [ ] File pane lists changed files.
- [ ] File pane renders changed files as a collapsed directory tree.
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
- [ ] Unknown files fall back to plain text.

## Navigation

- [ ] `Tab` switches panes.
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

## Selection And Comments

- [ ] Single-line selection is visible.
- [ ] `Shift+Up` and `Shift+Down` create multi-line selections.
- [ ] Selection does not cross file boundaries.
- [ ] `Enter` opens inline comment input for selected code.
- [ ] Single-line comments save correctly.
- [ ] Multi-line comments save correctly.
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
- [ ] Available tmux panes are listed with pane ID and title.
- [ ] Current pane is marked when detectable.
- [ ] No-pane/stdout option is always available.
- [ ] Stdout delivery prints the formatted review.
- [ ] Tmux delivery sends the same formatted review.
- [ ] Tmux delivery presses Enter.
- [ ] Tmux failures do not lose comments.

## Output Format

- [ ] Output includes review source.
- [ ] Branch output includes target branch.
- [ ] Comments are grouped by file.
- [ ] Comments are sorted by file and line.
- [ ] Each comment includes line range.
- [ ] Each comment includes selected context lines.
- [ ] Each comment includes comment body.
- [ ] Deleted-line comments are labeled as old-side lines.
- [ ] Markdown fences handle code containing backticks.

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
- [ ] Manual narrow-terminal check completed.
- [ ] Manual large-file expansion check completed.
