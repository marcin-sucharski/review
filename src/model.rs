use similar::{Algorithm, ChangeTag, TextDiff};

use crate::syntax::language_for_path;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ReviewKind {
    Uncommitted,
    Branch,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReviewSource {
    pub kind: ReviewKind,
    pub target_branch: Option<String>,
    pub base_ref: String,
}

impl ReviewSource {
    #[must_use]
    pub fn label(&self) -> String {
        match self.kind {
            ReviewKind::Branch => format!(
                "branch comparison against {}",
                self.target_branch.as_deref().unwrap_or("unknown")
            ),
            ReviewKind::Uncommitted => "uncommitted changes".to_owned(),
        }
    }

    #[must_use]
    pub const fn kind_name(&self) -> &'static str {
        match self.kind {
            ReviewKind::Uncommitted => "uncommitted",
            ReviewKind::Branch => "branch",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LineKind {
    Context,
    Addition,
    Deletion,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReviewLine {
    pub index: usize,
    pub kind: LineKind,
    pub text: String,
    pub old_line: Option<usize>,
    pub new_line: Option<usize>,
}

impl ReviewLine {
    #[must_use]
    pub const fn marker(&self) -> char {
        match self.kind {
            LineKind::Addition => '+',
            LineKind::Deletion => '-',
            LineKind::Context => ' ',
        }
    }

    #[must_use]
    pub const fn primary_line(&self) -> Option<usize> {
        match self.new_line {
            Some(line) => Some(line),
            None => self.old_line,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct VisibleInterval {
    pub start: usize,
    pub end: usize,
}

impl VisibleInterval {
    #[must_use]
    pub const fn contains(self, index: usize) -> bool {
        self.start <= index && index <= self.end
    }

    #[must_use]
    pub const fn touches(self, other: Self) -> bool {
        self.start <= other.end.saturating_add(1) && other.start <= self.end.saturating_add(1)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FileStatus {
    Unchanged,
    Modified,
    Added,
    Deleted,
    Renamed,
    Binary,
    Mode,
    TypeChanged,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReviewFile {
    pub path: String,
    pub old_path: Option<String>,
    pub status: FileStatus,
    pub language: String,
    pub lines: Vec<ReviewLine>,
    pub binary: bool,
    pub metadata: Vec<String>,
    pub visible_intervals: Vec<VisibleInterval>,
}

impl ReviewFile {
    #[must_use]
    pub fn display_path(&self) -> String {
        self.old_path.as_ref().map_or_else(
            || self.path.clone(),
            |old| {
                if old == &self.path {
                    self.path.clone()
                } else {
                    format!("{old} -> {}", self.path)
                }
            },
        )
    }

    #[must_use]
    pub const fn status_marker(&self) -> char {
        match self.status {
            FileStatus::Unchanged => '=',
            FileStatus::Modified | FileStatus::Mode => 'M',
            FileStatus::Added => 'A',
            FileStatus::Deleted => 'D',
            FileStatus::Renamed => 'R',
            FileStatus::Binary => 'B',
            FileStatus::TypeChanged => 'T',
        }
    }

    #[must_use]
    pub fn first_visible_row(&self) -> Option<usize> {
        self.visible_intervals
            .iter()
            .flat_map(|interval| interval.start..=interval.end)
            .find(|index| *index < self.lines.len())
    }

    pub fn add_visible_interval(&mut self, start: usize, end: usize) {
        if self.lines.is_empty() {
            return;
        }
        let new = VisibleInterval {
            start: start.min(self.lines.len() - 1),
            end: end.min(self.lines.len() - 1),
        };
        if new.start > new.end {
            return;
        }
        self.visible_intervals.push(new);
        self.visible_intervals
            .sort_by_key(|interval| interval.start);
        let mut merged: Vec<VisibleInterval> = Vec::with_capacity(self.visible_intervals.len());
        for interval in self.visible_intervals.drain(..) {
            match merged.last_mut() {
                Some(last) if last.touches(interval) => {
                    last.start = last.start.min(interval.start);
                    last.end = last.end.max(interval.end);
                    continue;
                }
                _ => {}
            }
            merged.push(interval);
        }
        self.visible_intervals = merged;
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CommentPlacement {
    Lines {
        start_row: usize,
        end_row: usize,
        selected_lines: Vec<ReviewLine>,
    },
    File {
        preferred_start_row: usize,
        preferred_end_row: usize,
        selected_lines: Vec<ReviewLine>,
    },
}

impl CommentPlacement {
    #[must_use]
    pub const fn sorted_rows(&self) -> (usize, usize) {
        let (start, end) = match self {
            Self::Lines {
                start_row, end_row, ..
            } => (*start_row, *end_row),
            Self::File {
                preferred_start_row,
                preferred_end_row,
                ..
            } => (*preferred_start_row, *preferred_end_row),
        };
        if start <= end {
            (start, end)
        } else {
            (end, start)
        }
    }

    #[must_use]
    pub fn selected_lines(&self) -> &[ReviewLine] {
        match self {
            Self::Lines { selected_lines, .. } | Self::File { selected_lines, .. } => {
                selected_lines
            }
        }
    }

    #[must_use]
    pub const fn is_file_level(&self) -> bool {
        matches!(self, Self::File { .. })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReviewComment {
    pub id: u64,
    pub file_path: String,
    pub placement: CommentPlacement,
    pub body: String,
    pub order: u64,
}

impl ReviewComment {
    #[must_use]
    pub const fn sorted_rows(&self) -> (usize, usize) {
        self.placement.sorted_rows()
    }

    #[must_use]
    pub fn selected_lines(&self) -> &[ReviewLine] {
        self.placement.selected_lines()
    }

    #[must_use]
    pub const fn is_file_level(&self) -> bool {
        self.placement.is_file_level()
    }
}

#[must_use]
pub fn create_review_file(
    path: String,
    status: FileStatus,
    old_lines: &[String],
    new_lines: &[String],
    old_path: Option<String>,
    binary: bool,
    metadata: Vec<String>,
) -> ReviewFile {
    let lines = if binary {
        Vec::new()
    } else {
        build_review_lines(old_lines, new_lines)
    };
    let visible_intervals = initial_visible_intervals(&lines, 20, 180);
    ReviewFile {
        language: language_for_path(&path).to_owned(),
        path,
        old_path,
        status: if binary { FileStatus::Binary } else { status },
        lines,
        binary,
        metadata,
        visible_intervals,
    }
}

#[must_use]
pub fn build_review_lines(old_lines: &[String], new_lines: &[String]) -> Vec<ReviewLine> {
    let old_refs = old_lines.iter().map(String::as_str).collect::<Vec<_>>();
    let new_refs = new_lines.iter().map(String::as_str).collect::<Vec<_>>();
    let diff = TextDiff::configure()
        .algorithm(Algorithm::Patience)
        .diff_slices(&old_refs, &new_refs);
    let mut rows = Vec::with_capacity(old_lines.len().max(new_lines.len()));
    for change in diff.iter_all_changes() {
        let (kind, old_line, new_line) = match change.tag() {
            ChangeTag::Equal => (
                LineKind::Context,
                change.old_index().map(|index| index + 1),
                change.new_index().map(|index| index + 1),
            ),
            ChangeTag::Delete => (
                LineKind::Deletion,
                change.old_index().map(|index| index + 1),
                None,
            ),
            ChangeTag::Insert => (
                LineKind::Addition,
                None,
                change.new_index().map(|index| index + 1),
            ),
        };
        rows.push(ReviewLine {
            index: rows.len(),
            kind,
            text: (*change.value()).to_owned(),
            old_line,
            new_line,
        });
    }
    rows
}

#[must_use]
pub fn initial_visible_intervals(
    lines: &[ReviewLine],
    context_radius: usize,
    full_file_threshold: usize,
) -> Vec<VisibleInterval> {
    if lines.is_empty() {
        return Vec::new();
    }
    if lines.len() <= full_file_threshold {
        return vec![VisibleInterval {
            start: 0,
            end: lines.len() - 1,
        }];
    }
    let changed = lines
        .iter()
        .filter(|line| matches!(line.kind, LineKind::Addition | LineKind::Deletion))
        .map(|line| line.index)
        .collect::<Vec<_>>();
    if changed.is_empty() {
        return vec![VisibleInterval {
            start: 0,
            end: (full_file_threshold - 1).min(lines.len() - 1),
        }];
    }
    let mut intervals = changed
        .into_iter()
        .map(|index| VisibleInterval {
            start: index.saturating_sub(context_radius),
            end: index.saturating_add(context_radius).min(lines.len() - 1),
        })
        .collect::<Vec<_>>();
    intervals.sort_by_key(|interval| interval.start);
    let mut merged: Vec<VisibleInterval> = Vec::with_capacity(intervals.len());
    for interval in intervals {
        match merged.last_mut() {
            Some(last) if last.touches(interval) => {
                last.end = last.end.max(interval.end);
                continue;
            }
            _ => {}
        }
        merged.push(interval);
    }
    merged
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(values: &[&str]) -> Vec<String> {
        values.iter().map(ToString::to_string).collect()
    }

    #[test]
    fn patience_diff_tracks_line_numbers_and_replacements() {
        let rows = build_review_lines(
            &strings(&["same", "old", "tail"]),
            &strings(&["same", "new", "tail"]),
        );
        assert_eq!(rows.len(), 4);
        assert_eq!(
            (rows[1].kind, rows[1].old_line, rows[1].new_line),
            (LineKind::Deletion, Some(2), None)
        );
        assert_eq!(
            (rows[2].kind, rows[2].old_line, rows[2].new_line),
            (LineKind::Addition, None, Some(2))
        );
    }

    #[test]
    fn large_files_show_context_and_expansion_gaps() {
        let mut lines = (0..300)
            .map(|index| ReviewLine {
                index,
                kind: LineKind::Context,
                text: index.to_string(),
                old_line: Some(index + 1),
                new_line: Some(index + 1),
            })
            .collect::<Vec<_>>();
        lines[150].kind = LineKind::Addition;
        let visible = initial_visible_intervals(&lines, 20, 180);
        assert_eq!(
            visible,
            vec![VisibleInterval {
                start: 130,
                end: 170
            }]
        );
    }

    #[test]
    fn visible_intervals_merge_when_expanded() {
        let lines = build_review_lines(&[], &strings(&["a", "b", "c", "d", "e"]));
        let mut file = create_review_file(
            "a.rs".into(),
            FileStatus::Added,
            &[],
            &strings(&["a", "b", "c", "d", "e"]),
            None,
            false,
            vec![],
        );
        file.visible_intervals.clear();
        file.add_visible_interval(0, 1);
        file.add_visible_interval(3, 4);
        file.add_visible_interval(2, 2);
        assert_eq!(
            file.visible_intervals,
            vec![VisibleInterval {
                start: 0,
                end: lines.len() - 1
            }]
        );
    }
}
