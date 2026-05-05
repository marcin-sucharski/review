# Review CLI Documentation

`review` is a Python terminal review tool for inspecting Git changes, writing line-level review comments, and sending the collected feedback to a coding agent through tmux or standard output.

This directory is the implementation contract for the project. It intentionally comes before the code so the behavior, interfaces, testing obligations, and edge cases are explicit.

## Initial Instructions

The project must implement a CLI named `review`.

On startup, the CLI asks whether the user wants to review:

- changes in pull-request style against a selected branch.
- uncommitted changes in the current Git working tree.

After the selection, the CLI detects the corresponding Git changes and opens a terminal user interface.

For pull-request style reviews, the target branch picker shows the current branch on the left and the target branch after `->`. It shows five branch choices at a time, supports `Up`/`Down` selection, and filters by typed substring immediately. The PR-style review shows the final working-tree diff against the target branch merge base, so committed branch changes and current staged or unstaged edits appear once as the current file state. Untracked files are included as added files.

For uncommitted reviews, staged and unstaged changes are collected together and shown as one unified review view. A file that has both staged and unstaged edits appears once as the final working-tree diff against `HEAD`, without separate staged and unstaged sections.

The TUI uses a review-first layout. The left navigation pane is hidden by default to maximize code space, and `T` shows or hides it. When visible, the TUI has two vertical panes:

- the left pane is split between a tree/list of modified files and a grouped list of review comments,
- the right pane shows one continuous review view containing the changed files.

The file list and review pane stay synchronized. Selecting a file in the left pane scrolls the right pane to that file. Scrolling through the right pane updates the highlighted file in the left pane to match the currently visible file.

The comment list groups saved comments by file. Each comment row is prefixed with the referenced line number or line range and shows a shortened preview of the comment body. Selecting a comment from this list focuses the corresponding inline comment in the review pane.

The right pane shows changed files with syntax highlighting and line numbers. Lines wrap by default. The default diff view shows broad context around changes, similar to `git diff -W`, rather than only a few surrounding lines. Where the full file is not visible, selectable expansion rows allow the user to expand more context upward or downward by 20 lines.

Users can navigate between panes with `Tab`. When the left pane is visible, focus cycles through the review pane, file tree, and comment list. In the review pane, users can move the selected line with arrow keys and page keys. Pressing `/` opens a search prompt from the current cursor position; `n` and `p` jump through matches, and an empty search clears highlighting. Mouse support lets users click a pane or line to focus and select it.

Users can select one or more lines. `Shift+Up` and `Shift+Down` extend or shrink the selected range. Pressing `Enter` on selected code lines opens an inline GitHub-style comment input below the selected line or range. `Ctrl+J` inserts a newline in the comment input and `Enter` saves the comment. While writing or editing a comment, arrow keys move within the message, `Ctrl+A`/`Ctrl+E` move to line or message boundaries, `Ctrl+W` deletes the word before the cursor, and supported `Option+Left`/`Option+Right` sequences move by word. Comments are stored in memory and rendered inline beneath the referenced code. Multi-line comments must visually show the range they apply to.

Exiting uses Vim-style command mode: the user presses `:` and then enters `q`. The command menu initially supports only quit, but must be designed so more commands can be added later.

`Ctrl+C` behavior depends on where the user is in the flow. In the initial review-source menu, one `Ctrl+C` cancels and exits. After the source is selected, including inside the TUI and later branch or delivery menus, the user must press `Ctrl+C` twice to exit.

When the review ends, the CLI asks where to send the review:

- save to a timestamped Markdown file in the current directory, named `review-YYYYMMDD-HHMM.md`,
- send to terminal, in which case the tool prints the final review text to standard output and exits, or
- a selected tmux pane, chosen from available panes with titles and pane IDs.

When a tmux pane is selected, the tool sends the generated review feedback to that pane and presses Enter. Non-empty review feedback is Markdown by default and can be switched to XML with `--output-format xml` or `-o xml`. Both formats group comments by file and include referenced line numbers, selected context lines, and the user comment body. The save-to-file delivery target always writes Markdown for easy local reading.

Every non-empty review is also saved before delivery. The archive is a JSON file under `$XDG_DATA_HOME/review/reviews` or, when `XDG_DATA_HOME` is not set, `~/.local/share/review/reviews`. Each file contains the repository path, current Git branch, and exact generated review message.

Archived reviews can be inspected without being in a Git repository. `review ls` lists the 10 most recent saved reviews, one per line. `review display` opens a small interactive history menu and prints the selected review message to stdout, keeping prompts off stdout so redirection captures only the review body. `review display --file` or `review display -f` saves the selected review message to a timestamped review file in the current directory instead.

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
- Persistent storage of unfinished review sessions. Completed non-empty review messages are archived, but drafts and partially completed sessions are not.
- Editing files from inside the review tool.
- Applying patches or accepting/rejecting changes.
- Multi-user review collaboration.
- Running static analysis or producing automatic review suggestions.

## Terminology

`Review source` means the selected source of changes: uncommitted working tree changes or branch comparison.

`Review document` means the internal ordered representation of files, context blocks, changed lines, expansion rows, and comments.

`Review pane` means the right pane showing the continuous diff-like code view.

`File pane` means the top part of the left pane showing modified files.

`Comment list pane` means the lower part of the left pane showing saved review comments grouped by file.

`Review archive` means the JSON file written for every completed non-empty review.

`Expansion row` means a selectable row in the review pane that expands hidden context above or below a visible block.

`Comment range` means one or more lines in a single file that a review comment applies to.

`Delivery target` means a timestamped Markdown file, standard output, or a tmux pane.
