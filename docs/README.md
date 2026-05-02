# Review CLI Documentation

`review` is a Python terminal review tool for inspecting Git changes, writing line-level review comments, and sending the collected feedback to a coding agent through tmux or standard output.

This directory is the implementation contract for the project. It intentionally comes before the code so the behavior, interfaces, testing obligations, and edge cases are explicit.

## Initial Instructions

The project must implement a CLI named `review`.

On startup, the CLI asks whether the user wants to review:

- uncommitted changes in the current Git working tree, or
- changes in pull-request style against a selected branch.

After the selection, the CLI detects the corresponding Git changes and opens a terminal user interface.

The TUI has two vertical panes:

- the left pane shows a tree/list of modified files,
- the right pane shows one continuous review view containing the changed files.

The file list and review pane stay synchronized. Selecting a file in the left pane scrolls the right pane to that file. Scrolling through the right pane updates the highlighted file in the left pane to match the currently visible file.

The right pane shows changed files with syntax highlighting and line numbers. Lines wrap by default. The default diff view shows broad context around changes, similar to `git diff -W`, rather than only a few surrounding lines. Where the full file is not visible, selectable expansion rows allow the user to expand more context upward or downward by 20 lines.

Users can navigate between panes with `Tab`. In the review pane, users can move the selected line with arrow keys and page keys. Mouse support lets users click a pane or line to focus and select it.

Users can select one or more lines. `Shift+Up` and `Shift+Down` extend or shrink the selected range. Pressing `Enter` on selected code lines opens an inline GitHub-style comment input below the selected line or range. Comments are stored in memory and rendered inline beneath the referenced code. Multi-line comments must visually show the range they apply to.

Exiting uses Vim-style command mode: the user presses `:` and then enters `q`. The command menu initially supports only quit, but must be designed so more commands can be added later.

When the review ends, the CLI asks where to send the review:

- a selected tmux pane, chosen from available panes with titles and pane IDs, or
- no pane, in which case the tool prints the final review text to standard output and exits.

When a tmux pane is selected, the tool sends the generated review feedback to that pane and presses Enter. The review feedback groups comments by file and includes referenced line numbers, selected context lines, and the user comment below each context block.

The tool must be thoroughly tested with unit tests, integration tests using temporary Git repositories, TUI behavior tests, and tmux integration tests where possible.

## Documentation Map

- [Product Requirements](./product-requirements.md): user-facing behavior and acceptance criteria.
- [Architecture](./architecture.md): proposed Python modules, data flow, and boundaries.
- [TUI Behavior](./tui-behavior.md): layout, navigation, selection, comments, and rendering rules.
- [Git Diff Model](./git-diff-model.md): how changes should be collected and represented.
- [Comment Output Format](./comment-output-format.md): feedback message format for tmux and stdout.
- [Tmux Integration](./tmux-integration.md): pane discovery, selection, and message delivery.
- [Testing Scenarios](./testing-scenarios.md): detailed test matrix and manual verification plan.
- [Acceptance Checklist](./acceptance-checklist.md): end-to-end completion checklist.
- [Known Limitations](./known-limitations.md): explicit limits of the initial implementation.

## Non-Goals For The Initial Version

- Publishing comments directly to GitHub, GitLab, Bitbucket, or other review systems.
- Persistent storage of unfinished review sessions.
- Editing files from inside the review tool.
- Applying patches or accepting/rejecting changes.
- Multi-user review collaboration.
- Running static analysis or producing automatic review suggestions.

## Terminology

`Review source` means the selected source of changes: uncommitted working tree changes or branch comparison.

`Review document` means the internal ordered representation of files, context blocks, changed lines, expansion rows, and comments.

`Review pane` means the right pane showing the continuous diff-like code view.

`File pane` means the left pane showing modified files.

`Expansion row` means a selectable row in the review pane that expands hidden context above or below a visible block.

`Comment range` means one or more lines in a single file that a review comment applies to.

`Delivery target` means either a tmux pane or standard output.
