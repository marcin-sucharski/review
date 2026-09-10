# Review 0.3.0 regression report

Three independent subagents reviewed Git collection, CLI/menu behavior, and
highlighting/output/packaging. Four confirmed code findings were fixed:

| Finding | Resolution |
| --- | --- |
| P1: shallow boundary treated as a root commit | Read actual commit parents and report unavailable history with fetch/deepen guidance. |
| P2: snapshot blob read failures silently become empty files | Propagate Git errors for required snapshot content. |
| P2: recent-commit menu overflows short terminals | Bound the menu by physical wrapped rows and keep the selection visible. |
| P2: text menu consumes subsequent piped answers | Read one answer line per prompt. |

The version is 0.3.0 in Cargo.toml, Cargo.lock, and flake.nix. The version test
uses Cargo's package version. Terraform highlighting had no confirmed defects.
A reported mouse-escape issue in the regression harness was rejected after
checking that the injected strings begin with byte 27 (ESC). Accepted harness
assertion gaps were fixed before the final live run.

## Automated checks

| Exact command | Final outcome |
| --- | --- |
| `nix develop -c cargo fmt --all -- --check` | PASS |
| `nix develop -c cargo clippy --all-targets -- -D warnings` | PASS |
| `nix develop -c cargo test --all-targets` | PASS: 76 unit, 6 CLI integration, 15 Git integration tests |
| `nix develop -c cargo build --release` | PASS |
| `target/release/review --help` | PASS |
| `target/release/review --version` | PASS: review 0.3.0 |
| `nix eval --raw .#packages.x86_64-linux.review.version` | PASS: 0.3.0 |
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

The wheel checks call `tmux -L "$sock" send-keys -t run:0.0 -l` with the literal
SGR events `$'\033[<65;60;10M'` and `$'\033[<65;260;10M'` and verify a viewport
change after each event. The script supplies these as argument strings, without
shell expansion.

No required manual/live scenario was skipped. Preliminary harness runs exposed
assertion/fixture weaknesses; final outcomes above refer to the complete run
with source-only color checks and explicit pane/snapshot assertions.
