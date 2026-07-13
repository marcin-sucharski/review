use std::env;
use std::io::Write;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};

use crate::error::{Result, ReviewError};

static BUFFER_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TmuxPane {
    pub pane_id: String,
    pub session_name: String,
    pub window_index: String,
    pub pane_index: String,
    pub pane_title: String,
    pub current_command: String,
    pub current: bool,
}

impl TmuxPane {
    #[must_use]
    pub fn location(&self) -> String {
        format!(
            "{}:{}.{}",
            self.session_name, self.window_index, self.pane_index
        )
    }

    #[must_use]
    pub fn display(&self) -> String {
        let title = if self.pane_title.is_empty() {
            "(no title)"
        } else {
            &self.pane_title
        };
        let command = if self.current_command.is_empty() {
            "unknown"
        } else {
            &self.current_command
        };
        let marker = if self.current { " [current]" } else { "" };
        format!(
            "{}  {}  {command}  title=\"{title}\"{marker}",
            self.pane_id,
            self.location()
        )
    }
}

#[must_use]
pub fn inside_tmux() -> bool {
    env::var_os("TMUX").is_some_and(|value| !value.is_empty())
}

#[must_use]
pub fn parse_panes(output: &str, current_pane_id: Option<&str>) -> Vec<TmuxPane> {
    output
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            let mut parts = line.split('\t');
            let pane_id = parts.next().unwrap_or_default().to_owned();
            TmuxPane {
                current: current_pane_id.is_some_and(|current| current == pane_id),
                pane_id,
                session_name: parts.next().unwrap_or_default().to_owned(),
                window_index: parts.next().unwrap_or_default().to_owned(),
                pane_index: parts.next().unwrap_or_default().to_owned(),
                pane_title: parts.next().unwrap_or_default().to_owned(),
                current_command: parts.next().unwrap_or_default().to_owned(),
            }
        })
        .collect()
}

pub fn list_panes() -> Result<Vec<TmuxPane>> {
    let current = env::var("TMUX_PANE")
        .ok()
        .filter(|pane| !pane.is_empty())
        .or_else(|| {
            Command::new("tmux")
                .args(["display-message", "-p", "#{pane_id}"])
                .output()
                .ok()
                .filter(|output| output.status.success())
                .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
                .filter(|pane| !pane.is_empty())
        });
    let output = Command::new("tmux")
        .args([
            "list-panes",
            "-a",
            "-F",
            "#{pane_id}\t#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_title}\t#{pane_current_command}",
        ])
        .output()
        .map_err(|error| ReviewError::io("tmux is not available", error))?;
    if !output.status.success() {
        let message = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        return Err(ReviewError::Message(if message.is_empty() {
            "tmux panes could not be listed".to_owned()
        } else {
            message
        }));
    }
    Ok(parse_panes(
        &String::from_utf8_lossy(&output.stdout),
        current.as_deref(),
    ))
}

pub fn send_text(pane_id: &str, text: &str) -> Result<()> {
    let counter = BUFFER_COUNTER.fetch_add(1, Ordering::Relaxed);
    let buffer = format!("review-{}-{counter}", std::process::id());
    let mut load = Command::new("tmux")
        .args(["load-buffer", "-b", &buffer, "-"])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| ReviewError::io("tmux is not available", error))?;
    if let Some(stdin) = load.stdin.as_mut() {
        stdin
            .write_all(text.as_bytes())
            .map_err(|error| ReviewError::io("tmux load-buffer failed", error))?;
    }
    let load_output = load
        .wait_with_output()
        .map_err(|error| ReviewError::io("tmux load-buffer failed", error))?;
    if !load_output.status.success() {
        return Err(tmux_error("tmux load-buffer failed", &load_output.stderr));
    }
    let paste = Command::new("tmux")
        .args(["paste-buffer", "-d", "-b", &buffer, "-t", pane_id])
        .output()
        .map_err(|error| ReviewError::io("tmux paste-buffer failed", error))?;
    if !paste.status.success() {
        let _ = Command::new("tmux")
            .args(["delete-buffer", "-b", &buffer])
            .status();
        return Err(tmux_error("tmux paste-buffer failed", &paste.stderr));
    }
    let enter = Command::new("tmux")
        .args(["send-keys", "-t", pane_id, "Enter"])
        .output()
        .map_err(|error| ReviewError::io("tmux send-keys failed", error))?;
    if !enter.status.success() {
        return Err(tmux_error("tmux send-keys failed", &enter.stderr));
    }
    Ok(())
}

fn tmux_error(fallback: &str, stderr: &[u8]) -> ReviewError {
    let message = String::from_utf8_lossy(stderr).trim().to_owned();
    ReviewError::Message(if message.is_empty() {
        fallback.to_owned()
    } else {
        message
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pane_parser_marks_current_and_preserves_titles() {
        let panes = parse_panes(
            "%1\ts\t0\t1\tEditor\tnvim\n%2\ts\t0\t2\tAgent\tbash\n",
            Some("%2"),
        );
        assert_eq!(panes.len(), 2);
        assert!(!panes[0].current);
        assert!(panes[1].current);
        assert!(panes[1].display().contains("[current]"));
    }
}
