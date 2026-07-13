use std::collections::BTreeMap;

use crate::model::ReviewFile;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FileTreeKind {
    Directory,
    File,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FileTreeRow {
    pub kind: FileTreeKind,
    pub label: String,
    pub depth: usize,
    pub file_index: Option<usize>,
}

#[derive(Default)]
struct Directory {
    directories: BTreeMap<String, Directory>,
    files: Vec<usize>,
}

enum DirectoryEntry<'a> {
    Directory(&'a str, &'a Directory),
    File(usize),
}

#[must_use]
pub fn build_file_tree(files: &[ReviewFile]) -> Vec<FileTreeRow> {
    let mut root = Directory::default();
    for (file_index, file) in files.iter().enumerate() {
        let parts = file
            .path
            .split('/')
            .filter(|part| !part.is_empty())
            .collect::<Vec<_>>();
        let Some((file_name, directories)) = parts.split_last() else {
            root.files.push(file_index);
            continue;
        };
        let mut directory = &mut root;
        for part in directories {
            directory = directory.directories.entry((*part).to_owned()).or_default();
        }
        let _ = file_name;
        directory.files.push(file_index);
    }
    let mut rows = Vec::new();
    append_rows(&root, files, &mut rows, 0);
    rows
}

fn append_rows(
    directory: &Directory,
    files: &[ReviewFile],
    rows: &mut Vec<FileTreeRow>,
    depth: usize,
) {
    let mut entries = Vec::with_capacity(directory.directories.len() + directory.files.len());
    for (name, child) in &directory.directories {
        entries.push((format!("{name}/"), DirectoryEntry::Directory(name, child)));
    }
    for file_index in &directory.files {
        entries.push((
            file_name(&files[*file_index].path).to_owned(),
            DirectoryEntry::File(*file_index),
        ));
    }
    entries.sort_by(|left, right| left.0.cmp(&right.0));

    for (_, entry) in entries {
        match entry {
            DirectoryEntry::Directory(name, child) => {
                let (label, collapsed) = collapse_directory(name, child);
                rows.push(FileTreeRow {
                    kind: FileTreeKind::Directory,
                    label: format!("{label}/"),
                    depth,
                    file_index: None,
                });
                append_rows(collapsed, files, rows, depth + 1);
            }
            DirectoryEntry::File(file_index) => rows.push(FileTreeRow {
                kind: FileTreeKind::File,
                label: file_label(&files[file_index]),
                depth,
                file_index: Some(file_index),
            }),
        }
    }
}

fn collapse_directory<'a>(name: &str, directory: &'a Directory) -> (String, &'a Directory) {
    let mut labels = vec![name];
    let mut current = directory;
    while current.files.is_empty() && current.directories.len() == 1 {
        let (child_name, child) = current.directories.first_key_value().expect("one child");
        labels.push(child_name);
        current = child;
    }
    (labels.join("/"), current)
}

fn file_label(file: &ReviewFile) -> String {
    let new_name = file_name(&file.path);
    if let Some(old_path) = &file.old_path {
        let old_name = file_name(old_path);
        if old_name != new_name {
            return format!("{old_name} -> {new_name}");
        }
    }
    new_name.to_owned()
}

fn file_name(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{FileStatus, create_review_file};

    fn file(path: &str) -> ReviewFile {
        create_review_file(
            path.into(),
            FileStatus::Added,
            &[],
            &["x".into()],
            None,
            false,
            vec![],
        )
    }

    #[test]
    fn collapses_single_child_directories() {
        let files = vec![file("a/b/c/one.rs"), file("a/b/d/two.rs")];
        let rows = build_file_tree(&files);
        assert_eq!(rows[0].label, "a/b/");
        assert_eq!(rows[1].label, "c/");
        assert_eq!(rows[2].label, "one.rs");
    }

    #[test]
    fn file_rows_follow_the_continuous_diff_order() {
        let mut renamed = file("docs/new.md");
        renamed.old_path = Some("docs/old.md".into());
        renamed.status = FileStatus::Renamed;
        let files = vec![
            file(".gitignore"),
            file("Cargo.toml"),
            file("README.md"),
            file("docs/a.md"),
            file("docs/deep/z.md"),
            renamed,
            file("flake.nix"),
            file("src/lib.rs"),
        ];

        let rows = build_file_tree(&files);
        let rendered_file_indices = rows
            .iter()
            .filter_map(|row| row.file_index)
            .collect::<Vec<_>>();

        assert_eq!(rendered_file_indices, (0..files.len()).collect::<Vec<_>>());
        assert!(rows.iter().any(|row| row.label == "old.md -> new.md"));
    }
}
