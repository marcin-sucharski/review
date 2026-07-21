# review

`review` is a fast terminal interface for reviewing local Git changes and writing line-level feedback. It is a native Rust executable with no runtime language environment.

It can review the combined final state of staged, unstaged, and untracked changes, or compare the current branch and working tree with a target branch. While the TUI is open, reviewed files are monitored and their diffs reload automatically; comments follow exact matching ranges and remain near their previous rows when code is rewritten. Reviews can be printed as Markdown or XML, saved as Markdown, sent to a tmux pane, and reopened from the local review archive.

## Install

With Nix:

```sh
nix run . -- --help
nix profile install .
```

With a Rust 1.88 or newer toolchain and a C compiler for the tree-sitter grammars:

```sh
cargo install --path .
```

The executable invokes `git` to collect repository state and `tmux` only when tmux delivery is selected.

## Use

Run `review` inside a Git worktree and choose PR-style or uncommitted review. The source and target can also be supplied directly:

```sh
review
review --source uncommitted
review --source branch --target main
review --source branch --target main --output-format xml --stdout
```

The file pane is hidden initially; press `T` to show it. Use arrows or `j`/`k` to move, Shift+arrow to select a range, Enter to comment, and Ctrl+J for a newline in a comment. Press Enter on a saved inline comment to edit it, or Backspace/Delete to remove it. Search with `/`, cycle matches with `n`/`p`, and quit with `:q`.

Every completed non-empty review is archived under `$XDG_DATA_HOME/review/reviews`, falling back to `~/.local/share/review/reviews`:

```sh
review ls
review display
review display --file
```

## Develop

```sh
nix develop
cargo fmt --all -- --check
cargo clippy --all-targets -- -D warnings
cargo test --all-targets
cargo build --release
```

See [the documentation map](docs/README.md), especially the [testing scenarios](docs/testing-scenarios.md) and [architecture](docs/architecture.md).
