use std::collections::{HashMap, HashSet};
use std::ffi::{OsStr, OsString};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use crate::error::{Result, ReviewError};
use crate::model::{FileStatus, ReviewFile, ReviewKind, ReviewSource, create_review_file};

const EMPTY_TREE: &str = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NameStatus {
    pub status: char,
    pub path: String,
    pub old_path: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FileRefresh {
    pub old_path: String,
    pub file: Option<ReviewFile>,
    pub physically_deleted: bool,
}

fn git_output<I, S>(root: &Path, args: I, check: bool) -> Result<Output>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let args = args
        .into_iter()
        .map(|arg| arg.as_ref().to_os_string())
        .collect::<Vec<_>>();
    let output = Command::new("git")
        .args(&args)
        .current_dir(root)
        .output()
        .map_err(|error| ReviewError::io("could not run git", error))?;
    if check && !output.status.success() {
        let command = display_command(&args);
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        return Err(ReviewError::Git {
            command,
            status: output.status.code(),
            message: if stderr.is_empty() {
                "Git command failed".to_owned()
            } else {
                stderr
            },
        });
    }
    Ok(output)
}

fn display_command(args: &[OsString]) -> String {
    let mut command = "git".to_owned();
    for arg in args {
        command.push(' ');
        command.push_str(&arg.to_string_lossy());
    }
    command
}

pub fn repository_root(start: &Path) -> Result<PathBuf> {
    let output = git_output(start, ["rev-parse", "--show-toplevel"], false)?;
    if !output.status.success() {
        return Err(ReviewError::NotGitRepository);
    }
    let root = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    if root.is_empty() {
        return Err(ReviewError::NotGitRepository);
    }
    Ok(PathBuf::from(root))
}

pub fn current_branch(root: &Path) -> Result<String> {
    let branch = git_output(root, ["branch", "--show-current"], false)?;
    let branch_name = String::from_utf8_lossy(&branch.stdout).trim().to_owned();
    if branch.status.success() && !branch_name.is_empty() {
        return Ok(branch_name);
    }
    let commit = git_output(root, ["rev-parse", "--short", "HEAD"], false)?;
    let commit_name = String::from_utf8_lossy(&commit.stdout).trim().to_owned();
    if commit.status.success() && !commit_name.is_empty() {
        return Ok(format!("detached:{commit_name}"));
    }
    Ok("unknown".to_owned())
}

fn has_head(root: &Path) -> Result<bool> {
    Ok(git_output(root, ["rev-parse", "--verify", "HEAD"], false)?
        .status
        .success())
}

pub fn default_branch_candidates(root: &Path) -> Result<Vec<String>> {
    let output = git_output(
        root,
        [
            "for-each-ref",
            "--format=%(refname:short)\t%(committerdate:unix)",
            "refs/heads",
            "refs/remotes",
        ],
        false,
    )?;
    let mut dates = HashMap::new();
    if output.status.success() {
        for line in String::from_utf8_lossy(&output.stdout).lines() {
            let (name, timestamp) = line.split_once('\t').unwrap_or((line, "0"));
            if name.is_empty() || name == "HEAD" || name.ends_with("/HEAD") {
                continue;
            }
            dates.insert(name.to_owned(), timestamp.parse::<i64>().unwrap_or(0));
        }
    }
    let mut branches = dates.keys().cloned().collect::<Vec<_>>();
    branches.sort_by(|left, right| {
        branch_priority(left)
            .cmp(&branch_priority(right))
            .then_with(|| dates.get(right).cmp(&dates.get(left)))
            .then_with(|| left.cmp(right))
    });
    Ok(branches)
}

fn branch_priority(branch: &str) -> u8 {
    if branch == "origin/master" {
        0
    } else if branch == "master" {
        1
    } else if branch == "main" {
        2
    } else if branch.ends_with("/master") {
        3
    } else if branch.ends_with("/main") {
        4
    } else {
        5
    }
}

