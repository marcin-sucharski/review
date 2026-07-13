use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use review::git::{collect_branch_comparison, collect_uncommitted};
use review::model::{FileStatus, LineKind};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempRepo {
    path: PathBuf,
}

impl TempRepo {
    fn new() -> Self {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let count = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "review-rust-test-{}-{stamp}-{count}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        let repo = Self { path };
        repo.git(&["init", "-q", "-b", "main"]);
        repo.git(&["config", "user.email", "review-test@example.invalid"]);
        repo.git(&["config", "user.name", "Review Test"]);
        repo
    }

    fn path(&self) -> &Path {
        &self.path
    }

    fn write(&self, relative: &str, value: impl AsRef<[u8]>) {
        let path = self.path.join(relative);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(path, value).unwrap();
    }

    fn git(&self, arguments: &[&str]) -> String {
        let output = Command::new("git")
            .args(arguments)
            .current_dir(&self.path)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "git {arguments:?}: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        String::from_utf8_lossy(&output.stdout).trim().to_owned()
    }

    fn commit_all(&self, message: &str) {
        self.git(&["add", "-A"]);
        self.git(&["commit", "-q", "-m", message]);
    }
}

impl Drop for TempRepo {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

#[test]
fn unborn_repository_combines_index_and_untracked_final_state() {
    let repo = TempRepo::new();
    repo.write("staged.txt", "index version\n");
    repo.git(&["add", "staged.txt"]);
    repo.write("staged.txt", "final version\n");
    repo.write("untracked.txt", "new file\n");

    let (source, files) = collect_uncommitted(repo.path()).unwrap();
    assert_eq!(source.base_ref, "4b825dc642cb6eb9a060e54bf8d69288fbee4904");
    assert_eq!(
        files
            .iter()
            .map(|file| file.path.as_str())
            .collect::<Vec<_>>(),
        vec!["staged.txt", "untracked.txt"]
    );
    let staged = files.iter().find(|file| file.path == "staged.txt").unwrap();
    assert!(staged.lines.iter().any(|line| line.text == "final version"));
    assert!(!staged.lines.iter().any(|line| line.text == "index version"));
}

#[test]
fn uncommitted_collection_uses_final_worktree_and_includes_untracked() {
    let repo = TempRepo::new();
    repo.write("src/app.rs", "one\ntwo\nthree\n");
    repo.commit_all("base");

    repo.write("src/app.rs", "staged\ntwo\nthree\n");
    repo.git(&["add", "src/app.rs"]);
    repo.write("src/app.rs", "final\ntwo\nthree\n");
    repo.write("empty.txt", "");
    repo.write("notes/new.md", "# untracked\n");

    let (_, files) = collect_uncommitted(repo.path()).unwrap();
    assert_eq!(files.len(), 3);
    let app = files.iter().find(|file| file.path == "src/app.rs").unwrap();
    assert!(
        app.lines
            .iter()
            .any(|line| { line.kind == LineKind::Addition && line.text == "final" })
    );
    assert!(!app.lines.iter().any(|line| line.text == "staged"));
    let untracked = files
        .iter()
        .find(|file| file.path == "notes/new.md")
        .unwrap();
    assert_eq!(untracked.status, FileStatus::Added);
    assert_eq!(untracked.metadata, vec!["Untracked file"]);
    let empty = files.iter().find(|file| file.path == "empty.txt").unwrap();
    assert_eq!(empty.status, FileStatus::Added);
    assert_eq!(empty.metadata, vec!["Untracked file"]);
    assert!(empty.lines.is_empty());
}

#[test]
fn branch_collection_combines_commits_staging_worktree_and_untracked() {
    let repo = TempRepo::new();
    repo.write("committed.rs", "base\n");
    repo.write("mixed.rs", "base\n");
    repo.commit_all("base");
    repo.git(&["switch", "-q", "-c", "feature"]);
    repo.write("committed.rs", "branch commit\n");
    repo.commit_all("feature commit");
    repo.write("mixed.rs", "staged value\n");
    repo.git(&["add", "mixed.rs"]);
    repo.write("mixed.rs", "final worktree value\n");
    repo.write("untracked.json", "{\"ok\": true}\n");

    let (source, files) = collect_branch_comparison(repo.path(), "main").unwrap();
    assert_eq!(source.target_branch.as_deref(), Some("main"));
    assert_eq!(
        files
            .iter()
            .map(|file| file.path.as_str())
            .collect::<Vec<_>>(),
        vec!["committed.rs", "mixed.rs", "untracked.json"]
    );
    let mixed = files.iter().find(|file| file.path == "mixed.rs").unwrap();
    assert!(
        mixed
            .lines
            .iter()
            .any(|line| line.text == "final worktree value")
    );
    assert!(!mixed.lines.iter().any(|line| line.text == "staged value"));
}

#[test]
fn recreated_staged_delete_is_compared_as_the_final_worktree_file() {
    let repo = TempRepo::new();
    repo.write("recreated.txt", "base\n");
    repo.commit_all("base");

    repo.git(&["rm", "-q", "recreated.txt"]);
    repo.write("recreated.txt", "final recreated value\n");

    let (_, files) = collect_uncommitted(repo.path()).unwrap();
    assert_eq!(files.len(), 1);
    assert_eq!(files[0].path, "recreated.txt");
    assert_eq!(files[0].status, FileStatus::Modified);
    assert!(
        files[0]
            .lines
            .iter()
            .any(|line| line.text == "final recreated value")
    );
}

#[test]
fn recreated_source_of_staged_rename_keeps_both_final_paths() {
    let repo = TempRepo::new();
    repo.write("old.txt", "base\n");
    repo.commit_all("base");

    repo.git(&["mv", "old.txt", "new.txt"]);
    repo.write("old.txt", "recreated and modified\n");

    let (_, files) = collect_uncommitted(repo.path()).unwrap();
    assert_eq!(
        files
            .iter()
            .map(|file| (file.path.as_str(), file.status))
            .collect::<Vec<_>>(),
        vec![
            ("new.txt", FileStatus::Added),
            ("old.txt", FileStatus::Modified),
        ]
    );
    assert!(
        files
            .iter()
            .find(|file| file.path == "old.txt")
            .unwrap()
            .lines
            .iter()
            .any(|line| line.text == "recreated and modified")
    );
}

#[test]
fn whitespace_unicode_and_newline_paths_survive_nul_delimited_git_output() {
    let repo = TempRepo::new();
    repo.write("space name.txt", "base\n");
    repo.commit_all("base");

    repo.write("space name.txt", "modified\n");
    repo.write("escape-\x1b[2J.txt", "terminal control path\n");
    repo.write("unicode-zażółć.md", "# Unicode\n");
    repo.write("line\nbreak.txt", "newline path\n");

    let (_, files) = collect_uncommitted(repo.path()).unwrap();
    assert_eq!(
        files
            .iter()
            .map(|file| file.path.as_str())
            .collect::<Vec<_>>(),
        vec![
            "escape-\x1b[2J.txt",
            "line\nbreak.txt",
            "space name.txt",
            "unicode-zażółć.md",
        ]
    );
}

#[test]
fn deleted_renamed_binary_and_mode_only_files_are_safe() {
    let repo = TempRepo::new();
    repo.write("old.txt", "rename me\n");
    repo.write("deleted.txt", "delete me\n");
    repo.write("binary.dat", b"old\0bytes");
    repo.write("mode.sh", "#!/bin/sh\nexit 0\n");
    repo.commit_all("base");

    repo.git(&["mv", "old.txt", "new.txt"]);
    fs::remove_file(repo.path().join("deleted.txt")).unwrap();
    repo.write("binary.dat", b"new\0bytes");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let path = repo.path().join("mode.sh");
        let mut permissions = fs::metadata(&path).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(path, permissions).unwrap();
    }

