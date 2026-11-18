# Review 0.4.3 terminal theme regression report

Review 0.4.3 inherits the terminal's default background for menus, normal
context, file paths, editors, padding, and empty review rows. Ordinary text
inherits the terminal's foreground; syntax and UI accents retain their semantic
ANSI colors. Diff, comment, selection, and search highlights use literal RGB
backgrounds with contrasting text.

The reproducing Rex theme maps extended palette index 231 to dark #333940 and
index 16 to light #fafafa. Palette indices therefore cannot stand in for fixed
white and black. Literal RGB highlights preserve their intended colors while
the normal canvas follows the user's light or dark terminal theme.

Interactive screens enable the colors required for highlights even when
NO_COLOR is inherited, and restore the previous color-output policy on exit.
Menus reset terminal attributes and colors around changed rows. The live tmux
harness waits for the review to open after target selection and for delivery or
exit after quitting before checking those screens.

Cargo.toml and Cargo.lock identify the application as 0.4.3. The flake reads its
package version from Cargo.toml, and both Linux package outputs evaluate to
0.4.3. Builds and live tests run on x86_64 Linux.

## Automated checks

| Exact command | Outcome |
| --- | --- |
| `nix develop -c cargo fmt --all -- --check` | PASS |
| `nix develop -c cargo clippy --all-targets -- -D warnings` | PASS |
| `nix develop -c cargo test --all-targets` | PASS: 90 unit, 7 CLI integration, 21 Git integration tests |
| `nix develop -c cargo build --release` | PASS |
| `target/release/review --help` | PASS |
| `target/release/review --version` | PASS: review 0.4.3 |
| `nix build --no-link --print-out-paths path:.#review` | PASS: package build, tests, installation, and wrapper |
| `nix run path:. -- --version` | PASS: review 0.4.3 |
| `nix eval --raw .#packages.x86_64-linux.review.version` | PASS: 0.4.3 |
| `nix eval --raw .#packages.aarch64-linux.review.version` | PASS: 0.4.3 |
| `git diff --check` | PASS |
| `find src tests -type d -name __pycache__ -prune -exec rm -rf {} +` | PASS |

## Live tmux checks

Exact command: `python3 tests/live_tmux_regression.py`

The harness opens real panes on its own isolated `review_regression_<PID>`
server and stops only that server. Captures, fixtures, archives, and delivery
output from the release run are in `/tmp/review-live-40s1116e`.

| Scenario | Outcome |
| --- | --- |
| Source menu, arrow navigation, PR before uncommitted | PASS |
| Real uncommitted changes | PASS |
| PR-style review against a target with committed, staged, unstaged, and untracked changes | PASS |
| File pane hidden initially, T toggle, file tree selection, comment-list focus | PASS |
| Single-line comments, multiline selection and Ctrl+J | PASS |
| Immediate editing after save, comment edit and delete | PASS |
| Default Markdown stdout and stdout delivery menu | PASS |
| XML stdout, save-to-file delivery, archive creation and content | PASS |
| Delivery to another pane on the isolated tmux server | PASS |
| Expansion rows, mouse activation, sticky headers, whole-file expansion and preserved viewport | PASS |
| Deleted and renamed files | PASS |
| Python, Markdown, Java, JavaScript, TypeScript, CSS, HTML, JSX highlighting | PASS |
| SQL, XML, JSON, properties, YAML, Nix, lock JSON, .gitignore highlighting | PASS |
| Terraform .tf, .tfvars, .terraform.lock.hcl highlighting | PASS |
| Initial-menu Ctrl+C, TUI warning, warning clear, double Ctrl+C | PASS |
| Mouse wheel scrolling in normal and far-right columns of a 280x40 pane | PASS |
| Commit/root/last-N pickers and flags, frozen snapshots, 80x16 picker navigation | PASS |
| Stacked source/target ordering, both stack layers, exact hashes, archive branch, unchanged dirty checkout | PASS |
| New tracked/untracked files appear; reverted files disappear or retain comments | PASS |
| Draft cancellation, deleting the last retained comment, empty-review navigation and later discovery | PASS |
| Index-only staging metadata and uncommitted change removal/discovery | PASS |
| Default foreground/background in light and dark terminals, with NO_COLOR set and unset | PASS |
| Context, file tree, editor, resized padding, and empty rows inherit terminal defaults | PASS |
| Comment highlights retain RGB colors; terminal colors restore on exit | PASS |

Wheel checks send the literal SGR events `$'\033[<65;60;10M'` and
`$'\033[<65;260;10M'` with
`tmux -L "$sock" send-keys -t run:0.0 -l`. The captured viewport changes after
each event with the file pane visible.

## Live Rex checks

Exact command:
`python3 tests/live_rex_regression.py --session session:01a1d5bc-5440-7196-a4ed-fa24886cf5ce --nix`

The harness uses the Rex CLI to open background tabs running the local Nix
package against an isolated Git fixture. It changes only those panes' themes,
reproduces the remapped extended palette, inspects HTML captures, and closes its
own tabs. Captures, palette exports, fixtures, and archives from this run are in
`/tmp/review-rex-xklslvey`.

| Scenario | Outcome |
| --- | --- |
| Light terminal with NO_COLOR unset | PASS |
| Dark terminal with NO_COLOR unset | PASS |
| Light terminal with NO_COLOR=1 | PASS |
| Dark terminal with NO_COLOR=1 | PASS |
| Source/target menus, header, context, and editor inherit terminal background | PASS |
| Source menu and ordinary context inherit terminal foreground | PASS |
| Additions stay RGB light green, deletions RGB light red, comments RGB light yellow | PASS |
| Successful exit, primary-screen restoration, and archive creation | PASS |

No required manual/live scenario was skipped. Build output and temporary
fixtures are excluded from the commit.
