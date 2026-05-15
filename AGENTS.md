# Agent Instructions

## Full Regression Testing

Do not call review-tool work complete after unit tests alone. A full regression pass for this repository means all of the following:

1. Run the automated suite in a Pygments-capable Python environment:

   ```sh
   nix shell --impure --expr 'with import <nixpkgs> {}; python3.withPackages (ps: [ ps.pygments ])' -c bash -lc \
     'PYTHONPATH=src python -m compileall src tests && PYTHONPATH=src python -m unittest discover -s tests'
   ```

2. Run a basic CLI smoke:

   ```sh
   PYTHONPATH=src python -m review --help
   ```

3. Run `git diff --check`.

4. Run live TUI regression in real tmux panes, using isolated tmux servers such as `tmux -L review_regression_<id>` so the current user session is not killed or disturbed. Clean up only the isolated server you created.

5. Manual/live tmux coverage must include:

   - startup source menu, including arrow navigation and PR-style review listed before uncommitted review,
   - uncommitted review against real modified files,
   - branch/PR-style review against a target branch, including committed branch changes plus current staged, unstaged, and untracked changes,
   - file pane hidden by default, `T` toggle, file tree selection, comment list focus,
   - single-line comments, multiline comments with `Ctrl+J`, immediate edit after saving, edit/delete behavior,
   - stdout delivery, save-to-file delivery, tmux-pane delivery, and archive creation,
   - Markdown default output and XML output mode,
   - expansion rows and sticky headers,
   - deleted and renamed files,
   - representative syntax-highlighted file types: Python, Markdown, Java, JavaScript, TypeScript, CSS, HTML, JSX, SQL, XML, JSON, properties, YAML, Nix, lock JSON, and `.gitignore`,
   - interrupt behavior: initial-menu `Ctrl+C`, inside-TUI first `Ctrl+C` warning, warning clear, and double `Ctrl+C` quit,
   - mouse support where practical, including wheel scrolling from both normal columns and far-right columns in a wide tmux pane.

6. For mouse regressions, verify the actual TUI under tmux. A useful wide-pane smoke is a `280x40` pane with the file pane visible; inject SGR wheel events such as:

   ```sh
   tmux -L "$sock" send-keys -t "$target" -l $'\033[<65;60;10M'
   tmux -L "$sock" send-keys -t "$target" -l $'\033[<65;260;10M'
   ```

   Confirm the captured viewport changes after both events.

7. After running compile/test commands, remove generated `__pycache__` directories before reporting or committing:

   ```sh
   find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
   ```

8. In the final report, include exact commands and PASS/FAIL outcomes for both automated tests and live tmux scenarios. If any manual scenario is skipped, explain why.
