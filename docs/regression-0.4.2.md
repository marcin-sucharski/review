# Review 0.4.2 dependency and regression report

Review 0.4.2 updates Rust dependencies and the NixOS 26.05 input. All 118 Rust
tests, the release build, the Nix package build, and the full live tmux regression
pass on x86_64 Linux. The flake derives its package version from Cargo.toml so
the application and Nix package share one version source.

## Project and dependency analysis

The project separates Git collection, review state, formatting, persistence,
syntax highlighting, filesystem monitoring, and terminal rendering. All eight
direct dependencies are used. Git and optional tmux delivery are external
processes; syntax grammars are compiled into the native executable. Selected
Arborium grammars and disabled default features keep the enabled functionality
explicit. The dependency updates required no application API changes.

Stable crate versions were checked against the crates.io registry on
2026-11-13. Cargo.toml now requests these versions, and Cargo.lock includes the
updated transitive dependencies:

| Dependency | Previous | Updated | Purpose |
| --- | --- | --- | --- |
| [arborium](https://crates.io/crates/arborium) | 2.18.1 | 2.18.2 | Syntax parsing and highlighting |
| [arborium-theme](https://crates.io/crates/arborium-theme) | 2.18.1 | 2.18.2 | Syntax theme slots |
| [crossterm](https://crates.io/crates/crossterm) | 0.29.0 | 0.29.0 | Terminal rendering and events; already current |
| [notify](https://crates.io/crates/notify) | 8.2.0 | 8.2.2 | Live filesystem monitoring |
| [serde](https://crates.io/crates/serde) | 1.0.228 | 1.0.229 | Archive serialization |
| [serde_json](https://crates.io/crates/serde_json) | 1.0.150 | 1.0.154 | JSON archive encoding and decoding |
| [similar](https://crates.io/crates/similar) | 3.1.1 | 3.2.1 | Patience diff construction |
| [unicode-width](https://crates.io/crates/unicode-width) | 0.2.2 | 0.2.3 | Terminal column measurement |

The lockfile shrinks from 93 to 85 package entries, including the application.
There are no duplicate crate versions across all target platforms. RustSec audit
reports no vulnerabilities. The declared Rust minimum remains 1.88. Cargo
selected notify-types 2.2.0 because 2.3.0 requires Rust 1.91; none of the resolved
packages that declare a minimum Rust version requires more than 1.88. Build and
test validation used Rust 1.95.0 from the updated development shell.

The flake retains the [NixOS 26.05 stable release](https://nixos.org/blog/announcements/2026/nixos-2605/).
Its nixpkgs revision advances from
`569d578509928497eddc3fdbf94a799027050be4` (2026-07-12) to
`ac588ef1c914865dad517b337ea0eb9694a4021c` (2026-11-08). Both supported
architectures evaluate successfully; the package was built and tested on
x86_64 Linux. An aarch64 build was not run on this x86_64 host.

The known-limitations document now reflects existing live discovery of new
changes and distinguishes it from frozen commit and stacked branch snapshots.

## Automated checks

| Exact command | Outcome |
| --- | --- |
| `nix flake update` | PASS: nixpkgs input refreshed |
| `nix develop -c cargo update` | PASS: lockfile refreshed with Rust 1.88 resolution |
| `nix develop -c cargo fmt --all -- --check` | PASS |
| `nix develop -c cargo clippy --all-targets -- -D warnings` | PASS |
| `nix develop -c cargo test --all-targets` | PASS: 90 unit, 7 CLI integration, 21 Git integration tests |
| `nix develop -c cargo build --release` | PASS |
| `target/release/review --help` | PASS |
| `target/release/review --version` | PASS: review 0.4.2 |
| `nix develop -c cargo tree --duplicates --target all` | PASS: no duplicate versions |
| `nix develop -c nix shell --inputs-from . nixpkgs#cargo-audit -c cargo audit` | PASS: no vulnerabilities |
| `nix flake check --all-systems --no-build` | PASS: x86_64-linux and aarch64-linux outputs |
| `nix build --no-link .#review` | PASS: package build, all 118 tests, installation and wrapper |
| `nix run . -- --version` | PASS: review 0.4.2 |
| `nix eval --raw .#packages.x86_64-linux.review.version` | PASS: 0.4.2 |
| `nix eval --raw .#packages.aarch64-linux.review.version` | PASS: 0.4.2 |
| `git diff --check` | PASS |
| `find src tests -type d -name __pycache__ -prune -exec rm -rf {} +` | PASS |

## Live tmux checks

Exact command: `python3 tests/live_tmux_regression.py`

The harness runs target/release/review in actual panes on its own isolated
`review_regression_<PID>` tmux server. It uses a 280x40 pane for wide mouse
coverage and an 80x16 pane for the commit picker, then stops only its own server.
Captures, fixtures, archives, and delivery output from this run are available at
`/tmp/review-live-l0xc1_4z` for local inspection.

| Scenario | Outcome |
| --- | --- |
| Startup source menu, arrow navigation, PR before uncommitted | PASS |
| Uncommitted review against real modified files | PASS |
| PR-style target comparison with committed, staged, unstaged, and untracked changes | PASS |
| File pane hidden initially, T toggle, tree selection, comment-list focus | PASS |
| Single-line comments, multiline selection and Ctrl+J | PASS |
| Immediate editing after save, comment edit and delete | PASS |
| Markdown default stdout and stdout delivery prompt | PASS |
| Save-to-file delivery and archive creation/content | PASS |
| XML stdout | PASS |
| Delivery to another pane on the isolated tmux server | PASS |
| Expansion rows, mouse activation, additional context | PASS |
| Sticky file header | PASS |
| Whole-file expansion with e, preserved viewport, top/middle/bottom context, repeated e | PASS |
| Deleted and renamed files | PASS |
| Python, Markdown, Java, JavaScript, TypeScript, CSS, HTML, JSX highlighting | PASS |
| SQL, XML, JSON, properties, YAML, Nix, lock JSON, .gitignore highlighting | PASS |
| Terraform .tf, .tfvars, .terraform.lock.hcl highlighting | PASS |
| Initial-menu Ctrl+C, first TUI interrupt warning, warning clear, double Ctrl+C quit | PASS |
| Wheel scrolling from normal and far-right columns in a 280x40 pane | PASS |
| Recent-commit picker, root snapshot, last-N prompt, CLI commit flags | PASS |
| Frozen commit snapshot after a working-tree edit | PASS |
| Stacked source/target pickers, branch ordering, both stack layers | PASS |
| Stacked branch names and frozen hashes in output, source branch in archive | PASS |
| Stacked review preserves the unrelated dirty checkout | PASS |
| Live discovery of new tracked/untracked files in PR-style review | PASS |
| Reverted uncommented files disappear and their drafts are cancelled | PASS |
| Reverted commented files stay until their last comment is deleted | PASS |
| Empty-review navigation and discovery of later changes | PASS |
| Index-only staging changes refresh metadata | PASS |
| Uncommitted review removes vanished changes and discovers later files | PASS |
| Twenty long commit subjects in an 80x16 picker, End/Home navigation and selection | PASS |

The mouse wheel checks invoke `tmux -L "$sock" send-keys -t run:0.0 -l`
with the literal SGR events `$'\033[<65;60;10M'` and
`$'\033[<65;260;10M'`. Each event changes the captured viewport with the file
pane visible.

No required manual/live scenario was skipped. Build output and temporary
regression fixtures are excluded from the commit.
