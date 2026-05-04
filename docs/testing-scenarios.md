# Testing Scenarios

## Testing Goals

The project needs layered tests:

- unit tests for pure parsing, state, formatting, and tmux command behavior,
- integration tests using temporary Git repositories,
- TUI interaction tests for navigation, selection, comments, and expansion,
- optional real tmux tests,
- manual end-to-end verification in a tmux session.

The test suite should be deterministic and runnable in CI without requiring a real tmux session. Real tmux tests should be opt-in or auto-skipped when tmux is unavailable.

## Test Repository Fixture

Integration tests should create temporary Git repositories with:

- an initial commit,
- a `main` branch,
- a feature branch,
- staged changes,
- unstaged changes,
- untracked files,
- renamed files,
- deleted files,
- binary files.

Fixture files should cover required languages:

- `src/Main.java`,
- `web/app.js`,
- `web/app.jsx`,
- `web/app.ts`,
- `web/component.tsx`,
- `web/styles.css`,
- `web/index.html`,
- `db/schema.sql`,
- `config/app.xml`,
- `config/data.json`,
- `config/application.properties`,
- `config/settings.yaml`,
- `docs/notes.md`,
- `flake.nix`,
- `flake.lock`,
- `.gitignore`.

## CLI Startup Tests

| Scenario | Expected Result |
| --- | --- |
| Run outside Git repository | Friendly error, non-zero exit |
| Run inside Git repo with no changes | Reports no changes, exits cleanly |
| User selects uncommitted changes with unstaged file | Opens review with modified file |
| User selects uncommitted changes with staged file | Opens review with staged file |
| User selects uncommitted changes with staged and unstaged same file | Shows one unified file view with no staged/unstaged section split |
| Same text is added in staged and final worktree diffs at different lines | Both additions remain visible |
| User selects uncommitted changes with untracked text file | Shows file as added |
| User selects uncommitted changes with untracked binary file | Shows file as binary or skipped with explanation |
| User selects branch comparison | Compares merge base to current index/worktree, including committed branch changes |
| User selects branch comparison with staged changes | PR-style review includes staged local changes |
| User selects branch comparison with unstaged changes | PR-style review includes unstaged local changes |
| User selects branch comparison with untracked files | PR-style review includes untracked files |
| Branch comparison target does not exist | Friendly error |
| Git command fails | Friendly error without raw traceback |
| User presses `Ctrl+C` in startup review-source prompt | Exits cleanly on first press |

## Branch Selection Tests

| Scenario | Expected Result |
| --- | --- |
| Upstream branch exists | It is default selection |
| `origin/HEAD` points to `origin/main` | `origin/main` is preferred |
| Only local `main` exists | `main` is offered |
| Only local `master` exists | `master` is offered |
| Multiple local and remote branches exist | Duplicates removed, symbolic refs removed |
| Many branches exist | Branch picker shows only five target branches at a time |
| User types in branch picker | Branch list filters by substring without focusing a search box |
| Branch picker renders comparison | Current branch appears on the left and target branch appears after `->` |
| Common target branches exist | `master` and `main` appear before date-sorted topic branches, with `master` first |
| Topic branches have different commit dates | More recently committed branches appear earlier |
| Current branch has commits not on target | PR-style diff includes feature commits |
| Current branch has uncommitted changes on top of feature commits | PR-style diff includes both feature commits and current uncommitted changes |
| Target branch is ancestor of current branch | Diff includes changes since branch point |
| Current branch equals target branch with no uncommitted changes | No changes found |

## Git Diff Parsing Tests

| Scenario | Expected Result |
| --- | --- |
| Modified text file | Parsed as modified with context/addition/deletion rows |
| Added file | Parsed as added with new-side line numbers |
| Deleted file | Parsed as deleted with old-side line numbers |
| Renamed file without content changes | Parsed as rename metadata |
| Renamed file with content changes | Parsed as rename plus changed lines |
| Copied file | Parsed as copied when Git reports copy |
| File mode change | Metadata preserved |
| Binary file changed | Parsed as binary, not line-commentable |
| File path contains spaces | Parsed path remains correct |
| File path contains unicode | Parsed path remains correct |
| File has no trailing newline | Marker handled without parser failure |
| Diff includes quoted paths | Paths decoded correctly |
| Large hunk | Parser remains performant |
| Multiple hunks in one file | File contains all hunks in order |
| Hunk with function context | Function header/context preserved |
| Merge conflict markers in file | Displayed as source text |

## Diff Context And Expansion Tests