pub fn collect_uncommitted(root: &Path) -> Result<(ReviewSource, Vec<ReviewFile>)> {
    let base = if has_head(root)? {
        let output = git_output(root, ["rev-parse", "HEAD"], true)?;
        String::from_utf8_lossy(&output.stdout).trim().to_owned()
    } else {
        EMPTY_TREE.to_owned()
    };
    let files = collect_worktree_files(root, &base)?;
    if files.is_empty() {
        return Err(ReviewError::NoChanges(
            "no uncommitted changes found".to_owned(),
        ));
    }
    Ok((
        ReviewSource {
            kind: ReviewKind::Uncommitted,
            target_branch: None,
            base_ref: base,
        },
        files,
    ))
}

pub fn collect_branch_comparison(
    root: &Path,
    target_branch: &str,
) -> Result<(ReviewSource, Vec<ReviewFile>)> {
    let merge_base_output = git_output(root, ["merge-base", "HEAD", target_branch], true)?;
    let merge_base = String::from_utf8_lossy(&merge_base_output.stdout)
        .trim()
        .to_owned();
    let files = collect_worktree_files(root, &merge_base)?;
    if files.is_empty() {
        return Err(ReviewError::NoChanges(format!(
            "no changes found against {target_branch}"
        )));
    }
    Ok((
        ReviewSource {
            kind: ReviewKind::Branch,
            target_branch: Some(target_branch.to_owned()),
            base_ref: merge_base,
        },
        files,
    ))
}

fn collect_worktree_files(root: &Path, base: &str) -> Result<Vec<ReviewFile>> {
    let entries = if base == EMPTY_TREE {
        cached_paths(root)?
            .into_iter()
            .map(|path| NameStatus {
                status: 'A',
                path,
                old_path: None,
            })
            .collect()
    } else {
        name_status(root, base)?
    };
    let untracked = untracked_paths(root)?;
    let untracked_set = untracked.iter().cloned().collect::<HashSet<_>>();
    let mut consumed = HashSet::new();
    let mut files = Vec::new();

    for entry in &entries {
        if entry.status == 'R'
            && entry
                .old_path
                .as_ref()
                .is_some_and(|path| untracked_set.contains(path))
        {
            if let Some(old_path) = &entry.old_path {
                consumed.insert(old_path.clone());
                if let Some(file) = build_target_as_added(root, &entry.path)? {
                    files.push(file);
                }
                if let Some(file) = build_untracked(root, base, old_path)? {
                    files.push(file);
                }
            }
        } else if entry.status == 'D' && untracked_set.contains(&entry.path) {
            consumed.insert(entry.path.clone());
            if let Some(file) = build_untracked(root, base, &entry.path)? {
                files.push(file);
            }
        } else if let Some(file) = build_file_from_refs(root, entry, base)? {
            files.push(file);
        }
    }

    let seen = entries
        .iter()
        .map(|entry| entry.path.as_str())
        .collect::<HashSet<_>>();
    for path in untracked {
        if seen.contains(path.as_str()) || consumed.contains(&path) {
            continue;
        }
        if let Some(file) = build_untracked(root, base, &path)? {
            files.push(file);
        }
    }
    files.sort_by(|left, right| left.path.cmp(&right.path));
    Ok(files)
}

