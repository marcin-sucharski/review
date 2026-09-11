# Review 0.4.1 regression report

Fixed live changeset reconciliation for PR-style and uncommitted reviews.
Refresh now reconciles the complete Git diff inventory, removes unchanged files
without comments, retains commented files, and inserts newly changed files.
The watcher observes previously unreviewed paths and relevant Git metadata,
including index changes in linked worktrees. Static commit/stacked snapshots
remain frozen.

State handling preserves unrelated selections when files enter/leave the list.
An empty review stays open for future changes; file-pane navigation is safe.
Draft comments are cancelled if their file disappears. Removing the last saved
comment also removes a retained unchanged file.

Independent review identified UI visibility intervals causing unnecessary
refreshes after expanding context. Semantic comparison now excludes those
intervals, with a regression test using an ignored-file event. Git reads set
GIT_OPTIONAL_LOCKS=0 to avoid index-write feedback. Re-review found no additional
material issues.

New tests cover full inventory reconciliation, comment retention/removal,
selection stability, empty states, duplicate-content rename identity, watcher
discovery, linked-worktree metadata, and UI-only expansion. Version is 0.4.1 in
Cargo.toml, Cargo.lock, and flake.nix.

## Automated checks

| Exact command | Final outcome |
| --- | --- |
| `nix develop -c cargo fmt --all -- --check` | PASS |
| `nix develop -c cargo clippy --all-targets -- -D warnings` | PASS |
| `nix develop -c cargo test --all-targets` | PASS: 90 unit, 7 CLI integration, 21 Git integration tests |
| `nix develop -c cargo build --release` | PASS |
| `target/release/review --help` | PASS |
| `target/release/review --version` | PASS: review 0.4.1 |
| `nix eval --raw .#packages.x86_64-linux.review.version` | PASS: 0.4.1 |
| `git diff --check` | PASS |
| `find src tests -type d -name __pycache__ -prune -exec rm -rf {} +` | PASS |

The Git tests include shallow-history failure and successful deepening, missing
old/new blobs, root and merge commits, combined first-parent ranges, renames,
deletions, and exclusion of working-tree changes. CLI tests exercise sequential
piped answers and invalid/conflicting arguments. Menu tests cover wrapped labels
in small panes.

## Live tmux checks

Exact command: `python tests/live_tmux_regression.py`

The harness runs the release binary in real panes on an isolated
`review_regression_<PID>` tmux server. It uses 280x40 for wide-pane coverage and
80x16 for the commit picker. It unsets inherited NO_COLOR to inspect actual
source colors. It stops only the server it creates and prints a temporary
artifact directory containing captures, archives, delivery output, and fixtures.

| Scenario | Final outcome |
| --- | --- |
| Startup menu, arrows, PR before uncommitted | PASS |
| Uncommitted review of real modified files | PASS |
| PR-style comparison: committed, staged, unstaged, untracked content | PASS |
| File pane hidden, T toggle, file selection, comment-list focus | PASS |
| Single-line comments, multiline selection and Ctrl+J | PASS |
| Immediate edit after save, edit/delete behavior | PASS |
| Markdown stdout and stdout delivery menu | PASS |
| Save-to-file delivery and archive creation/content | PASS |
| XML stdout | PASS |
| Delivery to another tmux pane | PASS |
| Expansion rows, mouse activation, additional context | PASS |
| Sticky header in the top review column | PASS |
| `e` reveals the current file, preserves viewport, and exposes top/middle/bottom context | PASS |
| Repeated `e` is safe | PASS |
| Deleted and renamed files | PASS |
| Python, Markdown, Java, JavaScript, TypeScript, CSS, HTML, JSX | PASS |
| SQL, XML, JSON, properties, YAML, Nix, lock JSON, .gitignore | PASS |
| Terraform .tf, .tfvars, .terraform.lock.hcl source colors | PASS |
| Menu Ctrl+C, TUI warning, warning clear, double Ctrl+C | PASS |
| Wheel scrolling from normal and far-right columns | PASS |
| Recent-commit picker and selected root hash in review output | PASS |
| Last-N menu/count prompt and source in output | PASS |
| Commit/last CLI flags and frozen snapshot after a working-tree edit | PASS |
| Twenty long commit subjects in 80x16; End/Home and selection | PASS |
| New tracked/untracked files appear in an open PR-style review | PASS |
| Reverted uncommented PR file disappears; its draft is cancelled | PASS |
| Reverted commented file remains; deleting its last comment removes it | PASS |
| Empty review supports navigation and discovers new files | PASS |
| Index-only staging updates metadata | PASS |
| Uncommitted reviews remove vanished changes and discover later files | PASS |
| Stacked source picker then target picker, with existing branch ordering | PASS |
| Second stacked branch against first branch, first branch against master | PASS |
| Markdown source/target branch names and frozen commit IDs | PASS |
| Stacked archive uses source branch; unrelated dirty checkout unchanged | PASS |

The wheel checks call `tmux -L "$sock" send-keys -t run:0.0 -l` with the literal
SGR events `$'\033[<65;60;10M'` and `$'\033[<65;260;10M'` and verify a viewport
change after each event. The script supplies these as argument strings, without
shell expansion.

No required manual/live scenario was skipped.