| Scenario | Expected Result |
| --- | --- |
| Change inside small function | Whole function context visible by default |
| Change near top of file | No invalid expand-up row before file start |
| Change near bottom of file | No invalid expand-down row after file end |
| Hidden context exists above | Expand-up row appears |
| Hidden context exists below | Expand-down row appears |
| Activate expand-up row | Up to 20 previous lines appear |
| Activate expand-down row | Up to 20 following lines appear |
| Fewer than 20 hidden lines remain | All remaining lines appear |
| Expand same direction repeatedly | More context appears until exhausted |
| Expansion row exhausted | Row disappears |
| Comments exist below expansion | Comments remain attached to original lines |
| Selection exists during expansion | Selection is preserved when possible |
| Multi-hunk file has hidden middle context | Middle expansion row expands correctly |

## Syntax Highlighting Tests

| File | Expected Lexer |
| --- | --- |
| `Main.java` | Java |
| `app.js` | JavaScript |
| `component.jsx` | JSX |
| `app.ts` | TypeScript |
| `component.tsx` | TSX or JSX-capable TypeScript |
| `styles.css` | CSS |
| `index.html` | HTML |
| `schema.sql` | SQL |
| `config.xml` | XML |
| `data.json` | JSON |
| `application.properties` | Java properties or properties lexer |
| `settings.yaml` | YAML |
| `notes.md` | Markdown |
| `flake.nix` | Nix |
| `flake.lock` | JSON |
| `.gitignore` | gitignore-style highlighting |
| `unknown.xyz` | Plain text fallback |
| Extensionless file | Plain text or content-detected lexer |
| Invalid syntax file | Still renders without crash |

## Review State Tests

| Scenario | Expected Result |
| --- | --- |
| Initial state | First file and first selectable line selected |
| Select file | Review document scroll target points to file |
| Scroll review pane into next file | Highlighted file changes |
| Move selection down | Next selectable row selected |
| Move selection over metadata | Metadata skipped if non-selectable |
| Move selection over expansion row | Expansion row selectable |
| Extend selection down | Range includes contiguous code lines |
| Extend selection up | Range includes contiguous code lines |
| Shrink selection by reversing direction | Range shrinks correctly |
| Selection reaches file boundary | Does not cross into another file |
| Selection includes wrapped line | Logical line selected once |
| Add single-line comment | Comment stored with correct file and line |
| Add multi-line comment | Comment stored with correct start/end lines |
| Add comment to added line | New-side line reference used |
| Add comment to deleted line | Old-side line reference used or rejected by policy |
| Multiple comments same file | Comments sorted by line |
| Comments across files | Grouping remains correct |

## TUI Rendering Tests

| Scenario | Expected Result |
| --- | --- |
| TUI starts with changes | Review pane visible with file pane hidden by default |
| Press `T` from initial view | File pane appears |
| File pane shown | Collapsed directory tree is visible |
| Press `Tab` with file pane visible | Focus cycles between review pane, file tree, and comment list |
| Select file in file pane | Review pane scrolls to file |
| Comments exist and file pane is shown | Comment list appears under file tree grouped by file |
| Select comment in comment list | Review pane focuses the inline saved comment |
| Select or edit a saved comment | Focus background is visible without underline styling |
| Select a code line or range | Selection background is visible without underline styling |
| Modified-file tree overflows | Bottom footer shows how many rows are below |
| Comment list overflows | Bottom footer shows how many rows are below |
| Scroll review pane | File pane highlight updates |
| Long file scrolls | Sticky header shows current file |
| Next file reaches top | Sticky header updates |
| Line numbers visible | Code rows include numbers |
| Long lines wrap | Lines wrap without breaking layout |
| Terminal 80x24 | UI remains usable |
| Very narrow terminal | Clear too-small message or graceful layout |
| Resize terminal | Selection and scroll mostly preserved |

## Keyboard Interaction Tests

