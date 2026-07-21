use std::path::{Path, PathBuf};

use crate::model::{
    CommentPlacement, ReviewComment, ReviewFile, ReviewLine, ReviewSource, VisibleInterval,
};

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

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct RefreshOutcome {
    pub moved_comments: usize,
    pub detached_comments: usize,
    pub deleted_comments: usize,
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
            }
            for comment in comments.iter().filter(|comment| comment.is_file_level()) {
                items.push(comment_item(file_index, &file.path, comment));
            }
            if file.binary {
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
                    for comment in comments.iter().filter(|comment| {
                        !comment.is_file_level() && comment.sorted_rows().1 == row_index
                    }) {
                        items.push(comment_item(file_index, &file.path, comment));
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
            placement: CommentPlacement::Lines {
                start_row: start,
                end_row: end,
                selected_lines,
            },
            body: body.to_owned(),
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
                !comment.is_file_level()
                    && comment.file_path == *file_path
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
                let row = comment.sorted_rows().1.min(
                    self.file_by_path(&comment.file_path)
                        .map_or(0, |file| file.lines.len().saturating_sub(1)),
                );
                self.selection = self.file_by_path(&comment.file_path).map_or_else(
                    || None,
                    |file| {
                        if file.lines.is_empty() {
                            Some(Selection::Metadata {
                                file_path: comment.file_path.clone(),
                            })
                        } else {
                            Some(Selection::Code {
                                file_path: comment.file_path.clone(),
                                anchor_row: row,
                                active_row: row,
                            })
                        }
                    },
                );
                if let Some(file_index) = self.file_index(&comment.file_path) {
                    self.file_pane_index = file_index;
                }
            } else {
                self.initialize_selection();
            }
        }
        true
    }

    pub fn replace_file(
        &mut self,
        old_path: &str,
        replacement: Option<ReviewFile>,
        physically_deleted: bool,
    ) -> RefreshOutcome {
        let Some(file_index) = self.file_index(old_path) else {
            return RefreshOutcome::default();
        };
        let previous_file = self.files[file_index].clone();
        let previous_selection = self.selection.clone();
        let selected_signature = selected_signature(&previous_file, previous_selection.as_ref());
        let mut outcome = RefreshOutcome::default();

        if physically_deleted {
            let before = self.comments.len();
            self.comments
                .retain(|comment| comment.file_path != old_path);
            outcome.deleted_comments = before - self.comments.len();
        }

        let Some(mut replacement) = replacement else {
            self.files.remove(file_index);
            self.selection = None;
            if self.files.is_empty() {
                self.file_pane_index = 0;
            } else {
                self.file_pane_index = file_index.min(self.files.len() - 1);
                self.initialize_selection();
            }
            return outcome;
        };

        let new_path = replacement.path.clone();
        if !physically_deleted {
            for comment in self
                .comments
                .iter_mut()
                .filter(|comment| comment.file_path == old_path)
            {
                comment.file_path.clone_from(&new_path);
                let old_rows = comment.sorted_rows();
                if replacement.binary || replacement.lines.is_empty() {
                    if !comment.is_file_level() {
                        outcome.detached_comments += 1;
                    }
                    comment.placement = CommentPlacement::File {
                        preferred_start_row: old_rows.0,
                        preferred_end_row: old_rows.1,
                        selected_lines: comment.selected_lines().to_vec(),
                    };
                    continue;
                }
                let (start, end, matched) =
                    relocate_rows(comment.selected_lines(), old_rows, &replacement.lines);
                if matched && (start, end) != old_rows {
                    outcome.moved_comments += 1;
                }
                comment.placement = CommentPlacement::Lines {
                    start_row: start,
                    end_row: end,
                    selected_lines: replacement.lines[start..=end].to_vec(),
                };
                replacement.add_visible_interval(start, end);
            }
        }

        preserve_visible_intervals(&previous_file, &mut replacement);
        self.files[file_index] = replacement;
        self.file_pane_index = file_index;
        self.selection = remap_selection(
            previous_selection,
            old_path,
            &new_path,
            &self.files[file_index],
            selected_signature.as_deref(),
            &self.comments,
        );
        if self.selection.is_none() {
            self.initialize_selection();
        }
        outcome
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

fn comment_item(file_index: usize, file_path: &str, comment: &ReviewComment) -> DocumentItem {
    DocumentItem {
        kind: DocumentKind::Comment,
        file_index,
        file_path: file_path.to_owned(),
        text: comment.body.clone(),
        row_index: None,
        line: None,
        expansion: None,
        comment: Some(comment.clone()),
    }
}

fn selected_signature(file: &ReviewFile, selection: Option<&Selection>) -> Option<Vec<ReviewLine>> {
    let Selection::Code {
        file_path,
        anchor_row,
        active_row,
    } = selection?
    else {
        return None;
    };
    if file_path != &file.path {
        return None;
    }
    let start = (*anchor_row).min(*active_row);
    let end = (*anchor_row).max(*active_row);
    file.lines.get(start..=end).map(<[ReviewLine]>::to_vec)
}

fn relocate_rows(
    signature: &[ReviewLine],
    previous: (usize, usize),
    lines: &[ReviewLine],
) -> (usize, usize, bool) {
    if lines.is_empty() {
        return (0, 0, false);
    }
    if !signature.is_empty() && signature.len() <= lines.len() {
        let best = lines
            .windows(signature.len())
            .enumerate()
            .filter(|(_, candidate)| {
                candidate
                    .iter()
                    .zip(signature)
                    .all(|(left, right)| left.kind == right.kind && left.text == right.text)
            })
            .min_by_key(|(start, _)| (start.abs_diff(previous.0), *start));
        if let Some((start, _)) = best {
            return (start, start + signature.len() - 1, true);
        }
    }
    let requested_len = previous.1.saturating_sub(previous.0).saturating_add(1);
    let len = requested_len.min(lines.len()).max(1);
    let start = previous.0.min(lines.len() - len);
    (start, start + len - 1, false)
}

fn preserve_visible_intervals(previous: &ReviewFile, replacement: &mut ReviewFile) {
    if replacement.lines.is_empty() {
        return;
    }
    for VisibleInterval { start, end } in &previous.visible_intervals {
        replacement.add_visible_interval(*start, *end);
    }
}

fn remap_selection(
    selection: Option<Selection>,
    old_path: &str,
    new_path: &str,
    file: &ReviewFile,
    signature: Option<&[ReviewLine]>,
    comments: &[ReviewComment],
) -> Option<Selection> {
    match selection? {
        Selection::Comment { file_path, id } if file_path == old_path => comments
            .iter()
            .any(|comment| comment.id == id)
            .then(|| Selection::Comment {
                file_path: new_path.to_owned(),
                id,
            }),
        Selection::Code {
            file_path,
            anchor_row,
            active_row,
        } if file_path == old_path => {
            if file.lines.is_empty() {
                return Some(Selection::Metadata {
                    file_path: new_path.to_owned(),
                });
            }
            let previous = (anchor_row.min(active_row), anchor_row.max(active_row));
            let (start, end, _) =
                relocate_rows(signature.unwrap_or_default(), previous, &file.lines);
            Some(Selection::Code {
                file_path: new_path.to_owned(),
                anchor_row: start,
                active_row: end,
            })
        }
        Selection::Metadata { file_path } if file_path == old_path => Some(Selection::Metadata {
            file_path: new_path.to_owned(),
        }),
        Selection::Expansion { file_path, .. } if file_path == old_path => {
            file.first_visible_row().map_or_else(
                || {
                    Some(Selection::Metadata {
                        file_path: new_path.to_owned(),
                    })
                },
                |row| {
                    Some(Selection::Code {
                        file_path: new_path.to_owned(),
                        anchor_row: row,
                        active_row: row,
                    })
                },
            )
        }
        other => Some(other),
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

    fn state_with_added_lines(values: &[&str]) -> ReviewState {
        let file = create_review_file(
            "src/a.rs".into(),
            FileStatus::Added,
            &[],
            &values
                .iter()
                .map(|value| (*value).to_owned())
                .collect::<Vec<_>>(),
            None,
            false,
            vec![],
        );
        ReviewState::new(
            Path::new("/tmp/repo"),
            ReviewSource {
                kind: ReviewKind::Uncommitted,
                target_branch: None,
                base_ref: "base".into(),
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

    #[test]
    fn refresh_moves_comment_to_nearest_exact_multiline_match() {
        let mut state = state_with_added_lines(&["zero", "target", "second", "tail"]);
        state.selection = Some(Selection::Code {
            file_path: "src/a.rs".into(),
            anchor_row: 1,
            active_row: 2,
        });
        let id = state.add_comment("move me").unwrap();
        let replacement = create_review_file(
            "src/a.rs".into(),
            FileStatus::Added,
            &[],
            &[
                "before".into(),
                "zero".into(),
                "target".into(),
                "second".into(),
                "tail".into(),
            ],
            None,
            false,
            vec![],
        );

        let outcome = state.replace_file("src/a.rs", Some(replacement), false);

        assert_eq!(outcome.moved_comments, 1);
        let comment = state
            .comments
            .iter()
            .find(|comment| comment.id == id)
            .unwrap();
        assert_eq!(comment.sorted_rows(), (2, 3));
        assert_eq!(
            comment
                .selected_lines()
                .iter()
                .map(|line| line.text.as_str())
                .collect::<Vec<_>>(),
            vec!["target", "second"]
        );
    }

    #[test]
    fn refresh_duplicate_match_prefers_nearest_then_earlier() {
        let mut state = state_with_added_lines(&["a", "b", "x", "target", "y"]);
        state.selection = Some(Selection::Code {
            file_path: "src/a.rs".into(),
            anchor_row: 3,
            active_row: 3,
        });
        state.add_comment("tie").unwrap();
        let replacement = create_review_file(
            "src/a.rs".into(),
            FileStatus::Added,
            &[],
            &[
                "a".into(),
                "target".into(),
                "x".into(),
                "y".into(),
                "z".into(),
                "target".into(),
            ],
            None,
            false,
            vec![],
        );

        state.replace_file("src/a.rs", Some(replacement), false);

        assert_eq!(state.comments[0].sorted_rows(), (1, 1));
    }

    #[test]
    fn refresh_without_match_clamps_range_up_and_preserves_length() {
        let mut state = state_with_added_lines(&["a", "b", "c", "d", "e", "f"]);
        state.selection = Some(Selection::Code {
            file_path: "src/a.rs".into(),
            anchor_row: 4,
            active_row: 5,
        });
        state.add_comment("clamp").unwrap();
        let replacement = create_review_file(
            "src/a.rs".into(),
            FileStatus::Added,
            &[],
            &["one".into(), "two".into(), "three".into()],
            None,
            false,
            vec![],
        );

        state.replace_file("src/a.rs", Some(replacement), false);

        assert_eq!(state.comments[0].sorted_rows(), (1, 2));
    }

    #[test]
    fn empty_refresh_detaches_comment_and_text_refresh_restores_it() {
        let mut state = state_with_added_lines(&["first", "target", "last"]);
        state.selection = Some(Selection::Code {
            file_path: "src/a.rs".into(),
            anchor_row: 1,
            active_row: 1,
        });
        let id = state.add_comment("survive empty").unwrap();
        let empty = create_review_file(
            "src/a.rs".into(),
            FileStatus::Unchanged,
            &[],
            &[],
            None,
            false,
            vec![],
        );

        let detached = state.replace_file("src/a.rs", Some(empty), false);
        assert_eq!(detached.detached_comments, 1);
        assert!(state.comments[0].is_file_level());
        assert!(state.document_items().iter().any(|item| {
            item.comment
                .as_ref()
                .is_some_and(|comment| comment.id == id)
        }));

        let restored = create_review_file(
            "src/a.rs".into(),
            FileStatus::Added,
            &[],
            &["new".into(), "target".into()],
            None,
            false,
            vec![],
        );
        state.replace_file("src/a.rs", Some(restored), false);
        assert!(!state.comments[0].is_file_level());
        assert_eq!(state.comments[0].sorted_rows(), (1, 1));
    }

    #[test]
    fn physical_deletion_removes_comments_but_keeps_tracked_deleted_diff() {
        let mut state = state_with_added_lines(&["target"]);
        state.add_comment("remove me").unwrap();
        let deleted = create_review_file(
            "src/a.rs".into(),
            FileStatus::Deleted,
            &["base".into()],
            &[],
            None,
            false,
            vec![],
        );

        let outcome = state.replace_file("src/a.rs", Some(deleted), true);

        assert_eq!(outcome.deleted_comments, 1);
        assert!(state.comments.is_empty());
        assert_eq!(state.files[0].status, FileStatus::Deleted);
    }

    #[test]
    fn refresh_follows_rename_for_comment_and_selection() {
        let mut state = state_with_added_lines(&["target"]);
        let id = state.add_comment("rename").unwrap();
        state.select_comment(id).unwrap();
        let renamed = create_review_file(
            "src/b.rs".into(),
            FileStatus::Renamed,
            &[],
            &["target".into()],
            Some("src/a.rs".into()),
            false,
            vec![],
        );

        state.replace_file("src/a.rs", Some(renamed), false);

        assert_eq!(state.comments[0].file_path, "src/b.rs");
        assert!(matches!(
            state.selection,
            Some(Selection::Comment { ref file_path, id: selected })
                if file_path == "src/b.rs" && selected == id
        ));
    }
}
