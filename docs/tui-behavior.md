# TUI Behavior

## Layout

The terminal UI opens in a review-first layout. The file pane is hidden by default and the review pane is focused.

Pressing `T` shows the left navigation pane. When the left pane is visible, the terminal UI uses a two-pane vertical layout. The left pane is split vertically: the upper region shows modified files and the lower region shows saved review comments.

```text
+-----------------------------+------------------------------------------------+
| Modified files              | Current file: src/example.py                  |
|                             |                                                |
| > src/example.py            |  12  def example():                            |
|   tests/test_example.py     |  13      value = compute()                     |
| Review comments             |  14 +    return value                          |
|   src/example.py            |                                                |
| >  14 Use explicit result   |      [comment attached to line 14]             |
|                             |                                                |
+-----------------------------+------------------------------------------------+
| :q                                                                           |
+------------------------------------------------------------------------------+
```

The left file pane should use roughly 25-35% of the terminal width, with a sensible minimum width. The right review pane gets the remaining width. When the file pane is hidden, the review pane uses the full terminal width.

The layout must degrade gracefully in narrow terminals. If the terminal is too small for a usable two-pane view, the app should show a clear message.

## Focus

Exactly one pane is focused at a time.

Focused pane indicators may include border styling, title styling, cursor visibility, or selection color.

`Tab` switches focus between the review pane, file tree, and comment list when the left pane is visible. When the left pane is hidden, `Tab` keeps focus in the review pane.

Mouse click inside a pane focuses that pane.

## File Pane

The file pane lists modified files as a tree. Directory chains with no modified file between them should be collapsed into a compact path such as `src/review/`.

Each file entry should display:

- change status marker,
- path,
- comment count if comments exist for the file.

Suggested markers:

| Status | Marker |
| --- | --- |
| Modified | `M` |
| Added | `A` |
| Deleted | `D` |
| Renamed | `R` |
| Copied file reported by Git | `A` |
| Binary | `B` |

The highlighted file is the file currently visible in the review pane or selected by the user.

The file pane is not required to be visible for file synchronization to occur. When it is shown again, the highlighted file should match the active review selection or current scroll location.

When the modified-file tree is scrollable, the bottom of the region should show a compact footer such as `v 10 more below`. If the view is scrolled down, the same footer may also show how many rows are above.

## Comment List Pane

The comment list pane appears below the file tree when the left pane is visible. It lists all saved review comments grouped by file.

Each file group should display the file path. Each comment row should display:

- referenced line number or line range,
- shortened preview of the first part of the comment body.

`Up`/`k` and `Down`/`j` move between comment rows. Moving to a comment focuses the corresponding inline saved comment in the review pane and scrolls the review pane enough to make it visible. `Enter` keeps that comment selected and returns focus to the review pane. `Delete` and `Backspace` delete the selected saved comment. Mouse click on a comment row has the same focusing behavior.

When the comment list is scrollable, the bottom of the region should show a compact footer such as `v 10 more below`, with an above-count when the user has scrolled down.

## Review Pane

The review pane is one continuous scrollable document.

It contains all reviewed file sections in order.

A file section should include:

- file header,
- optional status metadata,
- code lines,
- expansion rows,
- inline comments.

The right pane must not switch to a separate isolated file page. Selecting from the file pane scrolls within the continuous document.

## Sticky File Header

When scrolling within a file section whose header has moved off screen, a sticky header stays at the top of the review pane.

The sticky header displays:

- file path,
- status,
- current comment count for that file when useful.

The sticky header disappears or changes when the next file header reaches the top of the viewport.

## Line Numbers

Line numbers are shown by default.

For context and added lines, show the new-side line number.

For deleted lines, show the old-side line number and visually distinguish it from new-side numbers.

For wrapped lines, continuation rows should align under the code text and avoid repeating the line number unless repeating is clearer in the chosen TUI library.

## Wrapping

Code wraps by default.

Wrapped content must not break selection semantics. Selecting a wrapped line selects the original logical code line, not only the visual continuation row.

## Changed Line Styling

The UI should visually distinguish:

- added lines,
- deleted lines,
- context lines,
- selected lines,
- active cursor line,
- lines with comments,
- expansion rows.

The style must remain readable in both light and dark terminal themes. Avoid relying only on color; use markers where practical.

The active selected code line must be visible on every diff background. Added and deleted lines should keep their green/red meaning while using a light selected variant of that background rather than switching to a saturated unrelated color. Selected code lines and comment rows should not use underline; the background highlight is the selection indicator.

## Syntax Highlighting

The code text must be syntax highlighted by language.

Required language coverage:

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
- properties,
- YAML,
- Markdown,
- Nix,
- gitignore-style ignore files,
- JSON-compatible lock files.

If highlighting fails for a file, display plain text instead of failing the review session.

## Navigation Keys

Global keys:

| Key | Behavior |
| --- | --- |
| `Tab` | Switch focused pane |
| `T` | Show or hide the file pane |
| `:` | Open command mode |
| `/` | Open search prompt from the current review cursor position |
| `n` | Jump to the next active search match |
| `p` | Jump to the previous active search match |
| `Ctrl+C` | First press arms quit confirmation, second consecutive press quits |
| `Esc` | Cancel current transient mode when applicable |

File pane keys:

| Key | Behavior |
| --- | --- |
| `Up` / `k` | Select previous file |
| `Down` / `j` | Select next file |
| `PageUp` | Move up by page |
| `PageDown` | Move down by page |
| `Enter` | Scroll review pane to selected file |

Review pane keys:

| Key | Behavior |
| --- | --- |
| `Up` / `k` | Select previous selectable row |
| `Down` / `j` | Select next selectable row |
| `PageUp` | Scroll/select up by page |
| `PageDown` | Scroll/select down by page |
| `Shift+Up` | Extend selection upward |
| `Shift+Down` | Extend selection downward |
| `Enter` | Comment selected code or activate expansion row |
| `Delete` / `Backspace` | Delete the selected saved comment |

Arrow navigation should behave like an editor: the selected row moves within the viewport until it reaches the lower edge, then the viewport scrolls while keeping roughly three rows below the selected row. Scroll calculations use rendered terminal rows, so a wrapped logical line is kept fully inside the visible review pane before navigation continues. Page navigation moves by a viewport and preserves the selected row's screen offset when possible.

Search prompt keys:

| Key | Behavior |
| --- | --- |
| `/` | Opens the search prompt in the status line |
| Typed text | Builds a literal search query |
| `Enter` | Applies the search and jumps from the current cursor position to the first matching visible review row |
| `Enter` with an empty query | Clears the active search and removes match highlighting |
| `Esc` | Cancels editing the query and keeps the previous active search |
| `n` | Jumps to the next match after the current cursor, wrapping at the end |
| `p` | Jumps to the previous match before the current cursor, wrapping at the beginning |

Search matches are highlighted in the review pane. The initial search starts from the currently selected review row rather than from the top of the review.

Comment input keys:

| Key | Behavior |
| --- | --- |
| `Enter` | Submit comment |
| `Ctrl+J` | Insert newline |
| `Left` / `Right` | Move the comment cursor by character |
| `Up` / `Down` | Move the comment cursor between lines |
| `Ctrl+A` | Move to the beginning of the current line, or to the beginning of the message if already at line start |
| `Ctrl+E` | Move to the end of the current line, or to the end of the message if already at line end |
| `Option+Left` / `Option+Right` | Move the comment cursor by word where the terminal sends a supported Meta or modified-arrow sequence |
| `Ctrl+W` | Delete the word before the comment cursor |
| `Esc` | Cancel comment input |

When `Ctrl+J` inserts a newline at the end of the comment buffer, the newly created blank comment row should render immediately without waiting for another character.

While a comment input or edit is active, the terminal cursor should be visible at the current insertion point, including on newly inserted multiline rows.

Comment input should behave like a small multiline editor: typed text inserts at the cursor, Backspace removes the character before the cursor, `Ctrl+W` removes the word before the cursor, line-boundary movement follows common shell/editor bindings, word movement crosses whitespace and punctuation, and vertical movement preserves the intended column when moving across shorter or longer lines.