pub fn refresh_reviewed_files<S: std::hash::BuildHasher>(
    root: &Path,
    source: &ReviewSource,
    reviewed: &[ReviewFile],
    touched_paths: &HashSet<String, S>,
    refresh_all: bool,
) -> Result<Vec<FileRefresh>> {
    let inventory = collect_worktree_files(root, &source.base_ref)?;
    let mut used = HashSet::new();
    let mut refreshes = Vec::new();

    for previous in reviewed {
        if !refresh_all && !path_is_touched(&previous.path, touched_paths) {
            continue;
        }
        let candidate = inventory
            .iter()
            .enumerate()
            .filter(|(index, _)| !used.contains(index))
            .find(|(_, file)| file.path == previous.path)
            .or_else(|| {
                inventory
                    .iter()
                    .enumerate()
                    .filter(|(index, _)| !used.contains(index))
                    .find(|(_, file)| {
                        let same_base = match (&previous.old_path, &file.old_path) {
                            (Some(previous_old), Some(file_old)) => previous_old == file_old,
                            (None, Some(file_old)) => file_old == &previous.path,
                            _ => false,
                        };
                        same_base
                            || (touched_paths.contains(&file.path)
                                && file.old_path.as_deref() == Some(previous.path.as_str()))
                            || (previous.status == FileStatus::Added
                                && file.status == FileStatus::Added
                                && touched_paths.contains(&file.path)
                                && same_review_content(previous, file))
                    })
            });

        let file = if let Some((index, file)) = candidate {
            used.insert(index);
            Some(file.clone())
        } else if read_worktree(root, &previous.path)?.is_some() {
            Some(build_unchanged(root, previous, &previous.path)?)
        } else if let Some(base_path) = previous.old_path.as_deref().filter(|base_path| {
            touched_paths.contains(*base_path) && fs::symlink_metadata(root.join(base_path)).is_ok()
        }) {
            Some(build_unchanged(root, previous, base_path)?)
        } else {
            None
        };
        let physically_deleted = file
            .as_ref()
            .is_none_or(|file| fs::symlink_metadata(root.join(&file.path)).is_err());
        refreshes.push(FileRefresh {
            old_path: previous.path.clone(),
            file,
            physically_deleted,
        });
    }
    Ok(refreshes)
}

fn same_review_content(left: &ReviewFile, right: &ReviewFile) -> bool {
    left.binary == right.binary
        && (left.binary
            || left
                .lines
                .iter()
                .map(|line| (&line.kind, &line.text))
                .eq(right.lines.iter().map(|line| (&line.kind, &line.text))))
}

fn path_is_touched<S: std::hash::BuildHasher>(
    path: &str,
    touched_paths: &HashSet<String, S>,
) -> bool {
    touched_paths.iter().any(|touched| {
        touched == path
            || path
                .strip_prefix(touched)
                .is_some_and(|suffix| suffix.starts_with('/'))
            || touched
                .strip_prefix(path)
                .is_some_and(|suffix| suffix.starts_with('/'))
    })
}

fn build_unchanged(root: &Path, previous: &ReviewFile, path: &str) -> Result<ReviewFile> {
    let bytes = read_worktree(root, path)?.unwrap_or_default();
    let base_path = previous.old_path.as_deref().unwrap_or(&previous.path);
    let old_path = (base_path != path).then(|| base_path.to_owned());
    let mut file = review_file_from_bytes(
        path.to_owned(),
        FileStatus::Unchanged,
        &bytes,
        &bytes,
        old_path,
        vec!["No differences from the review base".to_owned()],
    );
    file.status = FileStatus::Unchanged;
    Ok(file)
}

fn name_status(root: &Path, base: &str) -> Result<Vec<NameStatus>> {
    let output = git_output(
        root,
        [
            "diff",
            "--name-status",
            "-z",
            "--find-renames=20%",
            "--find-copies=20%",
            base,
            "--",
        ],
        true,
    )?;
    Ok(parse_name_status_z(&output.stdout))
}

#[must_use]
pub fn parse_name_status_z(output: &[u8]) -> Vec<NameStatus> {
    let tokens = output
        .split(|byte| *byte == 0)
        .filter(|token| !token.is_empty())
        .collect::<Vec<_>>();
    let mut entries = Vec::new();
    let mut index = 0;
    while index < tokens.len() {
        let status_text = String::from_utf8_lossy(tokens[index]);
        index += 1;
        let Some(status) = status_text.chars().next() else {
            continue;
        };
        if matches!(status, 'R' | 'C') {
            if index + 1 >= tokens.len() {
                break;
            }
            let old_path = String::from_utf8_lossy(tokens[index]).into_owned();
            let path = String::from_utf8_lossy(tokens[index + 1]).into_owned();
            index += 2;
            entries.push(NameStatus {
                status,
                path,
                old_path: Some(old_path),
            });
        } else {
            if index >= tokens.len() {
                break;
            }
            let path = String::from_utf8_lossy(tokens[index]).into_owned();
            index += 1;
            entries.push(NameStatus {
                status,
                path,
                old_path: None,
            });
        }
    }
    entries
}

