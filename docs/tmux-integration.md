# Tmux Integration

## Goal

At the end of a review, the tool can send the formatted review comments to a selected tmux pane and press Enter.

This supports workflows where a coding agent is running in another pane and expects feedback as terminal input.

Before tmux delivery is attempted, every non-empty review is saved to the local JSON archive. Delivery failure must not be able to destroy the only copy of the comments.

## Availability Detection

The tmux integration should first determine whether tmux is available:

```bash
tmux -V
```

It should then determine whether the current process is inside tmux by checking the `TMUX` environment variable.

The tool may still list panes when outside tmux if the tmux server is reachable, but the UI should not assume tmux is active.

## Pane Discovery

Recommended pane list command:

```bash
tmux list-panes -a -F '#{pane_id}\t#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_title}\t#{pane_current_command}'
```

The parsed pane model should include:

- `pane_id`, such as `%3`,
- `session_name`,
- `window_index`,
- `pane_index`,
- `pane_title`,
- `pane_current_command`.

The selection UI should display enough information for the user to avoid sending feedback to the wrong pane.

Because delivery happens after the user has already written comments, the delivery target menu requires two consecutive `Ctrl+C` presses to cancel.

Example display:

```text
%3  main:0.1  codex  title="review"
```

## Current Pane Handling

The tool should identify the current pane when possible:

```bash
tmux display-message -p '#{pane_id}'
```

Preferred behavior is to include the current pane in the list but mark it clearly. Sending feedback to the current pane may be allowed, but it should not be the default target.

## No Pane Option

The delivery target selector must include:

```text
No tmux pane - print to stdout
```

This option is required even when tmux is available.

If tmux is unavailable or no panes are found, stdout should be offered automatically.

Choosing stdout still writes the local JSON archive first when the review contains comments.

## Sending Text

The tool should send the formatted review text literally, then press Enter.

Recommended tmux command strategy:

```bash
tmux load-buffer -
tmux paste-buffer -t <pane-id>
tmux send-keys -t <pane-id> Enter
```

Using a buffer is safer for multi-line text than passing a long string through shell arguments.

The implementation must avoid shell interpretation of the review comments.

## Message Boundaries

The sent message should be self-contained.

It should not depend on shell prompts, heredocs, or external files.

The final Enter should submit the message to the target pane. If the target application needs a different submit shortcut, that can be a future option.

## Error Handling

The integration must handle:

- tmux command not found,
- tmux server unavailable,
- no panes,
- selected pane disappearing before send,
- `load-buffer` failure,
- `paste-buffer` failure,
- `send-keys` failure.

On send failure, the tool should show the formatted review text or offer stdout fallback so comments are not lost.

## Testing Strategy

Unit tests should mock command execution and verify:

- pane list parsing,
- current pane detection,
- stdout fallback decisions,
- command construction,
- literal text handling.

Integration tests can run against a real tmux server when available.

Real tmux tests should be skipped automatically when tmux is missing or when the environment cannot create sessions.

## Manual Verification Scenario

1. Create a temporary tmux session with two panes.
2. Run a command in the target pane that visibly captures input, such as an interactive shell or `cat`.
3. Run `review` in the other pane.
4. Add one or more comments.
5. Quit with `:q`.
6. Verify that a JSON archive was written under the XDG data directory.
7. Select the target pane.
8. Verify that the full review text appears in the target pane.
9. Verify that Enter is sent.

## Security Considerations

Review comments are user-authored text and code context. They may contain shell metacharacters.

The tmux integration must treat the message as input text only.

Do not build shell command strings by concatenating the review text.

Use subprocess argument arrays and stdin for buffer loading.
