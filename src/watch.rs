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
    git_dirs: Vec<PathBuf>,
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
        let root = root
            .canonicalize()
            .map_err(|error| ReviewError::io("could not resolve watched directory", error))?;
        let git_dirs = git_metadata_directories(&root);
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
            .watch(&root, RecursiveMode::Recursive)
            .map_err(|error| {
                ReviewError::Message(format!("could not monitor {}: {error}", root.display()))
            })?;
        for directory in &git_dirs {
            if !directory.starts_with(&root) {
                watcher
                    .watch(directory, RecursiveMode::NonRecursive)
                    .map_err(|error| {
                        ReviewError::Message(format!(
                            "could not monitor {}: {error}",
                            directory.display()
                        ))
                    })?;
                let refs = directory.join("refs");
                if refs.is_dir() {
                    watcher
                        .watch(&refs, RecursiveMode::Recursive)
                        .map_err(|error| {
                            ReviewError::Message(format!(
                                "could not monitor {}: {error}",
                                refs.display()
                            ))
                        })?;
                }
            }
        }
        Ok(Self {
            root,
            git_dirs,
            _watcher: watcher,
            receiver,
            pending_paths: HashSet::new(),
            refresh_all: false,
            warning: None,
            deadline: None,
            disconnected: false,
        })
    }

    pub fn poll(&mut self, _reviewed_paths: &[String]) -> Option<MonitorBatch> {
        while !self.disconnected {
            match self.receiver.try_recv() {
                Ok(MonitorMessage::Event(event)) => self.record_event(event),
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

    fn record_event(&mut self, event: Event) {
        if matches!(event.kind, EventKind::Access(_)) {
            return;
        }
        if event.need_rescan() {
            self.refresh_all = true;
            self.deadline = Some(Instant::now() + DEBOUNCE);
        }
        let mut relevant = false;
        for path in event.paths {
            if let Some(relative) = self
                .git_dirs
                .iter()
                .find_map(|directory| path.strip_prefix(directory).ok())
            {
                if relevant_git_metadata(relative) {
                    self.refresh_all = true;
                    relevant = true;
                }
                continue;
            }
            let Some(relative) = self.relative_path(&path) else {
                continue;
            };
            if relative == ".git" {
                self.refresh_all = true;
            } else if relative.starts_with(".git/") {
                continue;
            } else {
                self.pending_paths.insert(relative);
            }
            relevant = true;
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

fn git_metadata_directories(root: &Path) -> Vec<PathBuf> {
    let marker = root.join(".git");
    let directory = if marker.is_dir() {
        marker
    } else if let Ok(contents) = std::fs::read_to_string(&marker) {
        let Some(path) = contents.trim().strip_prefix("gitdir: ") else {
            return Vec::new();
        };
        root.join(path)
    } else {
        return Vec::new();
    };
    let Ok(directory) = directory.canonicalize() else {
        return Vec::new();
    };
    let mut directories = vec![directory.clone()];
    if let Ok(common) = std::fs::read_to_string(directory.join("commondir"))
        && let Ok(common) = directory.join(common.trim()).canonicalize()
        && !directories.contains(&common)
    {
        directories.push(common);
    }
    directories
}

fn relevant_git_metadata(path: &Path) -> bool {
    if path
        .components()
        .any(|part| part.as_os_str().to_string_lossy().ends_with(".lock"))
    {
        return false;
    }
    path == Path::new("index")
        || path == Path::new("HEAD")
        || path == Path::new("packed-refs")
        || path == Path::new("info/exclude")
        || path == Path::new("config")
        || path == Path::new("config.worktree")
        || path.starts_with("refs")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    #[test]
    fn metadata_filter_ignores_git_feedback_and_locks() {
        for path in [
            "index",
            "HEAD",
            "packed-refs",
            "refs/heads/topic",
            "info/exclude",
            "config",
            "config.worktree",
        ] {
            assert!(relevant_git_metadata(Path::new(path)), "{path}");
        }
        for path in [
            "index.lock",
            "refs/heads/topic.lock",
            "objects/ab/cd",
            "logs/HEAD",
            "",
        ] {
            assert!(!relevant_git_metadata(Path::new(path)), "{path}");
        }
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
    struct Repository(PathBuf);

    impl Repository {
        fn new() -> Self {
            let directory = std::env::temp_dir().join(format!(
                "review-discovery-watch-{}-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos(),
                COUNTER.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&directory).unwrap();
            let repository = Self(directory);
            repository.git(&["init", "-q", "-b", "main"]);
            fs::write(repository.0.join("tracked.txt"), "before\n").unwrap();
            repository.git(&["add", "."]);
            repository.git(&[
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "base",
            ]);
            repository
        }

        fn git(&self, args: &[&str]) {
            let output = std::process::Command::new("git")
                .current_dir(&self.0)
                .args(args)
                .output()
                .unwrap();
            assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stderr)
            );
        }
    }

    impl Drop for Repository {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn await_batch(
        monitor: &mut FileMonitor,
        matches: impl Fn(&MonitorBatch) -> bool,
    ) -> MonitorBatch {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if let Some(batch) = monitor.poll(&["already-reviewed.txt".to_owned()])
                && matches(&batch)
            {
                return batch;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        panic!("expected watcher event before timeout");
    }

    #[test]
    fn real_watcher_discovers_new_unreviewed_and_ignore_files() {
        let repository = Repository::new();
        let mut monitor = FileMonitor::new(&repository.0).unwrap();
        for name in ["new.txt", "tracked.txt", ".gitignore"] {
            fs::write(repository.0.join(name), "changed\n").unwrap();
            await_batch(&mut monitor, |batch| batch.paths.contains(name));
        }
        fs::remove_file(repository.0.join("new.txt")).unwrap();
        await_batch(&mut monitor, |batch| batch.paths.contains("new.txt"));
    }

    #[test]
    fn real_watcher_reconciles_index_only_change_in_linked_worktree() {
        let repository = Repository::new();
        let checkout = repository.0.join("checkout");
        repository.git(&[
            "worktree",
            "add",
            "-qb",
            "linked",
            checkout.to_str().unwrap(),
        ]);
        let mut monitor = FileMonitor::new(&checkout).unwrap();
        let output = std::process::Command::new("git")
            .current_dir(&checkout)
            .args(["update-index", "--chmod=+x", "tracked.txt"])
            .output()
            .unwrap();
        assert!(output.status.success());
        let batch = await_batch(&mut monitor, |batch| batch.refresh_all);
        assert!(
            batch.paths.is_empty(),
            "index-only event should not imply a worktree edit"
        );
        assert_eq!(
            fs::read_to_string(checkout.join("tracked.txt")).unwrap(),
            "before\n"
        );
        repository.git(&["pack-refs", "--all"]);
        await_batch(&mut monitor, |batch| batch.refresh_all);
    }
}