fn untracked_paths(root: &Path) -> Result<Vec<String>> {
    let output = git_output(
        root,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        false,
    )?;
    if !output.status.success() {
        return Ok(Vec::new());
    }
    Ok(output
        .stdout
        .split(|byte| *byte == 0)
        .filter(|token| !token.is_empty())
        .map(|token| String::from_utf8_lossy(token).into_owned())
        .collect())
}

fn cached_paths(root: &Path) -> Result<Vec<String>> {
    let output = git_output(root, ["ls-files", "--cached", "-z"], true)?;
    Ok(output
        .stdout
        .split(|byte| *byte == 0)
        .filter(|token| !token.is_empty())
        .map(|token| String::from_utf8_lossy(token).into_owned())
        .collect())
}

fn build_file_from_refs(root: &Path, entry: &NameStatus, base: &str) -> Result<Option<ReviewFile>> {
    if entry.status == 'C' {
        return build_target_as_added(root, &entry.path);
    }
    let old_path = entry.old_path.as_deref().unwrap_or(&entry.path);
    let old_bytes = if entry.status == 'A' {
        Vec::new()
    } else {
        read_ref(root, base, old_path)?.unwrap_or_default()
    };
    let new_bytes = if entry.status == 'D' {
        Vec::new()
    } else {
        read_worktree(root, &entry.path)?.unwrap_or_default()
    };
    let mut metadata = metadata_for(entry);
    if old_bytes == new_bytes {
        if entry.status == 'R' {
            metadata.push("Renamed without content changes".to_owned());
        } else if entry.status == 'M' {
            metadata.push("Mode changed".to_owned());
            return Ok(Some(create_review_file(
                entry.path.clone(),
                FileStatus::Mode,
                &[],
                &[],
                entry.old_path.clone(),
                false,
                metadata,
            )));
        } else {
            return Ok(None);
        }
    }
    Ok(Some(review_file_from_bytes(
        entry.path.clone(),
        status_name(entry.status),
        &old_bytes,
        &new_bytes,
        entry.old_path.clone(),
        metadata,
    )))
}

fn build_target_as_added(root: &Path, path: &str) -> Result<Option<ReviewFile>> {
    let Some(bytes) = read_worktree(root, path)? else {
        return Ok(None);
    };
    Ok(Some(review_file_from_bytes(
        path.to_owned(),
        FileStatus::Added,
        &[],
        &bytes,
        None,
        Vec::new(),
    )))
}

fn build_untracked(root: &Path, base: &str, path: &str) -> Result<Option<ReviewFile>> {
    let Some(bytes) = read_worktree(root, path)? else {
        return Ok(None);
    };
    if let Some(old_bytes) = read_ref(root, base, path)? {
        if old_bytes == bytes {
            return Ok(None);
        }
        return Ok(Some(review_file_from_bytes(
            path.to_owned(),
            FileStatus::Modified,
            &old_bytes,
            &bytes,
            None,
            Vec::new(),
        )));
    }
    let metadata = if is_binary(&bytes) {
        vec!["Untracked binary file".to_owned()]
    } else {
        vec!["Untracked file".to_owned()]
    };
    Ok(Some(review_file_from_bytes(
        path.to_owned(),
        FileStatus::Added,
        &[],
        &bytes,
        None,
        metadata,
    )))
}

fn review_file_from_bytes(
    path: String,
    status: FileStatus,
    old_bytes: &[u8],
    new_bytes: &[u8],
    old_path: Option<String>,
    mut metadata: Vec<String>,
) -> ReviewFile {
    let binary = is_binary(old_bytes) || is_binary(new_bytes);
    if !binary {
        metadata.extend(trailing_newline_metadata(old_bytes, new_bytes));
    }
    let old_lines = if binary {
        Vec::new()
    } else {
        decode_lines(old_bytes)
    };
    let new_lines = if binary {
        Vec::new()
    } else {
        decode_lines(new_bytes)
    };
    create_review_file(
        path, status, &old_lines, &new_lines, old_path, binary, metadata,
    )
}

fn read_ref(root: &Path, reference: &str, path: &str) -> Result<Option<Vec<u8>>> {
    let spec = format!("{reference}:{path}");
    let output = git_output(root, [OsStr::new("show"), OsStr::new(&spec)], false)?;
    Ok(output.status.success().then_some(output.stdout))
}

