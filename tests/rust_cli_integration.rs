use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct TempDir(PathBuf);

impl TempDir {
    fn new(label: &str) -> Self {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let count = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "review-cli-{label}-{}-{stamp}-{count}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }

    fn git(&self, arguments: &[&str]) {
        let output = Command::new("git")
            .args(arguments)
            .current_dir(&self.0)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "git {arguments:?}: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    fn init_repo(&self) {
        self.git(&["init", "-q", "-b", "main"]);
        self.git(&["config", "user.email", "review-test@example.invalid"]);
        self.git(&["config", "user.name", "Review Test"]);
        self.git(&["commit", "-q", "--allow-empty", "-m", "base"]);
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_review")
}

#[test]
fn help_and_version_do_not_require_a_repository() {
    let directory = TempDir::new("help");
    let help = Command::new(binary())
        .arg("--help")
        .current_dir(directory.path())
        .output()
        .unwrap();
    assert!(help.status.success());
    let help = String::from_utf8(help.stdout).unwrap();
    assert!(help.contains("--source <uncommitted|branch|commit|commits>"));
    assert!(help.contains("review <COMMAND>"));

    let version = Command::new(binary())
        .arg("--version")
        .current_dir(directory.path())
        .output()
        .unwrap();
    assert!(version.status.success());
    assert_eq!(
        String::from_utf8(version.stdout).unwrap(),
        concat!("review ", env!("CARGO_PKG_VERSION"), "\n")
    );
}

#[test]
fn outside_git_fails_with_clear_error() {
    let directory = TempDir::new("outside");
    let output = Command::new(binary())
        .args(["--source", "uncommitted", "--no-tui"])
        .current_dir(directory.path())
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&output.stderr).contains("not a Git repository"));
}

#[test]
fn clean_repository_exits_successfully_without_opening_tui() {
    let directory = TempDir::new("clean");
    directory.init_repo();
    let output = Command::new(binary())
        .args(["--source", "uncommitted"])
        .current_dir(directory.path())
        .output()
        .unwrap();
    assert!(output.status.success());
    assert!(String::from_utf8_lossy(&output.stdout).contains("no uncommitted changes found"));
}

#[test]
fn no_tui_smoke_collects_real_changes() {
    let directory = TempDir::new("no-tui");
    directory.init_repo();
    fs::write(directory.path().join("new.rs"), "fn main() {}\n").unwrap();
    let output = Command::new(binary())
        .args([
            "--source",
            "uncommitted",
            "--no-tui",
            "--stdout",
            "--output-format",
            "xml",
        ])
        .current_dir(directory.path())
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        "No review comments.\n"
    );
}

#[test]
fn history_commands_work_outside_git_and_keep_menu_off_stdout() {
    let directory = TempDir::new("history");
    let archive_dir = directory.path().join("review/reviews");
    fs::create_dir_all(&archive_dir).unwrap();
    let message = "# Review comments\n\nA saved message.\n";
    let payload = serde_json::json!({
        "path": "/example/repository",
        "branch": "feature",
        "review_message": message,
    });
    fs::write(
        archive_dir.join("9999999999-000000000-1-0.json"),
        serde_json::to_vec_pretty(&payload).unwrap(),
    )
    .unwrap();

    let listed = Command::new(binary())
        .arg("ls")
        .env("XDG_DATA_HOME", directory.path())
        .current_dir(directory.path())
        .output()
        .unwrap();
    assert!(listed.status.success());
    assert!(String::from_utf8_lossy(&listed.stdout).contains("feature  /example/repository"));

    let mut child = Command::new(binary())
        .arg("display")
        .env("XDG_DATA_HOME", directory.path())
        .current_dir(directory.path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.as_mut().unwrap().write_all(b"1\n").unwrap();
    drop(child.stdin.take());
    let displayed = child.wait_with_output().unwrap();
    assert!(displayed.status.success());
    assert_eq!(String::from_utf8(displayed.stdout).unwrap(), message);
    assert!(String::from_utf8_lossy(&displayed.stderr).contains("Saved reviews"));
}

#[test]
fn sequential_text_prompts_preserve_count_and_commit_selection() {
    let directory = TempDir::new("sequential-prompts");
    directory.init_repo();
    fs::write(directory.path().join("file.txt"), "changed\n").unwrap();
    directory.git(&["add", "."]);
    directory.git(&["commit", "-q", "-m", "change"]);
    for (answers, expected) in [
        ("4\n2\n", "No review comments."),
        // The second commit is the empty root commit. Choosing the default instead
        // would review the nonempty newest commit and return a different message.
        ("3\n2\n", "no changes found in selected commits"),
    ] {
        let mut child = Command::new(binary())
            .args(["--no-tui", "--stdout"])
            .current_dir(directory.path())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        child
            .stdin
            .take()
            .unwrap()
            .write_all(answers.as_bytes())
            .unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(String::from_utf8_lossy(&output.stdout).contains(expected));
    }
}