    let (_, files) = collect_uncommitted(repo.path()).unwrap();
    assert_eq!(
        files
            .iter()
            .find(|file| file.path == "new.txt")
            .unwrap()
            .status,
        FileStatus::Renamed
    );
    assert_eq!(
        files
            .iter()
            .find(|file| file.path == "deleted.txt")
            .unwrap()
            .status,
        FileStatus::Deleted
    );
    assert!(
        files
            .iter()
            .find(|file| file.path == "binary.dat")
            .unwrap()
            .binary
    );
    #[cfg(unix)]
    assert_eq!(
        files
            .iter()
            .find(|file| file.path == "mode.sh")
            .unwrap()
            .status,
        FileStatus::Mode
    );
}

#[cfg(unix)]
#[test]
fn worktree_symlink_is_read_as_target_without_dereferencing() {
    use std::os::unix::fs::symlink;

    let repo = TempRepo::new();
    repo.write("first.txt", "secret first contents\n");
    repo.write("second.txt", "secret second contents\n");
    symlink("first.txt", repo.path().join("link")).unwrap();
    repo.commit_all("base");
    fs::remove_file(repo.path().join("link")).unwrap();
    symlink("second.txt", repo.path().join("link")).unwrap();

    let (_, files) = collect_uncommitted(repo.path()).unwrap();
    let link = files.iter().find(|file| file.path == "link").unwrap();
    assert!(link.lines.iter().any(|line| line.text == "second.txt"));
    assert!(!link.lines.iter().any(|line| line.text.contains("secret")));
}
