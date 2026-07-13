use std::path::{Path, PathBuf};

use crate::model::{ReviewComment, ReviewFile, ReviewLine, ReviewSource};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Expansion {
    pub id: String,
    pub file_path: String,
    pub direction: ExpansionDirection,
    pub gap_start: usize,
    pub gap_end: usize,
    pub reveal_start: usize,
    pub reveal_end: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExpansionDirection {
    Above,
    Below,
}

impl Expansion {
    #[must_use]
    pub const fn remaining_count(&self) -> usize {
        self.gap_end - self.gap_start + 1
    }

    #[must_use]
    pub const fn reveal_count(&self) -> usize {
        self.reveal_end - self.reveal_start + 1
    }

    #[must_use]
    pub fn label(&self) -> String {
        let direction = match self.direction {
            ExpansionDirection::Above => "above",
            ExpansionDirection::Below => "below",
        };
        if self.remaining_count() <= self.reveal_count() {
            let noun = if self.remaining_count() == 1 {
                "line"
            } else {
                "lines"
            };
            format!(
                "Show {} remaining {noun} {direction}",
                self.remaining_count()
            )
        } else {
            format!("Show {} lines {direction}", self.reveal_count())
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DocumentKind {
    FileHeader,
    Metadata,
    Code,
    Expansion,
    Comment,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DocumentItem {
    pub kind: DocumentKind,
    pub file_index: usize,
    pub file_path: String,
    pub text: String,
    pub row_index: Option<usize>,
    pub line: Option<ReviewLine>,
    pub expansion: Option<Expansion>,
    pub comment: Option<ReviewComment>,
}

impl DocumentItem {
    #[must_use]
    pub const fn selectable(&self) -> bool {
        matches!(
            self.kind,
            DocumentKind::Code | DocumentKind::Expansion | DocumentKind::Comment
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Selection {
    Metadata {
        file_path: String,
    },
    Code {
        file_path: String,
        anchor_row: usize,
        active_row: usize,
    },
    Expansion {
        file_path: String,
        id: String,
    },
    Comment {
        file_path: String,
        id: u64,
    },
}

#[derive(Debug)]
pub struct ReviewState {
    pub repository_root: PathBuf,
    pub source: ReviewSource,
    pub files: Vec<ReviewFile>,
    pub comments: Vec<ReviewComment>,
    pub file_pane_index: usize,
    pub selection: Option<Selection>,
    comment_counter: u64,
}

impl ReviewState {
    #[must_use]
    pub fn new(repository_root: &Path, source: ReviewSource, files: Vec<ReviewFile>) -> Self {
        let mut state = Self {
            repository_root: repository_root.to_path_buf(),
            source,
            files,
            comments: Vec::new(),
            file_pane_index: 0,
            selection: None,
            comment_counter: 0,
        };
        state.initialize_selection();
        state
    }

    fn initialize_selection(&mut self) {
        for (file_index, file) in self.files.iter().enumerate() {
            if let Some(row) = file.first_visible_row() {
                self.file_pane_index = file_index;
                self.selection = Some(Selection::Code {
                    file_path: file.path.clone(),
                    anchor_row: row,
                    active_row: row,
                });
                return;
            }
        }
        if let Some(file) = self.files.first() {
            self.selection = Some(Selection::Metadata {
                file_path: file.path.clone(),
            });
        }
    }

    #[must_use]
    pub fn file_index(&self, path: &str) -> Option<usize> {
        self.files.iter().position(|file| file.path == path)
    }

    #[must_use]
    pub fn file_by_path(&self, path: &str) -> Option<&ReviewFile> {
        self.files.iter().find(|file| file.path == path)
    }

    pub fn file_by_path_mut(&mut self, path: &str) -> Option<&mut ReviewFile> {
        self.files.iter_mut().find(|file| file.path == path)
    }

    #[must_use]
    pub fn comments_for_file(&self, path: &str) -> Vec<&ReviewComment> {
        let mut comments = self
            .comments
            .iter()
            .filter(|comment| comment.file_path == path)
            .collect::<Vec<_>>();
        comments.sort_by_key(|comment| (comment.sorted_rows(), comment.order));
        comments
    }

    #[must_use]
    pub fn document_items(&self) -> Vec<DocumentItem> {
        let mut items = Vec::new();
        for (file_index, file) in self.files.iter().enumerate() {
            let comments = self.comments_for_file(&file.path);
            let suffix = if comments.is_empty() {
                String::new()
            } else if comments.len() == 1 {
                " (1 comment)".to_owned()
            } else {
                format!(" ({} comments)", comments.len())
            };
            items.push(DocumentItem {
                kind: DocumentKind::FileHeader,
                file_index,
                file_path: file.path.clone(),
                text: format!("{} {}{suffix}", file.status_marker(), file.display_path()),
                row_index: None,
                line: None,
                expansion: None,
                comment: None,
            });
            for metadata in &file.metadata {
                items.push(metadata_item(file_index, &file.path, metadata.clone()));
            }
            if file.binary {
                items.push(metadata_item(
                    file_index,
                    &file.path,
                    format!("Binary file changed: {}", file.display_path()),
                ));
                continue;
            }
            let mut previous_end = None;
            for (interval_index, interval) in file.visible_intervals.iter().enumerate() {
                let gap_start = previous_end.map_or(0, |end| end + 1);
                if interval.start > gap_start {
                    let direction = if interval_index == 0 {
                        ExpansionDirection::Above
                    } else {
                        ExpansionDirection::Below
                    };
                    items.push(expansion_item(
                        file_index,
                        &file.path,
                        direction,
                        gap_start,
                        interval.start - 1,
                    ));
                }
                for row_index in
                    interval.start..=interval.end.min(file.lines.len().saturating_sub(1))
                {
                    let line = file.lines[row_index].clone();
                    items.push(DocumentItem {
                        kind: DocumentKind::Code,
                        file_index,
                        file_path: file.path.clone(),
                        text: line.text.clone(),
                        row_index: Some(row_index),
                        line: Some(line),
                        expansion: None,
                        comment: None,
                    });
                    for comment in comments
                        .iter()
                        .filter(|comment| comment.sorted_rows().1 == row_index)
                    {
                        items.push(DocumentItem {
                            kind: DocumentKind::Comment,
                            file_index,
                            file_path: file.path.clone(),
                            text: comment.body.clone(),
                            row_index: None,
                            line: None,
                            expansion: None,
                            comment: Some((*comment).clone()),
                        });
                    }
                }
                previous_end = Some(interval.end);
            }
            match file.lines.len().checked_sub(1) {
                Some(last) if previous_end.is_none_or(|end| end < last) => {
                    items.push(expansion_item(
                        file_index,
                        &file.path,
                        ExpansionDirection::Below,
                        previous_end.map_or(0, |end| end + 1),
                        last,
                    ));
                }
                _ => {}
            }
        }
        items
    }

    #[must_use]
    pub fn active_document_index(&self) -> Option<usize> {
        self.document_items()
            .iter()
            .position(|item| self.item_is_selected(item))
    }

    #[must_use]
    pub fn item_is_selected(&self, item: &DocumentItem) -> bool {
        match self.selection.as_ref() {
            Some(Selection::Code {
                file_path,
                active_row,
                ..
            }) => {
                item.kind == DocumentKind::Code
                    && &item.file_path == file_path
                    && item.row_index == Some(*active_row)
            }
            Some(Selection::Expansion { file_path, id }) => {
                item.kind == DocumentKind::Expansion
                    && &item.file_path == file_path
                    && item.expansion.as_ref().is_some_and(|value| &value.id == id)
            }
            Some(Selection::Comment { file_path, id }) => {
                item.kind == DocumentKind::Comment
                    && &item.file_path == file_path
                    && item.comment.as_ref().is_some_and(|value| value.id == *id)
            }
            _ => false,
        }
    }

    pub fn select_document_index(&mut self, index: usize) {
        let items = self.document_items();
        let Some(item) = items.get(index.min(items.len().saturating_sub(1))) else {
            return;
        };
        self.file_pane_index = item.file_index;
        self.selection = match item.kind {
            DocumentKind::Code => item.row_index.map(|row| Selection::Code {
                file_path: item.file_path.clone(),
                anchor_row: row,
                active_row: row,
            }),
            DocumentKind::Expansion => {
                item.expansion
                    .as_ref()
                    .map(|expansion| Selection::Expansion {
                        file_path: item.file_path.clone(),
                        id: expansion.id.clone(),
                    })
            }
            DocumentKind::Comment => item.comment.as_ref().map(|comment| Selection::Comment {
                file_path: item.file_path.clone(),
                id: comment.id,
            }),
            DocumentKind::FileHeader | DocumentKind::Metadata => Some(Selection::Metadata {
                file_path: item.file_path.clone(),
            }),
        };
    }

    pub fn select_file(&mut self, path: &str) -> usize {
        let Some(file_index) = self.file_index(path) else {
            return 0;
        };
        self.file_pane_index = file_index;
        let file = &self.files[file_index];
        self.selection = file.first_visible_row().map_or_else(
            || {
                Some(Selection::Metadata {
                    file_path: file.path.clone(),
                })
            },
            |row| {
                Some(Selection::Code {
                    file_path: file.path.clone(),
                    anchor_row: row,
                    active_row: row,
                })
            },
        );
        self.document_items()
            .iter()
            .position(|item| item.kind == DocumentKind::FileHeader && item.file_path == path)
            .unwrap_or(0)
    }

    pub fn move_file_selection(&mut self, delta: isize) -> usize {
        if self.files.is_empty() {
            return 0;
        }
        self.file_pane_index = offset_index(self.file_pane_index, delta, self.files.len());
        let path = self.files[self.file_pane_index].path.clone();
        self.select_file(&path)
    }

    pub fn move_selection(&mut self, delta: isize) -> usize {
        let items = self.document_items();
        let selectable = items
            .iter()
            .enumerate()
            .filter_map(|(index, item)| item.selectable().then_some(index))
            .collect::<Vec<_>>();
        if selectable.is_empty() {
            return 0;
        }
        let current = self.active_document_index().unwrap_or(selectable[0]);
        let position = selectable
            .iter()
            .position(|index| *index == current)
            .unwrap_or_else(|| selectable.partition_point(|index| *index < current));
        let next = offset_index(position.min(selectable.len() - 1), delta, selectable.len());
        let document_index = selectable[next];
        self.select_document_index(document_index);
        document_index
    }

    pub fn extend_selection(&mut self, delta: isize) -> usize {
        let Some(Selection::Code {
            file_path,
            anchor_row,
            active_row,
        }) = self.selection.clone()
        else {
            return self.move_selection(delta);
        };
        let Some(file) = self.file_by_path(&file_path) else {
            return 0;
        };
        let Some(interval) = file
            .visible_intervals
            .iter()
            .find(|interval| interval.contains(anchor_row))
        else {
            return self.active_document_index().unwrap_or(0);
        };
        let next = offset_bounded(active_row, delta, interval.start, interval.end);
        self.selection = Some(Selection::Code {
            file_path,
            anchor_row,
            active_row: next,
        });
        self.active_document_index().unwrap_or(0)
    }

    #[must_use]
    pub fn selected_range(&self) -> Option<(&str, usize, usize)> {
        let Selection::Code {
            file_path,
            anchor_row,
            active_row,
        } = self.selection.as_ref()?
        else {
            return None;
        };
        Some((
            file_path,
            (*anchor_row).min(*active_row),
            (*anchor_row).max(*active_row),
        ))
    }

    #[must_use]
    pub fn is_row_selected(&self, file_path: &str, row: usize) -> bool {
        self.selected_range()
            .is_some_and(|(path, start, end)| path == file_path && start <= row && row <= end)
    }

    pub fn collapse_selection(&mut self) -> bool {
        let Some(Selection::Code {
            file_path,
            anchor_row,
            active_row,
        }) = self.selection.clone()
        else {
            return false;
        };
        if anchor_row == active_row {
            return false;
        }
        self.selection = Some(Selection::Code {
            file_path,
            anchor_row: active_row,
            active_row,
        });
        true
    }

    pub fn add_comment(&mut self, body: &str) -> Option<u64> {
        let body = body.trim_end();
        if body.trim().is_empty() {
            return None;
        }
        let (file_path, start, end) = self
            .selected_range()
            .map(|(path, start, end)| (path.to_owned(), start, end))?;
        let file = self.file_by_path(&file_path)?;
        let selected_lines = file.lines.get(start..=end)?.to_vec();
        self.comment_counter += 1;
        let id = self.comment_counter;
        self.comments.push(ReviewComment {
            id,
            file_path,
            start_row: start,
            end_row: end,
            body: body.to_owned(),
            selected_lines,
            order: id,
        });
        Some(id)
    }

    pub fn select_comment(&mut self, id: u64) -> Option<usize> {
        let index = self.document_items().iter().position(|item| {
            item.kind == DocumentKind::Comment
                && item
                    .comment
                    .as_ref()
                    .is_some_and(|comment| comment.id == id)
        })?;
        self.select_document_index(index);
        Some(index)
    }

    #[must_use]
    pub fn comment_for_selection(&self) -> Option<&ReviewComment> {
        match self.selection.as_ref()? {
            Selection::Comment { id, .. } => self.comments.iter().find(|comment| comment.id == *id),
            Selection::Code {
                file_path,
                active_row,
                ..
            } => self.comments.iter().find(|comment| {
                comment.file_path == *file_path
                    && comment.sorted_rows().0 <= *active_row
                    && *active_row <= comment.sorted_rows().1
            }),
            _ => None,
        }
    }

    pub fn update_comment(&mut self, id: u64, body: &str) -> bool {
        let body = body.trim_end();
        if body.trim().is_empty() {
            return false;
        }
        let Some(comment) = self.comments.iter_mut().find(|comment| comment.id == id) else {
            return false;
        };
        body.clone_into(&mut comment.body);
        true
    }

    pub fn delete_comment(&mut self, id: u64) -> bool {
        let deleted = self
            .comments
            .iter()
            .find(|comment| comment.id == id)
            .cloned();
        let before = self.comments.len();
        self.comments.retain(|comment| comment.id != id);
        if self.comments.len() == before {
            return false;
        }
        if matches!(self.selection, Some(Selection::Comment { id: selected, .. }) if selected == id)
        {
            if let Some(comment) = deleted {
                let row = comment.end_row.min(
                    self.file_by_path(&comment.file_path)
                        .map_or(0, |file| file.lines.len().saturating_sub(1)),
                );
                self.selection = Some(Selection::Code {
                    file_path: comment.file_path.clone(),
                    anchor_row: row,
                    active_row: row,
                });
                if let Some(file_index) = self.file_index(&comment.file_path) {
                    self.file_pane_index = file_index;
                }
            } else {
                self.initialize_selection();
            }
        }
        true
    }

    #[must_use]
    pub fn selected_comment_id(&self) -> Option<u64> {
        self.comment_for_selection().map(|comment| comment.id)
    }

    pub fn activate_selection(&mut self) -> Activation {
        match self.selection.clone() {
            Some(Selection::Expansion { id, .. }) => {
                self.expand_context(&id);
                Activation::Expanded
            }
            Some(Selection::Comment { id, .. }) => Activation::EditComment(id),
            Some(Selection::Code { .. }) => Activation::NewComment,
            _ => Activation::None,
        }
    }

    pub fn expand_context(&mut self, id: &str) -> usize {
        let expansion = self.document_items().into_iter().find_map(|item| {
            item.expansion
                .filter(|expansion| expansion.id.as_str() == id)
        });
        let Some(expansion) = expansion else {
            return self.active_document_index().unwrap_or(0);
        };
        if let Some(file) = self.file_by_path_mut(&expansion.file_path) {
            file.add_visible_interval(expansion.reveal_start, expansion.reveal_end);
        }
        let file_index = self.file_index(&expansion.file_path).unwrap_or(0);
        self.file_pane_index = file_index;
        self.selection = Some(Selection::Code {
            file_path: expansion.file_path,
            anchor_row: expansion.reveal_start,
            active_row: expansion.reveal_start,
        });
        self.active_document_index().unwrap_or(0)
    }

    #[must_use]
    pub fn file_for_document_index(&self, index: usize) -> Option<String> {
        let items = self.document_items();
        items
            .get(index.min(items.len().saturating_sub(1)))
            .map(|item| item.file_path.clone())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Activation {
    None,
    NewComment,
    EditComment(u64),
    Expanded,
}

fn metadata_item(file_index: usize, file_path: &str, text: String) -> DocumentItem {
    DocumentItem {
        kind: DocumentKind::Metadata,
        file_index,
        file_path: file_path.to_owned(),
        text,
        row_index: None,
        line: None,
        expansion: None,
        comment: None,
    }
}

fn expansion_item(
    file_index: usize,
    file_path: &str,
    direction: ExpansionDirection,
    gap_start: usize,
    gap_end: usize,
) -> DocumentItem {
    let (reveal_start, reveal_end) = match direction {
        ExpansionDirection::Above => (gap_end.saturating_sub(19).max(gap_start), gap_end),
        ExpansionDirection::Below => (gap_start, gap_start.saturating_add(19).min(gap_end)),
    };
    let direction_name = match direction {
        ExpansionDirection::Above => "above",
        ExpansionDirection::Below => "below",
    };
    let expansion = Expansion {
        id: format!("{file_path}:{direction_name}:{gap_start}:{gap_end}"),
        file_path: file_path.to_owned(),
        direction,
        gap_start,
        gap_end,
        reveal_start,
        reveal_end,
    };
    DocumentItem {
        kind: DocumentKind::Expansion,
        file_index,
        file_path: file_path.to_owned(),
        text: expansion.label(),
        row_index: None,
        line: None,
        expansion: Some(expansion),
        comment: None,
    }
}

fn offset_index(index: usize, delta: isize, len: usize) -> usize {
    if delta.is_negative() {
        index.saturating_sub(delta.unsigned_abs())
    } else {
        index.saturating_add(delta.unsigned_abs()).min(len - 1)
    }
}

fn offset_bounded(index: usize, delta: isize, start: usize, end: usize) -> usize {
    if delta.is_negative() {
        index.saturating_sub(delta.unsigned_abs()).max(start)
    } else {
        index.saturating_add(delta.unsigned_abs()).min(end)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{FileStatus, ReviewKind, create_review_file};

    fn state_with_lines(count: usize) -> ReviewState {
        let old = (0..count)
            .map(|index| format!("line {index}"))
            .collect::<Vec<_>>();
        let mut new = old.clone();
        new[count / 2] = "changed".into();
        let file = create_review_file(
            "src/a.rs".into(),
            FileStatus::Modified,
            &old,
            &new,
            None,
            false,
            vec![],
        );
        ReviewState::new(
            Path::new("/tmp/repo"),
            ReviewSource {
                kind: ReviewKind::Uncommitted,
                target_branch: None,
                base_ref: "HEAD".into(),
            },
            vec![file],
        )
    }

    #[test]
    fn selection_comment_edit_and_delete_round_trip() {
        let mut state = state_with_lines(20);
        let id = state.add_comment("first\nsecond").unwrap();
        assert!(
            state
                .document_items()
                .iter()
                .any(|item| item.text.ends_with("(1 comment)"))
        );
        state.select_comment(id).unwrap();
        assert_eq!(state.comment_for_selection().unwrap().body, "first\nsecond");
        assert!(state.update_comment(id, "updated"));
        assert!(state.delete_comment(id));
        assert!(state.comments.is_empty());
        assert!(matches!(
            state.selection,
            Some(Selection::Code {
                ref file_path,
                anchor_row: 0,
                active_row: 0,
            }) if file_path == "src/a.rs"
        ));
    }

    #[test]
    fn range_selection_extends_upward_shrinks_and_crosses_its_anchor() {
        let mut state = state_with_lines(20);
        state.selection = Some(Selection::Code {
            file_path: "src/a.rs".into(),
            anchor_row: 10,
            active_row: 10,
        });

        state.extend_selection(-3);
        assert_eq!(state.selected_range(), Some(("src/a.rs", 7, 10)));
        state.extend_selection(1);
        assert_eq!(state.selected_range(), Some(("src/a.rs", 8, 10)));
        state.extend_selection(5);
        assert_eq!(state.selected_range(), Some(("src/a.rs", 10, 13)));
        assert!(matches!(
            state.selection,
            Some(Selection::Code {
                anchor_row: 10,
                active_row: 13,
                ..
            })
        ));
    }

    #[test]
    fn range_selection_stops_at_both_file_boundaries() {
        let mut state = state_with_lines(20);
        let last = state.files[0].lines.len() - 1;
        state.selection = Some(Selection::Code {
            file_path: "src/a.rs".into(),
            anchor_row: 10,
            active_row: 10,
        });

        state.extend_selection(-isize::MAX);
        assert_eq!(state.selected_range(), Some(("src/a.rs", 0, 10)));
        state.extend_selection(isize::MAX);
        assert_eq!(state.selected_range(), Some(("src/a.rs", 10, last)));
    }

    #[test]
    fn deleting_a_selected_comment_stays_at_its_range_in_the_same_file() {
        let mut state = state_with_lines(20);
        let old = (0..12)
            .map(|index| format!("other {index}"))
            .collect::<Vec<_>>();
        let mut new = old.clone();
        new[6] = "other changed".into();
        state.files.push(create_review_file(
            "src/b.rs".into(),
            FileStatus::Modified,
            &old,
            &new,
            None,
            false,
            vec![],
        ));
        state.selection = Some(Selection::Code {
            file_path: "src/b.rs".into(),
            anchor_row: 2,
            active_row: 5,
        });
        let id = state.add_comment("second-file range").unwrap();
        state.select_comment(id).unwrap();

        assert!(state.delete_comment(id));
        assert_eq!(state.file_pane_index, 1);
        assert!(matches!(
            state.selection,
            Some(Selection::Code {
                ref file_path,
                anchor_row: 5,
                active_row: 5,
            }) if file_path == "src/b.rs"
        ));
    }

    #[test]
    fn adversarial_navigation_sequence_preserves_selection_invariants() {
        let mut state = state_with_lines(300);
        let mut seed = 0x5eed_cafe_u64;
        for step in 0..20_000 {
            seed = seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1);
            let delta = isize::try_from((seed >> 32) % 17).unwrap_or(0) - 8;
            match seed % 8 {
                0 => {
                    state.move_selection(delta);
                }
                1 => {
                    state.extend_selection(delta);
                }
                2 => {
                    let count = state.document_items().len().max(1);
                    let random = usize::try_from(seed >> 32).unwrap_or(0);
                    state.select_document_index(random % count);
                }
                3 => {
                    if let Selection::Expansion { id, .. } = state.selection.clone().unwrap() {
                        state.expand_context(&id);
                    }
                }
                4 if step % 97 == 0 => {
                    let _ = state.add_comment(&format!("comment {step}"));
                }
                5 if !state.comments.is_empty() => {
                    let random = usize::try_from(seed >> 32).unwrap_or(0);
                    let index = random % state.comments.len();
                    let id = state.comments[index].id;
                    let _ = state.select_comment(id);
                }
                6 if step % 131 == 0 => {
                    if let Some(id) = state.selected_comment_id() {
                        state.delete_comment(id);
                    }
                }
                _ => {
                    let _ = state.collapse_selection();
                }
            }

            assert!(state.file_pane_index < state.files.len());
            match state.selection.as_ref().unwrap() {
                Selection::Code {
                    file_path,
                    anchor_row,
                    active_row,
                } => {
                    let file = state.file_by_path(file_path).unwrap();
                    assert!(*anchor_row < file.lines.len());
                    assert!(*active_row < file.lines.len());
                    assert!(
                        file.visible_intervals
                            .iter()
                            .any(|interval| interval.contains(*active_row))
                    );
                }
                Selection::Expansion { file_path, id } => assert!(
                    state
                        .document_items()
                        .iter()
                        .any(|item| item.file_path == *file_path
                            && item.expansion.as_ref().is_some_and(|value| value.id == *id))
                ),
                Selection::Comment { file_path, id } => assert!(
                    state
                        .comments
                        .iter()
                        .any(|comment| comment.file_path == *file_path && comment.id == *id)
                ),
                Selection::Metadata { file_path } => {
                    assert!(state.file_by_path(file_path).is_some());
                }
            }
        }
    }

    #[test]
    fn expansion_reveals_twenty_rows() {
        let mut state = state_with_lines(300);
        let expansion = state
            .document_items()
            .into_iter()
            .find_map(|item| item.expansion)
            .unwrap();
        assert_eq!(expansion.reveal_count(), 20);
        let before = state
            .file_by_path("src/a.rs")
            .unwrap()
            .visible_intervals
            .clone();
        state.expand_context(&expansion.id);
        let after = &state.file_by_path("src/a.rs").unwrap().visible_intervals;
        assert_ne!(&before, after);
    }
}
