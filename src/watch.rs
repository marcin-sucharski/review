use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Receiver, TryRecvError};
use std::time::{Duration, Instant};

use notify::{Config, Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};

use crate::error::{Result, ReviewError};

const DEBOUNCE: Duration = Duration::from_millis(150);

enum MonitorMessage {
    Event(Event),
    Error(String),
}

#[derive(Debug, Default, Eq, PartialEq)]
pub struct MonitorBatch {
    pub paths: HashSet<String>,
    pub refresh_all: bool,
    pub warning: Option<String>,
}

pub struct FileMonitor {
    root: PathBuf,
    _watcher: RecommendedWatcher,
    receiver: Receiver<MonitorMessage>,
    pending_paths: HashSet<String>,
    refresh_all: bool,
    warning: Option<String>,
    deadline: Option<Instant>,
    disconnected: bool,
}

impl FileMonitor {
    pub fn new(root: &Path) -> Result<Self> {
        let (sender, receiver) = mpsc::channel();
        let mut watcher = notify::recommended_watcher(move |result: notify::Result<Event>| {
            let message = match result {
                Ok(event) => MonitorMessage::Event(event),
                Err(error) => MonitorMessage::Error(error.to_string()),
            };
            let _ = sender.send(message);
        })
        .map_err(|error| {
            ReviewError::Message(format!("could not start file monitoring: {error}"))
        })?;
        let _ = watcher.configure(Config::default().with_follow_symlinks(false));
        watcher
            .watch(root, RecursiveMode::Recursive)
            .map_err(|error| {
                ReviewError::Message(format!("could not monitor {}: {error}", root.display()))
            })?;
        Ok(Self {
            root: root.to_path_buf(),
            _watcher: watcher,
            receiver,
            pending_paths: HashSet::new(),
            refresh_all: false,
            warning: None,
            deadline: None,
            disconnected: false,
        })
    }

    pub fn poll(&mut self, reviewed_paths: &[String]) -> Option<MonitorBatch> {
        while !self.disconnected {
            match self.receiver.try_recv() {
                Ok(MonitorMessage::Event(event)) => self.record_event(event, reviewed_paths),
                Ok(MonitorMessage::Error(error)) => {
                    self.refresh_all = true;
                    self.warning = Some(format!("File monitor warning: {error}"));
                    self.deadline = Some(Instant::now() + DEBOUNCE);
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    self.warning = Some("File monitoring stopped unexpectedly.".to_owned());
                    self.refresh_all = true;
                    self.deadline = Some(Instant::now());
                    self.disconnected = true;
                    break;
                }
            }
        }
        let ready = self
            .deadline
            .is_some_and(|deadline| Instant::now() >= deadline);
        if !ready {
            return None;
        }
        self.deadline = None;
        Some(MonitorBatch {
            paths: std::mem::take(&mut self.pending_paths),
            refresh_all: std::mem::take(&mut self.refresh_all),
            warning: self.warning.take(),
        })
    }

    fn record_event(&mut self, event: Event, reviewed_paths: &[String]) {
        if matches!(event.kind, EventKind::Access(_)) {
            return;
        }
        if event.need_rescan() {
            self.refresh_all = true;
            self.deadline = Some(Instant::now() + DEBOUNCE);
        }
        let mut relevant = false;
        for path in event.paths {
            let Some(relative) = self.relative_path(&path) else {
                continue;
            };
            if relative == ".git" || relative.starts_with(".git/") {
                continue;
            }
            if reviewed_paths
                .iter()
                .any(|reviewed| paths_overlap(reviewed, &relative))
            {
                self.pending_paths.insert(relative);
                relevant = true;
            }
        }
        if relevant {
            self.deadline = Some(Instant::now() + DEBOUNCE);
        }
    }

    fn relative_path(&self, path: &Path) -> Option<String> {
        path.strip_prefix(&self.root)
            .ok()
            .map(|relative| relative.to_string_lossy().replace('\\', "/"))
    }
}

fn paths_overlap(left: &str, right: &str) -> bool {
    left == right
        || left
            .strip_prefix(right)
            .is_some_and(|suffix| suffix.starts_with('/'))
        || right
            .strip_prefix(left)
            .is_some_and(|suffix| suffix.starts_with('/'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    #[test]
    fn overlap_is_path_component_aware() {
        assert!(paths_overlap("src/a.rs", "src/a.rs"));
        assert!(paths_overlap("src/a.rs", "src"));
        assert!(!paths_overlap("src/a.rs", "src/a.rs.tmp"));
        assert!(!paths_overlap("src/a.rs", ".git/index"));
    }

    #[test]
    fn real_watcher_reports_reviewed_file_change() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let count = COUNTER.fetch_add(1, Ordering::Relaxed);
        let root = std::env::temp_dir().join(format!(
            "review-watch-test-{}-{stamp}-{count}",
            std::process::id()
        ));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("watched.txt"), "before\n").unwrap();
        let mut monitor = FileMonitor::new(&root).unwrap();
        fs::write(root.join("watched.txt"), "after\n").unwrap();

        let deadline = Instant::now() + Duration::from_secs(5);
        let mut batch = None;
        while Instant::now() < deadline {
            if let Some(value) = monitor.poll(&["watched.txt".to_owned()]) {
                batch = Some(value);
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let _ = fs::remove_dir_all(&root);

        let batch = batch.expect("watch event should arrive before timeout");
        assert!(batch.paths.contains("watched.txt"));
    }
}