| Scenario | Expected Result |
| --- | --- |
| File pane `Up` | Previous file selected |
| File pane `Down` | Next file selected |
| File pane `Enter` | Review pane scrolls to file |
| Review pane `Up` | Previous selectable row selected |
| Review pane `Down` | Next selectable row selected |
| Review pane `PageUp` | Moves up by page |
| Review pane `PageDown` | Moves down by page |
| Review pane `Shift+Down` | Multi-line selection extends |
| Review pane `Shift+Up` | Multi-line selection extends upward |
| Review pane `Enter` on code | Comment input opens |
| Review pane `Enter` on expansion row | Context expands |
| `Esc` in comment input | Input cancels |
| `Ctrl+J` in comment input | Inserts newline and blank row renders immediately |
| `Left`/`Right` in comment input | Cursor moves within text and edits occur at that position |
| `Up`/`Down` in comment input | Cursor moves between comment lines and preserves column where possible |
| `Ctrl+A` in comment input | First press moves to current line start, second press moves to message start |
| `Ctrl+E` in comment input | First press moves to current line end, second press moves to message end |
| `Option+Left`/`Option+Right` in comment input | Cursor moves by word for supported terminal escape sequences |
| `Alt`/`Meta` left/right in a real tmux TUI session | Cursor x-position moves by word and inserted text lands at the word-moved cursor |
| `Enter` in comment input | Saves comment |
| Submit comment | Comment appears inline |
| Press `:` | Command mode opens |
| Command `q` | Starts quit flow |
| Command `quit` | Starts quit flow |
| Unknown command | Shows error, stays in TUI |
| First `Ctrl+C` in TUI | Warning is shown and TUI remains open |
| Second consecutive `Ctrl+C` in TUI | TUI exits |
| Other key after first `Ctrl+C` | Pending interrupt is cleared |

## Mouse Interaction Tests

| Scenario | Expected Result |
| --- | --- |
| Click file pane | File pane focused |
| Click review pane | Review pane focused |
| Click file entry | File selected and review pane scrolls |
| Click code line | Line selected |
| Click expansion row | Row selected or expanded according to UX |
| Mouse wheel review pane | Scrolls review pane |
| Mouse wheel changes visible file | File pane highlight updates |
| Click wrapped continuation | Original logical line selected |

## Comment Input Tests

| Scenario | Expected Result |
| --- | --- |
| Open comment on single line | Input appears below selected line |
| Open comment on range | Input appears below last selected line |
| Submit one-line comment | Saved and rendered inline |
| Submit multi-line comment | Newlines preserved |
| Submit empty comment | Rejected or ignored according to policy |
| Cancel comment | No comment saved |
| Comment contains backticks | Output fence remains valid |
| Comment contains shell metacharacters | Tmux send treats as literal text |
| Add comment after expansion | Anchors to correct line |
| Expand around existing comment | Comment remains under correct line |

## Output Formatting Tests

| Scenario | Expected Result |
| --- | --- |
| No comments | Empty-review behavior matches policy |
| One comment | Default Markdown output includes repo, source, file, line, code, comment |
| Markdown output default | No `--output-format` emits Markdown headings and fenced blocks |
| Multi-line comment range | XML `line_range` includes start/end and label text |
| Deleted-line comment | XML `line_range` uses `side="old"` |
| Multiple files | XML output grouped by `file` elements |
| Multiple comments same file | `review_comment` elements sorted by line number |
| Code contains XML metacharacters | Formatter escapes text and output remains parseable |
| XML output option | `--output-format xml` emits parseable XML |
| Markdown fence collision | Fences expand when code or comments contain backticks or tildes |
| Code contains triple backticks | Backticks are preserved as text without Markdown fence handling |
| Unicode comment and code | Preserved |
| Long line | Preserved or wrapped only by terminal, not formatter |
| Branch comparison source | Target branch included |
| Uncommitted source | Source labeled as uncommitted changes |
| Save-to-file delivery with default format | Writes Markdown to `review-YYYYMMDD-HHMM.md` in the current directory |
| Save-to-file delivery while `--output-format xml` is selected | File still contains Markdown, while stdout/tmux delivery would use XML |
| Save-to-file write failure | Friendly error is shown and Markdown review text is printed as fallback |

## Review Archive Tests

| Scenario | Expected Result |
| --- | --- |
| Non-empty review delivered to stdout | One JSON archive file is written |
| Non-empty review delivered to tmux | One JSON archive file is written before send |
| Empty review | No archive file is written |
| `XDG_DATA_HOME` is set | Archive goes to `$XDG_DATA_HOME/review/reviews` |
| `XDG_DATA_HOME` is unset | Archive goes to `~/.local/share/review/reviews` |
| Current branch is available | JSON `branch` equals branch name |
| Detached HEAD | JSON `branch` uses detached short-sha label |
| Archive payload | Contains `path`, `branch`, and exact `review_message` |

## Tmux Unit Tests

| Scenario | Expected Result |
| --- | --- |
| Parse pane list with one pane | Pane model correct |
| Parse pane list with multiple panes | All panes parsed |
| Pane title empty | Display fallback works |
| Current pane detected | Current pane marked |
| tmux missing | Stdout fallback available |
| tmux list fails | Friendly error and stdout fallback |
| tmux missing during delivery selection | Save-to-file and terminal delivery remain selectable |
| Selected pane disappears | Send failure handled |
| Review text contains quotes | Sent literally |
| Review text contains newlines | Sent as one buffer |
| Review text contains shell metacharacters | Not shell-executed |

