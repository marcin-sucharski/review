# Architecture

## Design Principles

The implementation should keep terminal UI code separate from Git parsing, review state, output formatting, and tmux integration.

The most important rule is that behavior must be testable without launching a full terminal UI. The TUI should render and mutate a plain in-memory review model rather than owning the business logic.

## Package Layout

The implemented package layout is:

```text
src/
  review/
    __init__.py
    __main__.py
    cli.py
    errors.py
    git.py
    languages.py
    diff_model.py
    review_state.py
    format_review.py
    archive.py
    tmux.py
    tui/
      __init__.py
      app.py
      file_tree.py
      highlight.py
      menu.py
tests/
  unit/
  integration/
  tui/
docs/
```

## Module Responsibilities

`cli.py` owns command startup, high-level prompts, error handling, and final process exit behavior.

`git.py` owns Git command execution and turns repository state into raw diff data.

`errors.py` defines typed user-facing exceptions.

`languages.py` maps file names and extensions to syntax/output languages.

`diff_model.py` owns parsing raw diff data into structured files, hunks, line records, and expansion ranges.

`review_state.py` owns the mutable review session state: focused pane, selected file, selected line range, expanded context, and saved comments.

`format_review.py` turns saved comments and referenced context into the final feedback message. Markdown is the default output format, and XML is available through the CLI output-format option.

`archive.py` persists completed non-empty reviews as JSON under the XDG local data directory and exposes read APIs for recent-review history commands.

`tmux.py` discovers panes and sends text to a selected pane.

`tui/app.py` owns curses application composition, rendering, global key bindings, review-pane navigation, command mode, comment editing, and mouse handling. The current implementation keeps these TUI behaviors together while preserving pure review state and formatting outside curses.

`tui/file_tree.py` builds the collapsed modified-file tree used by the file pane.

`tui/highlight.py` wraps Pygments syntax highlighting and maps tokens to renderer roles.

`tui/menu.py` owns compact inline terminal menus for startup, branch, and delivery selection.

## Core Data Flow

1. CLI validates repository state.
2. User selects review source.
3. Git adapter collects diff information.
4. Diff parser builds immutable file and line records.
5. Review state creates an initially visible review document with broad context.
6. TUI renders the left navigation pane and review pane from review state.
7. User navigates, expands context, and adds comments.
8. Quit command exits TUI and returns saved comments.
9. Formatter creates final review message.
10. If the review has comments, CLI writes a JSON archive.
11. CLI asks for delivery target.
12. Delivery writes Markdown to a timestamped local file, writes to stdout, or sends to a tmux pane.

## Core Domain Objects

### ReviewSession

Represents one review.

Fields:

- `source`: selected review source.
- `repository_root`: absolute path to the Git repository.
- `files`: ordered list of reviewed files.
- `comments`: list of saved comments.
- `selection`: current file and line/range selection.
- `expanded_ranges`: visible context expansions per file.

### ReviewFile

Represents one changed file.

Fields:

- `path`: current file path.
- `old_path`: previous path for renames.
- `status`: added, modified, deleted, renamed, binary, mode-changed. Git-reported copies are normalized to added files for display.
- `language`: syntax highlighting language.
- `old_lines`: optional old-side lines.
- `new_lines`: optional new-side lines.
- `visible_blocks`: currently visible code blocks and expansion rows.

### ReviewLine

Represents one displayed code line.

Fields:

- `kind`: context, addition, deletion, metadata, expansion, comment.
- `old_line_number`: old-side line number when applicable.
- `new_line_number`: new-side line number when applicable.
- `text`: display text.
- `highlight_language`: language used for syntax highlighting.
- `file_path`: owning file.
- `is_selectable`: whether the user can select it.

### ExpansionRow

Represents hidden context that can be expanded.

Fields:

- `file_path`: owning file.
- `direction`: above or below.
- `anchor_line`: nearby visible line.
- `remaining_count`: hidden line count.
- `expand_count`: default `20`.

### ReviewComment

Represents a saved user comment.

Fields:

- `id`: stable in-session identifier.
- `file_path`: file path.
- `start_line`: first selected new-side line.
- `end_line`: last selected new-side line.
- `selected_text`: selected context lines.
- `body`: comment body.
- `created_at`: timestamp for deterministic ordering if needed.

Comments should anchor to new-side line numbers for added and context lines. Deleted-line comments need a clear policy because they do not have new-side line numbers. The preferred policy is to allow deleted-line comments and label them as old-side lines in output.

### ReviewArchive