fn read_worktree(root: &Path, path: &str) -> Result<Option<Vec<u8>>> {
    let full_path = root.join(path);
    let metadata = match fs::symlink_metadata(&full_path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => {
            return Err(ReviewError::io(
                format!("could not inspect changed file {path}"),
                error,
            ));
        }
    };
    if metadata.file_type().is_symlink() {
        let target = fs::read_link(&full_path).map_err(|error| {
            ReviewError::io(format!("could not read changed symlink {path}"), error)
        })?;
        return Ok(Some(target.as_os_str().as_encoded_bytes().to_vec()));
    }
    if !metadata.is_file() {
        return Ok(None);
    }
    fs::read(&full_path)
        .map(Some)
        .map_err(|error| ReviewError::io(format!("could not read changed file {path}"), error))
}

fn decode_lines(bytes: &[u8]) -> Vec<String> {
    if bytes.is_empty() {
        return Vec::new();
    }
    String::from_utf8_lossy(bytes)
        .lines()
        .map(ToOwned::to_owned)
        .collect()
}

fn is_binary(bytes: &[u8]) -> bool {
    if bytes.is_empty() {
        return false;
    }
    let sample = &bytes[..bytes.len().min(8192)];
    if sample.contains(&0) {
        return true;
    }
    let controls = sample
        .iter()
        .filter(|byte| **byte < 9 || (13 < **byte && **byte < 32))
        .count();
    controls > 8.max(sample.len() / 20)
}

fn trailing_newline_metadata(old: &[u8], new: &[u8]) -> Vec<String> {
    let old_missing = !old.is_empty() && !old.ends_with(b"\n");
    let new_missing = !new.is_empty() && !new.ends_with(b"\n");
    match (old_missing, new_missing) {
        (true, true) => vec!["Old and new files have no trailing newline".to_owned()],
        (true, false) => vec!["Old file had no trailing newline".to_owned()],
        (false, true) => vec!["New file has no trailing newline".to_owned()],
        (false, false) => Vec::new(),
    }
}

const fn status_name(status: char) -> FileStatus {
    match status {
        'A' | 'C' => FileStatus::Added,
        'D' => FileStatus::Deleted,
        'R' => FileStatus::Renamed,
        'T' => FileStatus::TypeChanged,
        _ => FileStatus::Modified,
    }
}

fn metadata_for(entry: &NameStatus) -> Vec<String> {
    match (entry.status, entry.old_path.as_deref()) {
        ('R', Some(old)) => vec![format!("Renamed from {old} to {}", entry.path)],
        ('C', Some(old)) => vec![format!("Copied from {old} to {}", entry.path)],
        _ => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_nul_name_status_with_renames() {
        let parsed = parse_name_status_z(b"M\0a.py\0R100\0old name\0new name\0");
        assert_eq!(
            parsed,
            vec![
                NameStatus {
                    status: 'M',
                    path: "a.py".into(),
                    old_path: None,
                },
                NameStatus {
                    status: 'R',
                    path: "new name".into(),
                    old_path: Some("old name".into()),
                },
            ]
        );
    }

    #[test]
    fn branch_priority_matches_product_contract() {
        assert!(branch_priority("origin/master") < branch_priority("master"));
        assert!(branch_priority("master") < branch_priority("main"));
        assert!(branch_priority("main") < branch_priority("topic"));
    }

    #[test]
    fn binary_detection_rejects_nul_and_control_heavy_data() {
        assert!(is_binary(b"abc\0def"));
        assert!(!is_binary(b"ordinary UTF-8 text\n"));
    }

    #[test]
    fn trailing_newline_metadata_describes_each_changed_side() {
        assert!(trailing_newline_metadata(b"old\n", b"new\n").is_empty());
        assert_eq!(
            trailing_newline_metadata(b"old", b"new\n"),
            ["Old file had no trailing newline"]
        );
        assert_eq!(
            trailing_newline_metadata(b"old\n", b"new"),
            ["New file has no trailing newline"]
        );
        assert_eq!(
            trailing_newline_metadata(b"old", b"new"),
            ["Old and new files have no trailing newline"]
        );
    }
}