## Real Tmux Integration Tests

These tests should run only when tmux is available.

| Scenario | Expected Result |
| --- | --- |
| Create temporary tmux session | Session starts |
| Discover panes | Test panes listed |
| Send text to target pane running `cat` | Text appears |
| Send Enter | Target receives newline |
| Kill target before send | Error handled |
| Run outside tmux with tmux server active | Panes can still be listed or stdout fallback works |
| Run real TUI under tmux and send `M-Left`/`M-Right` | Comment editor cursor moves by word and submitted review text proves insertion happened at the moved cursor |

## Manual End-To-End Scenarios

### Scenario 1: Uncommitted Review To Stdout

1. Create a temporary Git repository.
2. Commit baseline files.
3. Modify JavaScript, CSS, JSON, and YAML files.
4. Run `review`.
5. Select uncommitted changes.
6. Verify the file pane is hidden by default.
7. Press `T` and verify the file tree appears.
8. Add single-line and multi-line comments.
9. Quit with `:q`.
10. Select save to file and verify `review-YYYYMMDD-HHMM.md` is written in the current directory with Markdown content.
11. Repeat and select send to terminal.
12. Verify stdout contains grouped comments with line references and context.
13. Verify a review JSON archive was written and contains the same review message.
14. Repeat with `--output-format xml`, verify stdout and archive contain XML, and verify save-to-file still writes Markdown.
15. Repeat the quit flow from a visible review pane and verify the delivery menu appears on a cleared terminal screen at the prompt area, with no stale TUI panes or previous review output mixed into the selector.

### Scenario 2: Branch Review To Tmux Pane

1. Create a temporary Git repository.
2. Commit `main`.
3. Create a feature branch.
4. Modify TypeScript, Java, and SQL files.
5. Commit one feature change.
6. Add one staged change, one unstaged change, and one untracked file.
7. Start tmux with two panes.
8. Run `review` in one pane.
9. Select branch comparison against `main`.
10. Verify the review includes the committed feature change, staged change, unstaged change, and untracked file.
11. Add comments.
12. Quit with `:q`.
13. Verify a review JSON archive was written.
14. Select the other pane.
15. Verify review feedback appears in the target pane and Enter is sent.

### Scenario 3: Expansion And Sticky Header

1. Create a file with at least 200 lines and changes around line 100.
2. Run review.
3. Verify only relevant broad context is initially visible.
4. Select `Show 20 lines above`.
5. Verify 20 additional lines appear.
6. Scroll within the file.
7. Verify sticky file header remains visible.
8. Scroll to the next file.
9. Verify sticky header updates.

### Scenario 4: Required Language Highlighting

1. Create changes in each required file type.
2. Run review.
3. Verify every file renders without plain parser errors.
4. Verify unknown extension falls back to plain text.

### Scenario 5: Deleted And Renamed Files

1. Delete one tracked file.
2. Rename another file and modify its content.
3. Run review.
4. Verify deleted file shows old-side line numbers.
5. Verify renamed file shows old and new paths.
6. Add comments where supported.
7. Verify output labels the references correctly.

### Scenario 6: Interrupt Behavior

1. Start `review` and leave it on the review-source prompt.
2. Press `Ctrl+C`.
3. Verify the program exits with cancellation.
4. Start `review --source uncommitted`.
5. Press `Ctrl+C` inside the TUI.
6. Verify the warning appears and the TUI remains open.
7. Press a non-`Ctrl+C` key and verify the warning clears.
8. Press `Ctrl+C` twice consecutively.
9. Verify the TUI exits.

## Regression Test Checklist

Before considering implementation complete, run:

- unit tests,
- Git integration tests,
- formatter tests,
- TUI interaction tests,
- tmux unit tests,
- real tmux tests when available,
- manual uncommitted review,
- manual branch review,
- manual branch review with committed, staged, unstaged, and untracked changes,
- manual stdout delivery,
- manual tmux delivery.
- manual archive verification.

## Coverage Expectations

The test suite should cover:

- every supported review source,
- every required file type mapping,
- every line kind,
- every file status,
- single-line and multi-line comments,
- expansion in both directions,
- output formatting edge cases,
- tmux success and failure paths,
- startup and graceful error paths.
- local review archive behavior.

High line coverage alone is not enough. The important measure is behavioral coverage of the review workflow.