Represents the persisted record for one completed non-empty review.

Fields:

- `path`: absolute repository path where the review occurred.
- `branch`: current Git branch, or a detached-head label if not on a branch.
- `review_message`: exact generated message used for stdout or tmux delivery.

Archive files live under `$XDG_DATA_HOME/review/reviews` with a `~/.local/share/review/reviews` fallback. The filename should be unique and stable enough to avoid collisions, typically using a UTC timestamp plus random suffix. History commands read this directory directly, ignore malformed archive files, sort recent valid reviews first, and cap the default list at 10 entries.

## Diff Representation

The review pane should be a unified, continuous document made of file sections.

Each file section contains:

- file header,
- optional rename/delete/add metadata,
- visible blocks of code,
- expansion rows,
- inline comment blocks.

The UI should not replace the right pane content when a file is selected. It scrolls within the continuous document.

## Syntax Highlighting Strategy

Use a mature syntax highlighting library rather than implementing lexers manually.

The highlighter must support at least:

- Java,
- Python,
- JavaScript,
- TypeScript,
- CSS,
- HTML,
- JSX,
- SQL,
- XML,
- JSON,
- properties,
- YAML,
- Markdown,
- Nix,
- gitignore-style ignore files,
- JSON-compatible lock files.

Language detection should use file extension first, then filename, then fallback to plain text.

Recommended extension mapping:

| Extension | Language |
| --- | --- |
| `.java` | Java |
| `.py`, `.pyi` | Python |
| `.js`, `.mjs`, `.cjs` | JavaScript |
| `.ts` | TypeScript |
| `.tsx` | TSX or JSX-capable TypeScript |
| `.jsx` | JSX |
| `.css` | CSS |
| `.html`, `.htm` | HTML |
| `.sql` | SQL |
| `.xml` | XML |
| `.json` | JSON |
| `.properties` | Java properties |
| `.yml`, `.yaml` | YAML |
| `.md`, `.markdown` | Markdown |
| `.nix` | Nix |
| `.lock` | JSON |
| `.gitignore`, `.ignore`, `.dockerignore` | gitignore |

## State Management

Review state should expose explicit methods for mutations:

- `select_file(path)`
- `select_line(file_path, line_id)`
- `move_selection(delta)`
- `extend_selection(delta)`
- `expand_context(expansion_id)`
- `add_comment(range, body)`
- `delete_comment(comment_id)` if deletion is implemented
- `visible_file_for_scroll_offset(offset)`

The TUI should call these methods rather than editing lists directly.

## Synchronization Rules

File pane to review pane:

- Selecting a file scrolls the review pane to that file header or first changed line.
- The selected file becomes highlighted.

Review pane to file pane:

- When the review pane scrolls, compute the file section nearest the top visible code line.
- Highlight that file in the file pane.
- If the highlighted file is outside the file pane viewport, scroll the file pane enough to show it.

Comment list to review pane:

- Build comment-list rows from saved comments grouped by file.
- Prefix each comment row with its referenced line number or range.
- Selecting a comment row selects the matching inline comment item in the review document and scrolls it into view.

Sticky file header:

- If a file section extends beyond the current review pane viewport, show the current file name at the top of the review pane while scrolling within that file.
- The sticky header must update immediately when the next file section reaches the top.

## Error Handling

Internal modules should raise typed exceptions where useful:

- `NotAGitRepository`
- `NoChangesFound`
- `GitCommandError`
- `DiffParseError`
- `TmuxUnavailable`
- `TmuxSendError`

The CLI should catch these and render friendly terminal messages.

## Dependency Guidance

The project should prefer stable, maintained Python packages for:

- terminal UI,
- syntax highlighting,
- testing.

The implementation should avoid coupling the domain model to a specific TUI library so that the parser, state transitions, formatter, and tmux integration remain unit-testable.

## Performance Targets

The tool should feel responsive for normal review sizes:

- up to 200 changed files,
- up to 20,000 visible lines after expansion,
- comments added interactively without noticeable delay.

Comment input redraw is a hot path. Rendering should avoid rebuilding selection ranges, comment ranges, or file trees for every visible row on every typed character.

Large repositories and very large generated files should be handled gracefully by truncation, binary detection, or warnings.

## Future Extension Points

The design should allow later support for:

- additional colon commands,
- saved review drafts,
- direct GitHub/GitLab publishing,
- comment editing and deletion,
- filtering by file status or path,
- hiding whitespace-only changes,
- side-by-side diff mode,
- custom output templates.