While editing a comment message, `j` and `k` are inserted as normal text rather than treated as navigation keys.

## Mouse Behavior

Mouse behavior:

- clicking a pane focuses it,
- clicking a file selects it and scrolls to that file,
- clicking a code line selects it,
- dragging across code lines may select a range if supported,
- clicking an expansion row activates or selects it,
- wheel scrolling in the review pane updates the highlighted file in the file pane.

Mouse wheel events should work across the full review pane width, including very wide terminals. The TUI enables extended SGR mouse coordinates so columns beyond the legacy mouse-coordinate range are still decoded.

Mouse support should be tested where the TUI framework supports synthetic mouse events.

## Selection Model

The review pane has a selected logical row.

Commentable rows are code rows. Expansion rows are selectable but not commentable.

When selection starts on a code row, `Shift+Up` and `Shift+Down` create a contiguous selection range inside the same file.

The UI must show:

- all lines in the active range,
- the anchor line,
- whether the range crosses additions/deletions/context,
- where the comment will appear if submitted.

Selection should not cross file boundaries. If the user extends beyond the first or last selectable line in a file, the selection stops at the boundary.

## Comment Input Placement

For a single line, the input appears below that line.

For a multi-line range, the input appears below the last selected line.

The comment block should include a compact left-side marker showing the range. The vertical marker appears between the line number and the change marker, and continues into the comment body. Example:

```text
  20 |+ const value = compute();
  21 |+ return value;
     |  Needs a null check before returning.
```

The exact visual style may vary, but the reference range must be obvious. Inline saved comments show only the user's comment body, not a repeated `comment on lines ...` title.

When editing an existing saved comment, keyboard changes must render immediately in the inline comment block as the user types. The saved comment body is updated in the review state only when the edit is submitted.

When a saved comment is selected or being edited, focus should be shown with a clear light-theme-safe background treatment rather than underline styling.

## Saved Comment Rendering

Saved comments appear inline in the review pane.

Each saved comment must show:

- range marker,
- comment body,
- visual attachment to the selected code lines.

The file pane should reflect files with comments, such as by showing a count.

## Expansion Rows

Expansion rows are part of the review pane and can be selected.

They are displayed when hidden context exists above or below the current visible block.

Activating an expansion row reveals up to 20 more lines in that direction.

When fewer than 20 lines remain, the label should say or imply that all remaining lines will be shown.

Expansion rows should not steal comments or reset selections unrelated to the expanded region.

## Command Mode

Pressing `:` opens command mode at the bottom of the TUI.

Initial supported commands:

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

Unknown commands should show a concise error and keep the user in the TUI.

Command mode must be implemented as a dispatch table or equivalent extensible structure so future commands can be added cleanly.

## Interrupt Behavior

The initial review-source menu belongs to the startup flow and cancels with one `Ctrl+C`.

After the source is selected, including inside the TUI, branch selection, and delivery selection, `Ctrl+C` requires confirmation:

1. First `Ctrl+C` shows a warning.
2. A second consecutive `Ctrl+C` exits.
3. Any other key clears the pending interrupt.

## Quit And Delivery Transition

When `q` or `quit` succeeds, the TUI closes and returns the in-memory comments to the CLI.

The CLI then prompts for a delivery target outside or inside a simple terminal selection UI.

The final review message must be generated after the TUI closes so stdout delivery is clean and not mixed with TUI drawing artifacts.

Before the delivery menu or stdout output is rendered, the CLI restores normal terminal attributes, makes the cursor visible, clears the stale TUI screen, and moves the prompt to a predictable bottom-of-terminal position. This prevents inline menus from being drawn in the middle of leftover review panes after curses exits.

## Empty Review Behavior

If the user quits with no comments, the TUI exits immediately without a confirmation prompt.

The CLI then prints the empty-review message or delivers it according to the selected non-TUI path.

## Terminal Resize Behavior

On resize, the TUI should recompute pane sizes and preserve:

- focused pane,
- selected file,
- selected line/range,
- scroll position as closely as possible,
- inline comment positions.

Sticky headers and wrapping must update to match the new width.
