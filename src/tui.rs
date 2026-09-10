use std::collections::HashMap;
use std::io::{self, IsTerminal, Stdout, Write};
use std::time::{Duration, Instant};

use crossterm::cursor::{Hide, MoveTo, Show};
use crossterm::event::{
    self, DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture,
    Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use crossterm::style::{
    Attribute, Color, Print, ResetColor, SetAttribute, SetBackgroundColor, SetForegroundColor,
};
use crossterm::terminal::{
    BeginSynchronizedUpdate, EndSynchronizedUpdate, EnterAlternateScreen, LeaveAlternateScreen,
    disable_raw_mode, enable_raw_mode, size,
};
use crossterm::{execute, queue};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::error::{Result, ReviewError};
use crate::file_tree::{FileTreeKind, build_file_tree};
use crate::git::refresh_reviewed_files;
use crate::model::{FileStatus, LineKind, ReviewComment, ReviewFile};
use crate::state::{Activation, DocumentItem, DocumentKind, ReviewState, Selection};
use crate::syntax::{
    ATTRIBUTE_FOREGROUND, COMMENT_FOREGROUND, DEFAULT_FOREGROUND, EMPHASIS_FOREGROUND,
    ERROR_FOREGROUND, FUNCTION_FOREGROUND, HEADING_FOREGROUND, HighlightedSpan, KEYWORD_FOREGROUND,
    NUMBER_FOREGROUND, OPERATOR_FOREGROUND, PUNCTUATION_FOREGROUND, STRING_FOREGROUND,
    SyntaxHighlighter, TAG_FOREGROUND, TYPE_FOREGROUND,
};
use crate::tmux::inside_tmux;
use crate::watch::{FileMonitor, MonitorBatch};

const GUTTER_WIDTH: usize = 9;
const MOUSE_SCROLL_LINES: usize = 3;
const ADDITION_BACKGROUND: Color = Color::AnsiValue(194);
const ADDITION_SELECTION_BACKGROUND: Color = Color::AnsiValue(193);
const DELETION_BACKGROUND: Color = Color::AnsiValue(224);
const DELETION_SELECTION_BACKGROUND: Color = Color::AnsiValue(223);
const COMMENT_BACKGROUND: Color = Color::AnsiValue(230);
const SEARCH_BACKGROUND: Color = Color::AnsiValue(226);
const SELECTION_BACKGROUND: Color = Color::AnsiValue(229);
const INITIAL_STATUS: &str = "Tab switches panes. T toggles left pane. z centers code. :q quits.";
const INTERRUPT_WARNING: &str = "Press Ctrl+C again to quit review.";
const EVENT_POLL_INTERVAL: Duration = Duration::from_millis(50);
const REFRESH_RETRY_DELAY: Duration = Duration::from_millis(250);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Focus {
    Review,
    Files,
    Comments,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct Style {
    foreground: Option<Color>,
    background: Option<Color>,
    bold: bool,
    italic: bool,
    reverse: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Segment {
    text: String,
    style: Style,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct StyledLine {
    segments: Vec<Segment>,
    fill_background: Option<Color>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct FrameRegion {
    x: u16,
    width: u16,
    line: StyledLine,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct RenderFrame {
    width: u16,
    height: u16,
    rows: Vec<Vec<FrameRegion>>,
    cursor: Option<(u16, u16)>,
}

impl RenderFrame {
    fn new(width: u16, height: u16) -> Self {
        Self {
            width,
            height,
            rows: vec![Vec::new(); usize::from(height)],
            cursor: None,
        }
    }

    fn draw_region(&mut self, x: u16, y: u16, width: u16, line: &StyledLine) {
        if y >= self.height || x >= self.width || width == 0 {
            return;
        }
        self.rows[usize::from(y)].push(FrameRegion {
            x,
            width: width.min(self.width - x),
            line: line.clone(),
        });
    }
}

impl StyledLine {
    fn plain(text: impl Into<String>, style: Style) -> Self {
        Self {
            segments: vec![Segment {
                text: text.into(),
                style,
            }],
            fill_background: style.background,
        }
    }

    fn push(&mut self, text: impl Into<String>, style: Style) {
        let text = text.into();
        if text.is_empty() {
            return;
        }
        match self.segments.last_mut() {
            Some(last) if last.style == style => last.text.push_str(&text),
            _ => self.segments.push(Segment { text, style }),
        }
    }
}

#[derive(Clone, Debug)]
enum LeftHit {
    File(usize),
    Comment(u64),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum MouseRegion {
    FileTree,
    Comments,
    Review,
    Outside,
}

#[derive(Clone, Debug)]
struct CommentPaneRow {
    file_path: String,
    comment: Option<ReviewComment>,
}

struct TerminalGuard;

impl TerminalGuard {
    fn enter(output: &mut Stdout) -> Result<Self> {
        enable_raw_mode()
            .map_err(|error| ReviewError::io("could not enable terminal raw mode", error))?;
        let guard = Self;
        execute!(
            output,
            EnterAlternateScreen,
            EnableMouseCapture,
            EnableBracketedPaste,
            Hide
        )
        .map_err(|error| ReviewError::io("could not initialize terminal UI", error))?;
        Ok(guard)
    }
}

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        let mut output = io::stdout();
        let _ = execute!(
            output,
            ResetColor,
            SetAttribute(Attribute::Reset),
            Show,
            DisableBracketedPaste,
            DisableMouseCapture,
            LeaveAlternateScreen
        );
    }
}

pub struct ReviewApp<'a> {
    state: &'a mut ReviewState,
    focus: Focus,
    file_pane_visible: bool,
    file_scroll: usize,
    file_scroll_follows_selection: bool,
    comment_scroll: usize,
    comment_scroll_follows_selection: bool,
    comment_pane_index: usize,
    review_scroll: usize,
    status: String,
    interrupt_armed: bool,
    command_mode: bool,
    command_buffer: String,
    search_mode: bool,
    search_buffer: String,
    search_query: String,
    comment_mode: bool,
    comment_buffer: String,
    comment_cursor: usize,
    comment_goal_column: Option<usize>,
    comment_editor_scroll: usize,
    editing_comment_id: Option<u64>,
    quit_requested: bool,
    cancel_requested: bool,
    syntax_highlighter: SyntaxHighlighter,
    highlighted: HashMap<(String, usize), Vec<HighlightedSpan>>,
    screen_map: HashMap<u16, usize>,
    left_hit_map: HashMap<u16, LeftHit>,
    cursor: Option<(u16, u16)>,
    last_width: u16,
    last_height: u16,
    last_left_width: u16,
    previous_frame: Option<RenderFrame>,
    monitor: Option<FileMonitor>,
    refresh_retry: Option<(Instant, MonitorBatch)>,
}

impl<'a> ReviewApp<'a> {
    #[must_use]
    pub fn new(state: &'a mut ReviewState) -> Self {
        Self {
            state,
            focus: Focus::Review,
            file_pane_visible: false,
            file_scroll: 0,
            file_scroll_follows_selection: true,
            comment_scroll: 0,
            comment_scroll_follows_selection: true,
            comment_pane_index: 0,
            review_scroll: 0,
            status: INITIAL_STATUS.to_owned(),
            interrupt_armed: false,
            command_mode: false,
            command_buffer: String::new(),
            search_mode: false,
            search_buffer: String::new(),
            search_query: String::new(),
            comment_mode: false,
            comment_buffer: String::new(),
            comment_cursor: 0,
            comment_goal_column: None,
            comment_editor_scroll: 0,
            editing_comment_id: None,
            quit_requested: false,
            cancel_requested: false,
            syntax_highlighter: SyntaxHighlighter::default(),
            highlighted: HashMap::new(),
            screen_map: HashMap::new(),
            left_hit_map: HashMap::new(),
            cursor: None,
            last_width: 80,
            last_height: 24,
            last_left_width: 0,
            previous_frame: None,
            monitor: None,
            refresh_retry: None,
        }
    }

    pub fn run(&mut self) -> Result<()> {
        if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
            return Err(ReviewError::Message(
                "interactive TUI requires a terminal".to_owned(),
            ));
        }
        let mut output = io::stdout();
        let _guard = TerminalGuard::enter(&mut output)?;
        if !self.state.source.is_snapshot() {
            match FileMonitor::new(&self.state.repository_root) {
                Ok(monitor) => self.monitor = Some(monitor),
                Err(error) => self.status = format!("Monitoring unavailable: {error}"),
            }
        }
        self.draw(&mut output)?;
        while !self.quit_requested {
            let mut redraw = false;
            if event::poll(EVENT_POLL_INTERVAL)
                .map_err(|error| ReviewError::io("could not poll terminal input", error))?
            {
                let event = event::read()
                    .map_err(|error| ReviewError::io("could not read terminal input", error))?;
                self.handle_event(event);
                redraw = true;
            }
            redraw |= self.process_file_changes();
            if redraw && !self.quit_requested {
                self.draw(&mut output)?;
            }
        }
        if self.cancel_requested {
            Err(ReviewError::Cancelled)
        } else {
            Ok(())
        }
    }

    fn process_file_changes(&mut self) -> bool {
        let reviewed_paths = self
            .state
            .files
            .iter()
            .flat_map(|file| {
                std::iter::once(file.path.clone()).chain(file.old_path.iter().cloned())
            })
            .collect::<Vec<_>>();
        let retry_ready = self
            .refresh_retry
            .as_ref()
            .is_some_and(|(deadline, _)| Instant::now() >= *deadline);
        let (batch, is_retry) = if retry_ready {
            let (_, batch) = self.refresh_retry.take().expect("checked above");
            (Some(batch), true)
        } else {
            (
                self.monitor
                    .as_mut()
                    .and_then(|monitor| monitor.poll(&reviewed_paths)),
                false,
            )
        };
        let Some(batch) = batch else {
            return false;
        };
        let result = refresh_reviewed_files(
            &self.state.repository_root,
            &self.state.source,
            &self.state.files,
            &batch.paths,
            batch.refresh_all,
        );
        let refreshes = match result {
            Ok(refreshes) => refreshes,
            Err(error) => {
                self.status = format!("Could not reload reviewed files: {error}");
                if !is_retry {
                    self.refresh_retry = Some((Instant::now() + REFRESH_RETRY_DELAY, batch));
                }
                return true;
            }
        };
        let mut moved = 0;
        let mut detached = 0;
        let mut deleted = 0;
        for refresh in refreshes {
            let new_path = refresh.file.as_ref().map(|file| file.path.clone());
            let outcome = self.state.replace_file(
                &refresh.old_path,
                refresh.file,
                refresh.physically_deleted,
            );
            moved += outcome.moved_comments;
            detached += outcome.detached_comments;
            deleted += outcome.deleted_comments;
            self.highlighted.retain(|(path, _), _| {
                path != &refresh.old_path && new_path.as_ref() != Some(path)
            });
        }
        if self.comment_mode {
            let edited_exists = self
                .editing_comment_id
                .is_none_or(|id| self.state.comments.iter().any(|comment| comment.id == id));
            if !edited_exists
                || (self.editing_comment_id.is_none() && self.state.selected_range().is_none())
            {
                self.close_comment();
            }
        }
        self.sync_comment_selection();
        self.ensure_selected_visible();
        self.previous_frame = None;
        self.status = batch.warning.unwrap_or_else(|| {
            format!(
                "Files reloaded: {moved} comments moved, {detached} detached, {deleted} deleted."
            )
        });
        true
    }

    fn draw(&mut self, output: &mut Stdout) -> Result<()> {
        let (width, height) = size().unwrap_or((80, 24));
        self.last_width = width;
        self.last_height = height;
        self.screen_map.clear();
        self.left_hit_map.clear();
        self.cursor = None;
        let mut frame = RenderFrame::new(width, height);
        if width < 60 || height < 12 {
            let line = StyledLine::plain(
                "Terminal is too small for review. Resize to at least 60x12.",
                Style {
                    foreground: Some(Color::DarkYellow),
                    bold: true,
                    ..Style::default()
                },
            );
            draw_region_line(&mut frame, 0, 0, width, &line);
            return self.present_frame(output, frame);
        }

        let content_height = height - 1;
        let left_width = if self.file_pane_visible {
            (width / 3).clamp(24, 42)
        } else {
            0
        };
        self.last_left_width = left_width;
        if self.file_pane_visible {
            self.draw_left_pane(&mut frame, left_width, content_height);
            for y in 0..content_height {
                draw_region_line(
                    &mut frame,
                    left_width,
                    y,
                    1,
                    &StyledLine::plain(
                        "│",
                        Style {
                            foreground: Some(Color::DarkBlue),
                            ..Style::default()
                        },
                    ),
                );
            }
        } else if matches!(self.focus, Focus::Files | Focus::Comments) {
            self.focus = Focus::Review;
        }
        let review_x = left_width + u16::from(self.file_pane_visible);
        self.draw_review(&mut frame, review_x, width - review_x, content_height);
        self.draw_status(&mut frame, height - 1, width);
        frame.cursor = self
            .cursor
            .map(|(x, y)| (x.min(width - 1), y.min(height - 1)));
        self.present_frame(output, frame)
    }

    fn present_frame(&mut self, output: &mut Stdout, frame: RenderFrame) -> Result<()> {
        render_frame(output, &frame, self.previous_frame.as_ref())?;
        output
            .flush()
            .map_err(|error| ReviewError::io("could not refresh terminal UI", error))?;
        self.previous_frame = Some(frame);
        Ok(())
    }

    fn draw_left_pane(&mut self, frame: &mut RenderFrame, width: u16, content_height: u16) {
        let file_height = (content_height / 2).max(3);
        let comment_height = content_height - file_height;
        self.draw_file_tree(frame, 0, file_height, width);
        self.draw_comment_pane(frame, file_height, comment_height, width);
    }

    fn draw_file_tree(&mut self, frame: &mut RenderFrame, start_y: u16, height: u16, width: u16) {
        let header_style = pane_header_style(self.focus == Focus::Files);
        draw_region_line(
            frame,
            0,
            start_y,
            width,
            &full_width_line(" Files", width, header_style),
        );
        if height <= 1 {
            return;
        }
        let rows = build_file_tree(&self.state.files);
        let selected_row = rows
            .iter()
            .position(|row| row.file_index == Some(self.state.file_pane_index))
            .unwrap_or(0);
        let body_height = usize::from(height - 1);
        if self.file_scroll_follows_selection {
            let reserves_footer = rows.len() > body_height || self.file_scroll > 0;
            let scroll_height = body_height.saturating_sub(usize::from(reserves_footer));
            ensure_scroll(&mut self.file_scroll, selected_row, scroll_height);
        } else {
            clamp_pane_scroll(&mut self.file_scroll, rows.len(), body_height);
        }
        let above = self.file_scroll;
        let remaining = rows.len().saturating_sub(self.file_scroll);
        let footer_needed = above > 0 || remaining > body_height;
        let visible_height = body_height.saturating_sub(usize::from(footer_needed));
        for (slot, row) in rows
            .iter()
            .skip(self.file_scroll)
            .take(visible_height)
            .enumerate()
        {
            let y = start_y + 1 + u16::try_from(slot).unwrap_or(u16::MAX);
            let selected = row.file_index == Some(self.state.file_pane_index);
            let line = if let Some(file_index) = row.file_index {
                let count = self
                    .state
                    .comments
                    .iter()
                    .filter(|comment| comment.file_path == self.state.files[file_index].path)
                    .count();
                self.left_hit_map.insert(y, LeftHit::File(file_index));
                file_tree_file_line(
                    &self.state.files[file_index],
                    &row.label,
                    row.depth,
                    count,
                    selected,
                    self.focus == Focus::Files,
                    width,
                )
            } else if row.kind == FileTreeKind::Directory {
                StyledLine::plain(
                    format!("{}▸ {}", "  ".repeat(row.depth), row.label),
                    Style {
                        foreground: Some(Color::DarkCyan),
                        bold: true,
                        ..Style::default()
                    },
                )
            } else {
                StyledLine::plain(
                    format!("{}? {}", "  ".repeat(row.depth), row.label),
                    Style::default(),
                )
            };
            draw_region_line(frame, 0, y, width, &line);
        }
        if footer_needed {
            let below = rows.len().saturating_sub(self.file_scroll + visible_height);
            let text = scroll_footer(above, below);
            draw_region_line(
                frame,
                0,
                start_y + height - 1,
                width,
                &StyledLine::plain(
                    text,
                    Style {
                        foreground: Some(Color::DarkBlue),
                        ..Style::default()
                    },
                ),
            );
        }
    }

    fn comment_pane_rows(&self) -> Vec<CommentPaneRow> {
        let mut rows = Vec::new();
        for file in &self.state.files {
            let comments = self.state.comments_for_file(&file.path);
            if comments.is_empty() {
                continue;
            }
            rows.push(CommentPaneRow {
                file_path: file.path.clone(),
                comment: None,
            });
            rows.extend(comments.into_iter().map(|comment| CommentPaneRow {
                file_path: file.path.clone(),
                comment: Some(comment.clone()),
            }));
        }
        rows
    }

    fn draw_comment_pane(
        &mut self,
        frame: &mut RenderFrame,
        start_y: u16,
        height: u16,
        width: u16,
    ) {
        if height == 0 {
            return;
        }
        draw_region_line(
            frame,
            0,
            start_y,
            width,
            &StyledLine::plain(
                pad_to_width(" Comments", width),
                pane_header_style(self.focus == Focus::Comments),
            ),
        );
        if height <= 1 {
            return;
        }
        let rows = self.comment_pane_rows();
        if rows.is_empty() {
            draw_region_line(
                frame,
                0,
                start_y + 1,
                width,
                &StyledLine::plain(
                    "  No comments yet",
                    Style {
                        foreground: Some(Color::DarkBlue),
                        ..Style::default()
                    },
                ),
            );
            return;
        }
        self.comment_pane_index = self.comment_pane_index.min(rows.len() - 1);
        if rows[self.comment_pane_index].comment.is_none() {
            self.comment_pane_index = rows
                .iter()
                .position(|row| row.comment.is_some())
                .unwrap_or(0);
        }
        let body_height = usize::from(height - 1);
        if self.comment_scroll_follows_selection {
            let reserves_footer = rows.len() > body_height || self.comment_scroll > 0;
            let scroll_height = body_height.saturating_sub(usize::from(reserves_footer));
            ensure_scroll(
                &mut self.comment_scroll,
                self.comment_pane_index,
                scroll_height,
            );
        } else {
            clamp_pane_scroll(&mut self.comment_scroll, rows.len(), body_height);
        }
        let footer_needed =
            self.comment_scroll > 0 || rows.len().saturating_sub(self.comment_scroll) > body_height;
        let visible_height = body_height.saturating_sub(usize::from(footer_needed));
        for (slot, row) in rows
            .iter()
            .skip(self.comment_scroll)
            .take(visible_height)
            .enumerate()
        {
            let y = start_y + 1 + u16::try_from(slot).unwrap_or(u16::MAX);
            let (text, style) = if let Some(comment) = &row.comment {
                self.left_hit_map.insert(y, LeftHit::Comment(comment.id));
                let selected = self.comment_pane_index == self.comment_scroll + slot;
                let (start, end) = comment.sorted_rows();
                let line_label = if comment.is_file_level() {
                    "File".to_owned()
                } else if start == end {
                    format!("L{}", selected_line_number(comment))
                } else {
                    format!(
                        "L{}-{}",
                        selected_line_number(comment),
                        selected_end_line_number(comment)
                    )
                };
                let preview = comment.body.lines().next().unwrap_or_default();
                (
                    format!("  {line_label} {preview}"),
                    if selected {
                        selection_style(self.focus == Focus::Comments)
                    } else {
                        Style::default()
                    },
                )
            } else {
                (
                    format!(" {}", row.file_path),
                    Style {
                        foreground: Some(Color::DarkCyan),
                        bold: true,
                        ..Style::default()
                    },
                )
            };
            let line = if style.reverse {
                full_width_line(text, width, style)
            } else {
                StyledLine::plain(text, style)
            };
            draw_region_line(frame, 0, y, width, &line);
        }
        if footer_needed {
            let below = rows
                .len()
                .saturating_sub(self.comment_scroll + visible_height);
            draw_region_line(
                frame,
                0,
                start_y + height - 1,
                width,
                &StyledLine::plain(
                    scroll_footer(self.comment_scroll, below),
                    Style {
                        foreground: Some(Color::DarkBlue),
                        ..Style::default()
                    },
                ),
            );
        }
    }

    fn draw_review(&mut self, frame: &mut RenderFrame, x: u16, width: u16, height: u16) {
        let items = self.state.document_items();
        if items.is_empty() {
            return;
        }
        self.review_scroll = self.review_scroll.min(items.len() - 1);
        let mut y = 0_u16;
        let header = (items[self.review_scroll].kind != DocumentKind::FileHeader)
            .then(|| sticky_header(&items, self.review_scroll))
            .flatten();
        if let Some(header) = header {
            let line = self.file_header_line(&header.text, false);
            draw_region_line(frame, x, y, width, &line);
            y += 1;
        }
        for (index, item) in items.iter().enumerate().skip(self.review_scroll) {
            if y >= height {
                break;
            }
            let selected = self.state.item_is_selected(item);
            let editing_saved_comment = self.comment_mode
                && item
                    .comment
                    .as_ref()
                    .is_some_and(|comment| Some(comment.id) == self.editing_comment_id);
            let (lines, cursor_columns) = self.review_item_lines(item, width, selected);
            let visible_lines = if editing_saved_comment {
                self.comment_editor_window(
                    lines.into_iter().zip(cursor_columns).collect(),
                    usize::from(height.saturating_sub(y)),
                )
            } else {
                lines.into_iter().zip(cursor_columns).collect()
            };
            for (line, cursor_column) in visible_lines {
                if y >= height {
                    break;
                }
                draw_region_line(frame, x, y, width, &line);
                self.screen_map.insert(y, index);
                if let Some(column) = cursor_column {
                    self.cursor = Some((x + column.min(width.saturating_sub(1)), y));
                }
                y += 1;
            }
            if self.should_draw_new_comment_input(item) {
                let editor_lines = self.comment_editor_lines(width);
                let editor =
                    self.comment_editor_window(editor_lines, usize::from(height.saturating_sub(y)));
                for (line, cursor_column) in editor {
                    if y >= height {
                        break;
                    }
                    draw_region_line(frame, x, y, width, &line);
                    self.screen_map.insert(y, index);
                    if let Some(column) = cursor_column {
                        self.cursor = Some((x + column.min(width.saturating_sub(1)), y));
                    }
                    y += 1;
                }
            }
        }
    }

    fn review_item_lines(
        &mut self,
        item: &DocumentItem,
        width: u16,
        selected: bool,
    ) -> (Vec<StyledLine>, Vec<Option<u16>>) {
        let lines = match item.kind {
            DocumentKind::FileHeader => vec![self.file_header_line(&item.text, selected)],
            DocumentKind::Metadata => vec![StyledLine::plain(
                format!("  {}", item.text),
                Style {
                    foreground: Some(Color::DarkYellow),
                    ..Style::default()
                },
            )],
            DocumentKind::Expansion => vec![full_width_line(
                format!("       ⋯ {}", item.text),
                width,
                Style {
                    foreground: Some(Color::DarkBlue),
                    reverse: selected,
                    ..Style::default()
                },
            )],
            DocumentKind::Comment => {
                if self.comment_mode
                    && item
                        .comment
                        .as_ref()
                        .is_some_and(|comment| Some(comment.id) == self.editing_comment_id)
                {
                    return split_editor_result(self.comment_editor_lines(width));
                }
                self.saved_comment_lines(item, width, selected)
            }
            DocumentKind::Code => self.code_lines(item, width),
        };
        let cursors = vec![None; lines.len()];
        (lines, cursors)
    }

    fn file_header_line(&self, text: &str, _selected: bool) -> StyledLine {
        StyledLine::plain(
            format!(" {text}"),
            Style {
                foreground: Some(Color::DarkCyan),
                bold: true,
                ..Style::default()
            },
        )
    }

    fn code_lines(&mut self, item: &DocumentItem, width: u16) -> Vec<StyledLine> {
        let Some(line) = item.line.as_ref() else {
            return Vec::new();
        };
        let row = item.row_index.unwrap_or_default();
        let selected = self.state.is_row_selected(&item.file_path, row);
        let base_background = code_background(selected, line.kind);
        let number = line
            .primary_line()
            .map_or_else(String::new, |line| line.to_string());
        let has_comment = self.state.comments.iter().any(|comment| {
            comment.file_path == item.file_path
                && comment.sorted_rows().0 <= row
                && row <= comment.sorted_rows().1
        });
        let rail = if has_comment { '┃' } else { '│' };
        let content_width = usize::from(width).saturating_sub(GUTTER_WIDTH).max(1);
        let cache_key = (item.file_path.clone(), row);
        let spans = if let Some(spans) = self.highlighted.get(&cache_key) {
            spans.clone()
        } else {
            let language = self
                .state
                .file_by_path(&item.file_path)
                .map_or("text", |file| file.language.as_str());
            let spans =
                self.syntax_highlighter
                    .highlight_line(&item.file_path, language, &line.text);
            self.highlighted.insert(cache_key, spans.clone());
            spans
        };
        let wrapped = wrap_highlighted(
            &spans,
            content_width,
            base_background,
            &line.text,
            &self.search_query,
        );
        wrapped
            .into_iter()
            .enumerate()
            .map(|(index, segments)| {
                let mut result = StyledLine {
                    segments: Vec::new(),
                    fill_background: base_background,
                };
                let (number_text, marker) = if index == 0 {
                    (format!("{number:>5} "), line.marker())
                } else {
                    ("      ".to_owned(), ' ')
                };
                let line_number_color = if base_background.is_some() {
                    Color::Black
                } else {
                    Color::DarkBlue
                };
                result.push(
                    number_text,
                    Style {
                        foreground: Some(line_number_color),
                        background: base_background,
                        ..Style::default()
                    },
                );
                result.push(
                    rail.to_string(),
                    Style {
                        foreground: Some(if has_comment {
                            Color::DarkYellow
                        } else {
                            line_number_color
                        }),
                        background: base_background,
                        bold: has_comment,
                        ..Style::default()
                    },
                );
                result.push(
                    format!("{marker} "),
                    Style {
                        foreground: Some(line_number_color),
                        background: base_background,
                        ..Style::default()
                    },
                );
                result.segments.extend(segments);
                result
            })
            .collect()
    }

    fn saved_comment_lines(
        &self,
        item: &DocumentItem,
        width: u16,
        selected: bool,
    ) -> Vec<StyledLine> {
        let content_width = usize::from(width).saturating_sub(GUTTER_WIDTH).max(1);
        let background = if selected {
            SELECTION_BACKGROUND
        } else {
            COMMENT_BACKGROUND
        };
        let body = item
            .comment
            .as_ref()
            .map_or(item.text.as_str(), |comment| comment.body.as_str());
        wrap_plain_lines(body, content_width)
            .into_iter()
            .map(|text| {
                let mut line = StyledLine {
                    segments: Vec::new(),
                    fill_background: Some(background),
                };
                line.push(
                    "      ┃  ",
                    Style {
                        foreground: Some(Color::DarkYellow),
                        background: Some(background),
                        bold: true,
                        ..Style::default()
                    },
                );
                line.push(
                    text,
                    Style {
                        foreground: Some(Color::Black),
                        background: Some(background),
                        ..Style::default()
                    },
                );
                line
            })
            .collect()
    }

    fn should_draw_new_comment_input(&self, item: &DocumentItem) -> bool {
        if !self.comment_mode
            || self.editing_comment_id.is_some()
            || item.kind != DocumentKind::Code
        {
            return false;
        }
        let Some((file_path, _, end)) = self.state.selected_range() else {
            return false;
        };
        item.file_path == file_path && item.row_index == Some(end)
    }

    fn comment_editor_lines(&self, width: u16) -> Vec<(StyledLine, Option<u16>)> {
        let content_width = usize::from(width).saturating_sub(GUTTER_WIDTH).max(1);
        let visual = editor_visual_lines(&self.comment_buffer, content_width);
        let mut result = Vec::with_capacity(visual.len());
        for (line_index, (text, start, _end)) in visual.iter().enumerate() {
            let mut line = StyledLine {
                segments: Vec::new(),
                fill_background: None,
            };
            line.push(
                "      ┃  ",
                Style {
                    foreground: Some(Color::DarkYellow),
                    bold: true,
                    ..Style::default()
                },
            );
            line.push(
                if text.is_empty() { " " } else { text },
                Style {
                    foreground: Some(Color::DarkYellow),
                    bold: true,
                    ..Style::default()
                },
            );
            let cursor_line = visual
                .iter()
                .rposition(|(_, line_start, line_end)| {
                    *line_start <= self.comment_cursor && self.comment_cursor <= *line_end
                })
                .unwrap_or(0);
            let cursor = (line_index == cursor_line).then(|| {
                let prefix = chars_between(&self.comment_buffer, *start, self.comment_cursor);
                u16::try_from(GUTTER_WIDTH + UnicodeWidthStr::width(prefix.as_str()))
                    .unwrap_or(u16::MAX)
            });
            result.push((line, cursor));
        }
        result
    }

    fn draw_status(&self, frame: &mut RenderFrame, y: u16, width: u16) {
        let text = if self.interrupt_armed {
            INTERRUPT_WARNING.to_owned()
        } else if self.command_mode {
            format!(":{}", self.command_buffer)
        } else if self.search_mode {
            format!("/{}", self.search_buffer)
        } else if self.comment_mode {
            let action = if self.editing_comment_id.is_some() {
                "Edit comment"
            } else {
                "New comment"
            };
            format!("{action}: Enter saves, Ctrl+J inserts newline, Esc cancels")
        } else {
            self.status.clone()
        };
        let active_input =
            self.interrupt_armed || self.command_mode || self.search_mode || self.comment_mode;
        let style = Style {
            foreground: (!active_input).then_some(Color::DarkBlue),
            reverse: active_input,
            ..Style::default()
        };
        draw_region_line(frame, 0, y, width, &full_width_line(text, width, style));
    }

    fn handle_event(&mut self, event: Event) {
        match event {
            Event::Key(key) if actionable_key_event(key) => self.handle_key(key),
            Event::Mouse(mouse) => self.handle_mouse(mouse),
            Event::Resize(_, _) => self.ensure_selected_visible(),
            Event::Paste(text) => self.handle_paste(&text),
            _ => {}
        }
    }

    fn handle_paste(&mut self, text: &str) {
        if self.comment_mode {
            self.insert_comment(text);
            self.position_comment_editor();
        } else if self.command_mode {
            self.command_buffer.push_str(&single_line_paste(text));
        } else if self.search_mode {
            self.search_buffer.push_str(&single_line_paste(text));
        }
    }

    fn handle_key(&mut self, key: KeyEvent) {
        if is_ctrl_char(key, 'c') {
            if self.interrupt_armed {
                self.quit_requested = true;
                self.cancel_requested = true;
            } else {
                self.interrupt_armed = true;
            }
            return;
        }
        self.interrupt_armed = false;
        if self.comment_mode {
            self.handle_comment_key(key);
            if self.comment_mode {
                self.position_comment_editor();
            }
            return;
        }
        if self.command_mode {
            self.handle_command_key(key);
            return;
        }
        if self.search_mode {
            self.handle_search_key(key);
            return;
        }
        if self.handle_global_key(key) {
            return;
        }
        match self.focus {
            Focus::Review => self.handle_review_key(key),
            Focus::Files => self.handle_file_key(key),
            Focus::Comments => self.handle_comment_pane_key(key),
        }
    }

    fn handle_global_key(&mut self, key: KeyEvent) -> bool {
        match key.code {
            KeyCode::Tab => {
                self.switch_focus();
                true
            }
            KeyCode::Char('t' | 'T')
                if key.modifiers.is_empty() || key.modifiers == KeyModifiers::SHIFT =>
            {
                self.file_pane_visible = !self.file_pane_visible;
                if !self.file_pane_visible {
                    self.focus = Focus::Review;
                }
                true
            }
            KeyCode::Char(':') => {
                self.command_mode = true;
                self.command_buffer.clear();
                true
            }
            KeyCode::Char('/') => {
                self.search_mode = true;
                self.search_buffer.clear();
                true
            }
            KeyCode::Char('z') => {
                self.center_selection();
                true
            }
            KeyCode::Char('n' | 'N') => {
                self.jump_search(1, false);
                true
            }
            KeyCode::Char('p' | 'P') => {
                self.jump_search(-1, false);
                true
            }
            KeyCode::Char('y' | 'Y') => {
                self.copy_selection();
                true
            }
            KeyCode::Esc => {
                if self.state.collapse_selection() {
                    self.status = "Selection cleared.".to_owned();
                }
                true
            }
            _ => false,
        }
    }

    fn switch_focus(&mut self) {
        if !self.file_pane_visible {
            self.focus = Focus::Review;
            return;
        }
        self.focus = match self.focus {
            Focus::Review => Focus::Files,
            Focus::Files => Focus::Comments,
            Focus::Comments => Focus::Review,
        };
        if self.focus == Focus::Comments {
            self.sync_comment_selection();
        }
    }

    fn handle_review_key(&mut self, key: KeyEvent) {
        let extend = key.modifiers.contains(KeyModifiers::SHIFT);
        match key.code {
            KeyCode::Up => self.move_review(-1, extend),
            KeyCode::Down => self.move_review(1, extend),
            KeyCode::Char('k') if !extend => self.move_review(-1, false),
            KeyCode::Char('j') if !extend => self.move_review(1, false),
            KeyCode::PageUp => self.page_review(-1),
            KeyCode::PageDown => self.page_review(1),
            KeyCode::Enter => self.activate_selection(),
            KeyCode::Delete | KeyCode::Backspace => self.delete_selected_comment(),
            _ => {}
        }
    }

    fn move_review(&mut self, delta: isize, extend: bool) {
        if extend {
            self.state.extend_selection(delta);
        } else {
            self.state.move_selection(delta);
        }
        self.file_scroll_follows_selection = true;
        self.ensure_selected_visible();
    }

    fn page_review(&mut self, direction: isize) {
        let items = self.state.document_items();
        if items.is_empty() {
            return;
        }
        let active = self
            .state
            .active_document_index()
            .unwrap_or(self.review_scroll);
        let current_layout = self.layout_visible_indices(self.review_scroll, &items);
        let screen_offset = current_layout
            .iter()
            .position(|index| *index == active)
            .unwrap_or(0);
        let new_scroll = if direction > 0 {
            current_layout
                .last()
                .copied()
                .map_or(self.review_scroll, |index| index.saturating_add(1))
                .min(items.len() - 1)
        } else {
            self.previous_page_start(&items)
        };
        self.review_scroll = new_scroll;
        let new_layout = self.layout_visible_indices(new_scroll, &items);
        if let Some(target) = selectable_near_offset(&items, &new_layout, screen_offset) {
            self.state.select_document_index(target);
            self.file_scroll_follows_selection = true;
        }
    }

    fn activate_selection(&mut self) {
        match self.state.activate_selection() {
            Activation::NewComment => self.start_new_comment(),
            Activation::EditComment(id) => self.start_edit_comment(id),
            Activation::Expanded => self.ensure_selected_visible(),
            Activation::None => {}
        }
    }

    fn handle_file_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Up | KeyCode::Char('k') => self.move_file_tree(-1),
            KeyCode::Down | KeyCode::Char('j') => self.move_file_tree(1),
            KeyCode::PageUp => {
                self.move_file_tree(-isize::try_from(self.last_height / 2).unwrap_or(1));
            }
            KeyCode::PageDown => {
                self.move_file_tree(isize::try_from(self.last_height / 2).unwrap_or(1));
            }
            KeyCode::Enter => {
                let path = self.state.files[self.state.file_pane_index].path.clone();
                self.review_scroll = self.state.select_file(&path);
                self.focus = Focus::Review;
            }
            _ => {}
        }
    }

    fn move_file_tree(&mut self, delta: isize) {
        let rows = build_file_tree(&self.state.files);
        let files = rows
            .iter()
            .filter_map(|row| row.file_index)
            .collect::<Vec<_>>();
        if files.is_empty() {
            return;
        }
        let position = files
            .iter()
            .position(|index| *index == self.state.file_pane_index)
            .unwrap_or(0);
        let next = offset_index(position, delta, files.len());
        let file_index = files[next];
        let path = self.state.files[file_index].path.clone();
        self.review_scroll = self.state.select_file(&path);
        self.file_scroll_follows_selection = true;
    }

    fn handle_comment_pane_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Up | KeyCode::Char('k') => self.move_comment_pane(-1),
            KeyCode::Down | KeyCode::Char('j') => self.move_comment_pane(1),
            KeyCode::PageUp => {
                self.move_comment_pane(-isize::try_from(self.last_height / 2).unwrap_or(1));
            }
            KeyCode::PageDown => {
                self.move_comment_pane(isize::try_from(self.last_height / 2).unwrap_or(1));
            }
            KeyCode::Enter => {
                self.focus_comment_pane();
                self.focus = Focus::Review;
            }
            KeyCode::Delete | KeyCode::Backspace => self.delete_comment_pane_selection(),
            _ => {}
        }
    }

    fn move_comment_pane(&mut self, delta: isize) {
        let rows = self.comment_pane_rows();
        let selectable = rows
            .iter()
            .enumerate()
            .filter_map(|(index, row)| row.comment.as_ref().map(|_| index))
            .collect::<Vec<_>>();
        if selectable.is_empty() {
            return;
        }
        let position = selectable
            .iter()
            .position(|index| *index == self.comment_pane_index)
            .unwrap_or(0);
        self.comment_pane_index = selectable[offset_index(position, delta, selectable.len())];
        self.comment_scroll_follows_selection = true;
        self.focus_comment_pane();
    }

    fn focus_comment_pane(&mut self) {
        let id = self
            .comment_pane_rows()
            .get(self.comment_pane_index)
            .and_then(|row| row.comment.as_ref())
            .map(|comment| comment.id);
        if let Some(index) = id.and_then(|id| self.state.select_comment(id)) {
            self.review_scroll = index;
            self.file_scroll_follows_selection = true;
        }
    }

    fn sync_comment_selection(&mut self) {
        let selected = self.state.selected_comment_id();
        if let Some(index) = self.comment_pane_rows().iter().position(|row| {
            row.comment
                .as_ref()
                .is_some_and(|comment| Some(comment.id) == selected)
        }) {
            self.comment_pane_index = index;
            self.comment_scroll_follows_selection = true;
        }
    }

    fn handle_command_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Esc => {
                self.command_mode = false;
                self.command_buffer.clear();
            }
            KeyCode::Backspace => {
                self.command_buffer.pop();
            }
            KeyCode::Enter => self.run_command(),
            KeyCode::Char(character) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.command_buffer.push(character);
            }
            _ => {}
        }
    }

    fn run_command(&mut self) {
        let command = self.command_buffer.trim().to_owned();
        self.command_mode = false;
        self.command_buffer.clear();
        match command.as_str() {
            "q" | "quit" | "q!" | "quit!" => self.quit_requested = true,
            "e" | "edit" | "edit-comment" => {
                if let Some(id) = self.state.selected_comment_id() {
                    self.start_edit_comment(id);
                } else {
                    self.status = "No comment is attached to the selected line.".to_owned();
                }
            }
            "d" | "delete" | "delete-comment" => self.delete_selected_comment(),
            "c" | "center" | "centre" => self.center_selection(),
            "y" | "yank" | "copy" | "copy-selection" => self.copy_selection(),
            _ => self.status = format!("Unknown command: {command}"),
        }
    }

    fn handle_search_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Esc => {
                self.search_mode = false;
                self.search_buffer.clear();
                self.status = "Search cancelled.".to_owned();
            }
            KeyCode::Backspace => {
                self.search_buffer.pop();
            }
            KeyCode::Enter => {
                let query = std::mem::take(&mut self.search_buffer);
                self.search_mode = false;
                if query.is_empty() {
                    self.search_query.clear();
                    self.status = "Search cleared.".to_owned();
                } else {
                    self.search_query = query;
                    self.jump_search(1, true);
                }
            }
            KeyCode::Char(character) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.search_buffer.push(character);
            }
            _ => {}
        }
    }

    fn search_matches(&self) -> Vec<usize> {
        if self.search_query.is_empty() {
            return Vec::new();
        }
        self.state
            .document_items()
            .iter()
            .enumerate()
            .filter_map(|(index, item)| {
                (item.selectable() && item.text.contains(&self.search_query)).then_some(index)
            })
            .collect()
    }

    fn jump_search(&mut self, direction: isize, include_current: bool) {
        if self.search_query.is_empty() {
            self.status = "No active search.".to_owned();
            return;
        }
        let matches = self.search_matches();
        if matches.is_empty() {
            self.status = format!("No matches for /{}", self.search_query);
            return;
        }
        let current = self
            .state
            .active_document_index()
            .unwrap_or(self.review_scroll);
        let target = if direction > 0 {
            matches
                .iter()
                .copied()
                .find(|index| *index > current || (include_current && *index == current))
                .unwrap_or(matches[0])
        } else {
            matches
                .iter()
                .rev()
                .copied()
                .find(|index| *index < current || (include_current && *index == current))
                .unwrap_or(*matches.last().unwrap_or(&matches[0]))
        };
        self.state.select_document_index(target);
        self.ensure_selected_visible();
        let position = matches
            .iter()
            .position(|index| *index == target)
            .unwrap_or(0);
        self.status = format!(
            "Match {}/{} for /{}",
            position + 1,
            matches.len(),
            self.search_query
        );
    }

    fn start_new_comment(&mut self) {
        self.comment_mode = true;
        self.comment_buffer.clear();
        self.comment_cursor = 0;
        self.comment_goal_column = None;
        self.comment_editor_scroll = 0;
        self.editing_comment_id = None;
    }

    fn start_edit_comment(&mut self, id: u64) {
        let Some(comment) = self.state.comments.iter().find(|comment| comment.id == id) else {
            self.status = "No comment is attached to the selected line.".to_owned();
            return;
        };
        self.comment_mode = true;
        self.comment_buffer = comment.body.clone();
        self.comment_cursor = self.comment_buffer.chars().count();
        self.comment_goal_column = None;
        self.comment_editor_scroll = 0;
        self.editing_comment_id = Some(id);
    }

    fn handle_comment_key(&mut self, key: KeyEvent) {
        if is_ctrl_char(key, 'j') {
            self.insert_comment("\n");
            return;
        }
        if is_ctrl_char(key, 'a') {
            self.comment_line_start();
            return;
        }
        if is_ctrl_char(key, 'e') {
            self.comment_line_end();
            return;
        }
        if is_ctrl_char(key, 'w') {
            self.delete_comment_word();
            return;
        }
        match key.code {
            KeyCode::Esc => self.close_comment(),
            KeyCode::Enter => self.submit_comment(),
            KeyCode::Backspace => self.delete_comment_char(),
            KeyCode::Left if key.modifiers.contains(KeyModifiers::ALT) => {
                self.move_comment_word(-1);
            }
            KeyCode::Right if key.modifiers.contains(KeyModifiers::ALT) => {
                self.move_comment_word(1);
            }
            KeyCode::Left => {
                self.comment_cursor = self.comment_cursor.saturating_sub(1);
                self.comment_goal_column = None;
            }
            KeyCode::Right => {
                self.comment_cursor =
                    (self.comment_cursor + 1).min(self.comment_buffer.chars().count());
                self.comment_goal_column = None;
            }
            KeyCode::Up => self.move_comment_vertical(-1),
            KeyCode::Down => self.move_comment_vertical(1),
            KeyCode::Tab => self.insert_comment("\t"),
            KeyCode::Char(character) if !key.modifiers.contains(KeyModifiers::CONTROL) => {
                self.insert_comment(&character.to_string());
            }
            _ => {}
        }
    }

    fn insert_comment(&mut self, text: &str) {
        let byte = char_to_byte(&self.comment_buffer, self.comment_cursor);
        self.comment_buffer.insert_str(byte, text);
        self.comment_cursor += text.chars().count();
        self.comment_goal_column = None;
    }

    fn delete_comment_char(&mut self) {
        if self.comment_cursor == 0 {
            return;
        }
        let end = char_to_byte(&self.comment_buffer, self.comment_cursor);
        let start = char_to_byte(&self.comment_buffer, self.comment_cursor - 1);
        self.comment_buffer.replace_range(start..end, "");
        self.comment_cursor -= 1;
        self.comment_goal_column = None;
    }

    fn delete_comment_word(&mut self) {
        if self.comment_cursor == 0 {
            return;
        }
        let chars = self.comment_buffer.chars().collect::<Vec<_>>();
        let mut start = self.comment_cursor;
        while start > 0 && chars[start - 1].is_whitespace() {
            start -= 1;
        }
        while start > 0 && !chars[start - 1].is_whitespace() {
            start -= 1;
        }
        let start_byte = char_to_byte(&self.comment_buffer, start);
        let end_byte = char_to_byte(&self.comment_buffer, self.comment_cursor);
        self.comment_buffer.replace_range(start_byte..end_byte, "");
        self.comment_cursor = start;
        self.comment_goal_column = None;
    }

    fn move_comment_word(&mut self, direction: isize) {
        let chars = self.comment_buffer.chars().collect::<Vec<_>>();
        if direction < 0 {
            let mut cursor = self.comment_cursor;
            while cursor > 0 && chars[cursor - 1].is_whitespace() {
                cursor -= 1;
            }
            while cursor > 0 && !chars[cursor - 1].is_whitespace() {
                cursor -= 1;
            }
            self.comment_cursor = cursor;
        } else {
            let mut cursor = self.comment_cursor;
            while cursor < chars.len() && !chars[cursor].is_whitespace() {
                cursor += 1;
            }
            while cursor < chars.len() && chars[cursor].is_whitespace() {
                cursor += 1;
            }
            self.comment_cursor = cursor;
        }
        self.comment_goal_column = None;
    }

    fn comment_line_start(&mut self) {
        let chars = self.comment_buffer.chars().collect::<Vec<_>>();
        let start = chars[..self.comment_cursor]
            .iter()
            .rposition(|character| *character == '\n')
            .map_or(0, |index| index + 1);
        self.comment_cursor = if self.comment_cursor == start {
            0
        } else {
            start
        };
        self.comment_goal_column = None;
    }

    fn comment_line_end(&mut self) {
        let chars = self.comment_buffer.chars().collect::<Vec<_>>();
        let end = chars[self.comment_cursor..]
            .iter()
            .position(|character| *character == '\n')
            .map_or(chars.len(), |offset| self.comment_cursor + offset);
        self.comment_cursor = if self.comment_cursor == end {
            chars.len()
        } else {
            end
        };
        self.comment_goal_column = None;
    }

    fn move_comment_vertical(&mut self, delta: isize) {
        let chars = self.comment_buffer.chars().collect::<Vec<_>>();
        let line_starts = std::iter::once(0)
            .chain(
                chars
                    .iter()
                    .enumerate()
                    .filter_map(|(index, character)| (*character == '\n').then_some(index + 1)),
            )
            .collect::<Vec<_>>();
        let line = line_starts
            .partition_point(|start| *start <= self.comment_cursor)
            .saturating_sub(1);
        let column = self.comment_cursor - line_starts[line];
        let goal = *self.comment_goal_column.get_or_insert(column);
        let target = offset_index(line, delta, line_starts.len());
        let end = chars[line_starts[target]..]
            .iter()
            .position(|character| *character == '\n')
            .map_or(chars.len(), |offset| line_starts[target] + offset);
        self.comment_cursor = (line_starts[target] + goal).min(end);
    }

    fn submit_comment(&mut self) {
        if let Some(id) = self.editing_comment_id {
            if self.state.update_comment(id, &self.comment_buffer) {
                self.status = "Comment updated.".to_owned();
            } else {
                self.status = "Empty comments are ignored.".to_owned();
            }
        } else if let Some(id) = self.state.add_comment(&self.comment_buffer) {
            self.state.select_comment(id);
            self.status = "Comment saved.".to_owned();
            self.ensure_selected_visible();
        } else {
            self.status = "Empty comments are ignored.".to_owned();
        }
        self.close_comment();
    }

    fn close_comment(&mut self) {
        self.comment_mode = false;
        self.comment_buffer.clear();
        self.comment_cursor = 0;
        self.comment_goal_column = None;
        self.comment_editor_scroll = 0;
        self.editing_comment_id = None;
    }

    fn position_comment_editor(&mut self) {
        let items = self.state.document_items();
        let attachment = if self.editing_comment_id.is_some() {
            self.state.active_document_index()
        } else {
            let Some((file_path, _, end)) = self.state.selected_range() else {
                return;
            };
            items.iter().position(|item| {
                item.kind == DocumentKind::Code
                    && item.file_path == file_path
                    && item.row_index == Some(end)
            })
        };
        let Some(attachment) = attachment else {
            return;
        };
        if self.review_scroll > attachment {
            self.review_scroll = attachment;
            return;
        }
        let required_rows = if self.editing_comment_id.is_some() {
            1
        } else {
            let selected = self.state.item_is_selected(&items[attachment]);
            self.review_item_lines(&items[attachment], self.current_review_width(), selected)
                .0
                .len()
                .saturating_add(1)
        };
        while self.review_scroll < attachment {
            let visible_attachment_rows = self
                .layout_visible_indices(self.review_scroll, &items)
                .into_iter()
                .filter(|index| *index == attachment)
                .count();
            if visible_attachment_rows >= required_rows {
                break;
            }
            self.review_scroll += 1;
        }
    }

    fn comment_editor_window(
        &mut self,
        lines: Vec<(StyledLine, Option<u16>)>,
        available: usize,
    ) -> Vec<(StyledLine, Option<u16>)> {
        if available == 0 || lines.is_empty() {
            return Vec::new();
        }
        let cursor_line = lines
            .iter()
            .position(|(_, cursor)| cursor.is_some())
            .unwrap_or(0);
        ensure_scroll(&mut self.comment_editor_scroll, cursor_line, available);
        self.comment_editor_scroll = self
            .comment_editor_scroll
            .min(lines.len().saturating_sub(available.min(lines.len())));
        lines
            .into_iter()
            .skip(self.comment_editor_scroll)
            .take(available)
            .collect()
    }

    fn delete_selected_comment(&mut self) {
        let Some(id) = self.state.selected_comment_id() else {
            self.status = "No comment is attached to the selected line.".to_owned();
            return;
        };
        if self.state.delete_comment(id) {
            self.status = "Comment deleted.".to_owned();
            self.sync_comment_selection();
        }
    }

    fn delete_comment_pane_selection(&mut self) {
        let id = self
            .comment_pane_rows()
            .get(self.comment_pane_index)
            .and_then(|row| row.comment.as_ref())
            .map(|comment| comment.id);
        if let Some(id) = id {
            self.state.delete_comment(id);
            self.status = "Comment deleted.".to_owned();
            self.comment_pane_index = self.comment_pane_index.saturating_sub(1);
            self.comment_scroll_follows_selection = true;
            self.focus_comment_pane();
        }
    }

    fn handle_mouse(&mut self, mouse: MouseEvent) {
        match mouse.kind {
            MouseEventKind::ScrollUp => {
                self.scroll_under_mouse(
                    mouse.column,
                    mouse.row,
                    -isize::try_from(MOUSE_SCROLL_LINES).unwrap_or(1),
                );
            }
            MouseEventKind::ScrollDown => {
                self.scroll_under_mouse(
                    mouse.column,
                    mouse.row,
                    isize::try_from(MOUSE_SCROLL_LINES).unwrap_or(1),
                );
            }
            MouseEventKind::Down(MouseButton::Left) => {
                if self.file_pane_visible && mouse.column < self.last_left_width {
                    self.handle_left_click(mouse.row);
                } else if let Some(index) = self.screen_map.get(&mouse.row).copied() {
                    let kind = self.state.document_items().get(index).map(|item| item.kind);
                    self.state.select_document_index(index);
                    self.file_scroll_follows_selection = true;
                    if kind == Some(DocumentKind::Expansion) {
                        self.activate_selection();
                    }
                    self.focus = Focus::Review;
                }
            }
            MouseEventKind::Drag(MouseButton::Left) => {
                if let Some(index) = self.screen_map.get(&mouse.row).copied() {
                    let item = self.state.document_items().get(index).cloned();
                    let selection = match self.state.selection.as_ref() {
                        Some(Selection::Code {
                            file_path,
                            anchor_row,
                            ..
                        }) => Some((file_path.clone(), *anchor_row)),
                        _ => None,
                    };
                    match (item, selection) {
                        (Some(item), Some((path, anchor)))
                            if item.kind == DocumentKind::Code && item.file_path == path =>
                        {
                            if let Some(row) = item.row_index {
                                self.state.selection = Some(Selection::Code {
                                    file_path: path,
                                    anchor_row: anchor,
                                    active_row: row,
                                });
                            }
                        }
                        _ => {}
                    }
                }
            }
            _ => {}
        }
    }

    fn scroll_under_mouse(&mut self, column: u16, row: u16, delta: isize) {
        match self.mouse_region(column, row) {
            MouseRegion::FileTree => {
                let rows = build_file_tree(&self.state.files);
                let body_height = usize::from(self.file_pane_heights().0.saturating_sub(1));
                self.file_scroll_follows_selection = false;
                scroll_pane(&mut self.file_scroll, delta, rows.len(), body_height);
            }
            MouseRegion::Comments => {
                let rows = self.comment_pane_rows();
                let body_height = usize::from(self.file_pane_heights().1.saturating_sub(1));
                self.comment_scroll_follows_selection = false;
                scroll_pane(&mut self.comment_scroll, delta, rows.len(), body_height);
            }
            MouseRegion::Review => self.scroll_review(delta),
            MouseRegion::Outside => {}
        }
    }

    fn mouse_region(&self, column: u16, row: u16) -> MouseRegion {
        let content_height = self.last_height.saturating_sub(1);
        if column >= self.last_width || row >= content_height {
            return MouseRegion::Outside;
        }
        if !self.file_pane_visible {
            return MouseRegion::Review;
        }
        if column < self.last_left_width {
            let (file_height, _) = self.file_pane_heights();
            return if row < file_height {
                MouseRegion::FileTree
            } else {
                MouseRegion::Comments
            };
        }
        if column == self.last_left_width {
            MouseRegion::Outside
        } else {
            MouseRegion::Review
        }
    }

    fn file_pane_heights(&self) -> (u16, u16) {
        let content_height = self.last_height.saturating_sub(1);
        let file_height = (content_height / 2).max(3).min(content_height);
        (file_height, content_height.saturating_sub(file_height))
    }

    fn handle_left_click(&mut self, row: u16) {
        match self.left_hit_map.get(&row).cloned() {
            Some(LeftHit::File(file_index)) => {
                let path = self.state.files[file_index].path.clone();
                self.review_scroll = self.state.select_file(&path);
                self.file_scroll_follows_selection = true;
                self.focus = Focus::Files;
            }
            Some(LeftHit::Comment(id)) => {
                if let Some(index) = self.state.select_comment(id) {
                    self.review_scroll = index;
                }
                self.file_scroll_follows_selection = true;
                self.comment_scroll_follows_selection = true;
                self.sync_comment_selection();
                self.focus = Focus::Comments;
            }
            None => {}
        }
    }

    fn scroll_review(&mut self, delta: isize) {
        let count = self.state.document_items().len();
        if count == 0 {
            return;
        }
        self.review_scroll = offset_index(self.review_scroll, delta, count);
        let file_index = self
            .state
            .file_for_document_index(self.review_scroll)
            .and_then(|path| self.state.file_index(&path));
        if let Some(index) = file_index {
            self.state.file_pane_index = index;
            self.file_scroll_follows_selection = true;
        }
    }

    fn ensure_selected_visible(&mut self) {
        self.file_scroll_follows_selection = true;
        let Some(active) = self.state.active_document_index() else {
            return;
        };
        let items = self.state.document_items();
        if self
            .layout_visible_indices(self.review_scroll, &items)
            .contains(&active)
        {
            return;
        }
        if active < self.review_scroll {
            self.review_scroll = active;
            return;
        }
        let target_rows = usize::from(self.last_height.saturating_sub(4)).max(1);
        let width = self.current_review_width();
        let mut start = active;
        let mut used = self.item_physical_height(&items[active], width);
        while start > 0 {
            let preceding = self.item_physical_height(&items[start - 1], width);
            if used.saturating_add(preceding) > target_rows {
                break;
            }
            start -= 1;
            used += preceding;
        }
        self.review_scroll = start;
    }

    fn center_selection(&mut self) {
        let Some(active) = self.state.active_document_index() else {
            return;
        };
        let items = self.state.document_items();
        let width = self.current_review_width();
        let target = usize::from(self.last_height.saturating_sub(1) / 2).max(1);
        let mut start = active;
        let mut used = 0;
        while start > 0 && used < target {
            start -= 1;
            used += self.item_physical_height(&items[start], width);
        }
        self.review_scroll = start;
    }

    fn current_review_width(&self) -> u16 {
        let separator = u16::from(self.file_pane_visible);
        self.last_width
            .saturating_sub(self.last_left_width)
            .saturating_sub(separator)
            .max(1)
    }

    fn item_physical_height(&mut self, item: &DocumentItem, width: u16) -> usize {
        let selected = self.state.item_is_selected(item);
        let (lines, _) = self.review_item_lines(item, width, selected);
        let editor = if self.should_draw_new_comment_input(item) {
            self.comment_editor_lines(width).len()
        } else {
            0
        };
        lines.len().saturating_add(editor).max(1)
    }

    fn layout_visible_indices(&mut self, start: usize, items: &[DocumentItem]) -> Vec<usize> {
        if items.is_empty() {
            return Vec::new();
        }
        let start = start.min(items.len() - 1);
        let capacity = usize::from(self.last_height.saturating_sub(1));
        let width = self.current_review_width();
        let mut layout = Vec::with_capacity(capacity);
        let header = (items[start].kind != DocumentKind::FileHeader)
            .then(|| {
                items[..=start]
                    .iter()
                    .rposition(|item| item.kind == DocumentKind::FileHeader)
            })
            .flatten();
        if let Some(index) = header {
            layout.push(index);
        }
        for (index, item) in items.iter().enumerate().skip(start) {
            for _ in 0..self.item_physical_height(item, width) {
                if layout.len() == capacity {
                    return layout;
                }
                layout.push(index);
            }
        }
        layout
    }

    fn previous_page_start(&mut self, items: &[DocumentItem]) -> usize {
        let capacity = usize::from(self.last_height.saturating_sub(1)).max(1);
        let width = self.current_review_width();
        let mut start = self.review_scroll;
        let mut rows = 0;
        while start > 0 && rows < capacity {
            start -= 1;
            rows += self.item_physical_height(&items[start], width);
        }
        start
    }

    fn copy_selection(&mut self) {
        let text = match self.state.selection.as_ref() {
            Some(Selection::Code {
                file_path,
                anchor_row,
                active_row,
            }) => self.state.file_by_path(file_path).map(|file| {
                let start = (*anchor_row).min(*active_row);
                let end = (*anchor_row).max(*active_row);
                file.lines[start..=end]
                    .iter()
                    .map(|line| line.text.as_str())
                    .collect::<Vec<_>>()
                    .join("\n")
            }),
            Some(Selection::Comment { id, .. }) => self
                .state
                .comments
                .iter()
                .find(|comment| comment.id == *id)
                .map(|comment| comment.body.clone()),
            _ => None,
        };
        let Some(text) = text.filter(|text| !text.is_empty()) else {
            self.status = "Nothing selected to copy.".to_owned();
            return;
        };
        let sequence = osc52_sequence(&text, inside_tmux());
        let mut output = io::stdout();
        if output
            .write_all(sequence.as_bytes())
            .and_then(|()| output.flush())
            .is_ok()
        {
            self.status = "Selection copied with OSC 52.".to_owned();
        } else {
            self.status = "Could not copy selection.".to_owned();
        }
    }
}

fn split_editor_result(
    values: Vec<(StyledLine, Option<u16>)>,
) -> (Vec<StyledLine>, Vec<Option<u16>>) {
    values.into_iter().unzip()
}

fn pane_header_style(focused: bool) -> Style {
    Style {
        foreground: (!focused).then_some(Color::DarkCyan),
        bold: true,
        reverse: focused,
        ..Style::default()
    }
}

fn code_background(selected: bool, kind: LineKind) -> Option<Color> {
    match (selected, kind) {
        (true, LineKind::Addition) => Some(ADDITION_SELECTION_BACKGROUND),
        (true, LineKind::Deletion) => Some(DELETION_SELECTION_BACKGROUND),
        (true, LineKind::Context) => Some(SELECTION_BACKGROUND),
        (false, LineKind::Addition) => Some(ADDITION_BACKGROUND),
        (false, LineKind::Deletion) => Some(DELETION_BACKGROUND),
        (false, LineKind::Context) => None,
    }
}

fn selection_style(focused: bool) -> Style {
    Style {
        bold: focused,
        reverse: true,
        ..Style::default()
    }
}

const fn file_status_color(status: FileStatus) -> Color {
    match status {
        FileStatus::Unchanged => Color::DarkGrey,
        FileStatus::Added => Color::DarkGreen,
        FileStatus::Modified => Color::DarkBlue,
        FileStatus::Deleted => Color::DarkRed,
        FileStatus::Renamed | FileStatus::TypeChanged => Color::DarkMagenta,
        FileStatus::Binary => Color::DarkCyan,
        FileStatus::Mode => Color::DarkYellow,
    }
}

fn file_tree_file_line(
    file: &ReviewFile,
    label: &str,
    depth: usize,
    comment_count: usize,
    selected: bool,
    focused: bool,
    width: u16,
) -> StyledLine {
    let base_style = if selected {
        selection_style(focused)
    } else {
        Style::default()
    };
    let mut status_style = base_style;
    status_style.foreground = Some(file_status_color(file.status));
    status_style.bold = true;

    let indentation = "  ".repeat(depth);
    let marker = format!("{} ", file.status_marker());
    let suffix = match comment_count {
        0 => String::new(),
        count => format!(" ({count})"),
    };
    let label = format!("{label}{suffix}");
    let used_width = indentation.width() + marker.width() + label.width();

    let mut line = StyledLine::default();
    line.push(indentation, base_style);
    line.push(marker, status_style);
    line.push(label, base_style);
    if selected {
        line.push(
            " ".repeat(usize::from(width).saturating_sub(used_width)),
            base_style,
        );
    }
    line
}

fn full_width_line(text: impl Into<String>, width: u16, style: Style) -> StyledLine {
    StyledLine::plain(pad_to_width(text, width), style)
}

fn pad_to_width(text: impl Into<String>, width: u16) -> String {
    let mut text = text.into();
    let padding = usize::from(width).saturating_sub(text.width());
    text.push_str(&" ".repeat(padding));
    text
}

fn scroll_footer(above: usize, below: usize) -> String {
    match (above, below) {
        (0, below) => format!(" v {below} more below"),
        (above, 0) => format!(" ^ {above} above"),
        (above, below) => format!(" ^ {above} above  v {below} below"),
    }
}

fn selected_line_number(comment: &ReviewComment) -> usize {
    comment
        .selected_lines()
        .first()
        .and_then(crate::model::ReviewLine::primary_line)
        .unwrap_or(comment.sorted_rows().0 + 1)
}

fn selected_end_line_number(comment: &ReviewComment) -> usize {
    comment
        .selected_lines()
        .last()
        .and_then(crate::model::ReviewLine::primary_line)
        .unwrap_or(comment.sorted_rows().1 + 1)
}

fn sticky_header(items: &[DocumentItem], index: usize) -> Option<&DocumentItem> {
    items[..=index.min(items.len().saturating_sub(1))]
        .iter()
        .rev()
        .find(|item| item.kind == DocumentKind::FileHeader)
}

fn wrap_plain_lines(text: &str, width: usize) -> Vec<String> {
    let mut output = Vec::new();
    for logical in text.split('\n') {
        let mut current = String::new();
        let mut cells = 0;
        for character in logical.chars() {
            if character == '\t' {
                let spaces = 4 - cells % 4;
                if cells > 0 && cells + spaces > width {
                    output.push(std::mem::take(&mut current));
                    cells = 0;
                }
                current.push_str(&" ".repeat(spaces));
                cells += spaces;
                continue;
            }
            let character_width = character.width().unwrap_or(0);
            if cells > 0 && cells + character_width > width {
                output.push(std::mem::take(&mut current));
                cells = 0;
            }
            current.push(character);
            cells += character_width;
        }
        output.push(current);
    }
    if output.is_empty() {
        output.push(String::new());
    }
    output
}

fn wrap_highlighted(
    spans: &[HighlightedSpan],
    width: usize,
    background: Option<Color>,
    source: &str,
    search: &str,
) -> Vec<Vec<Segment>> {
    let matches = search_match_chars(source, search);
    let mut lines = vec![Vec::<Segment>::new()];
    let mut cells = 0;
    let mut char_index = 0;
    for span in spans {
        let base = Style {
            foreground: syntax_foreground(span.foreground, background.is_some()),
            background,
            bold: span.bold,
            italic: span.italic,
            reverse: false,
        };
        for character in span.text.chars() {
            let pieces = if character == '\t' {
                " ".repeat(4 - cells % 4)
            } else {
                character.to_string()
            };
            let piece_width = pieces.width();
            if cells > 0 && cells + piece_width > width {
                lines.push(Vec::new());
                cells = 0;
            }
            let mut style = base;
            if matches.get(char_index).copied().unwrap_or(false) {
                style.foreground = Some(Color::Black);
                style.background = Some(SEARCH_BACKGROUND);
            }
            push_segment(lines.last_mut().expect("one line"), pieces, style);
            cells += piece_width;
            char_index += 1;
        }
    }
    lines
}

fn syntax_foreground(foreground: (u8, u8, u8), has_background: bool) -> Option<Color> {
    match foreground {
        DEFAULT_FOREGROUND | PUNCTUATION_FOREGROUND => has_background.then_some(Color::Black),
        KEYWORD_FOREGROUND | COMMENT_FOREGROUND | ATTRIBUTE_FOREGROUND | EMPHASIS_FOREGROUND => {
            Some(Color::DarkBlue)
        }
        FUNCTION_FOREGROUND | TAG_FOREGROUND | HEADING_FOREGROUND => Some(Color::DarkMagenta),
        STRING_FOREGROUND => Some(if has_background {
            Color::DarkMagenta
        } else {
            Color::DarkGreen
        }),
        TYPE_FOREGROUND | NUMBER_FOREGROUND => Some(if has_background {
            Color::DarkBlue
        } else {
            Color::DarkCyan
        }),
        OPERATOR_FOREGROUND => Some(if has_background {
            Color::Black
        } else {
            Color::DarkRed
        }),
        ERROR_FOREGROUND => Some(Color::DarkRed),
        (r, g, b) => Some(Color::Rgb { r, g, b }),
    }
}

fn push_segment(segments: &mut Vec<Segment>, text: String, style: Style) {
    match segments.last_mut() {
        Some(last) if last.style == style => last.text.push_str(&text),
        _ => segments.push(Segment { text, style }),
    }
}

fn search_match_chars(source: &str, query: &str) -> Vec<bool> {
    let mut matches = vec![false; source.chars().count()];
    if query.is_empty() {
        return matches;
    }
    for (byte_start, _) in source.match_indices(query) {
        let start = source[..byte_start].chars().count();
        let count = query.chars().count();
        for flag in matches.iter_mut().skip(start).take(count) {
            *flag = true;
        }
    }
    matches
}

fn editor_visual_lines(buffer: &str, width: usize) -> Vec<(String, usize, usize)> {
    let chars = buffer.chars().collect::<Vec<_>>();
    let mut output = Vec::new();
    let mut logical_start = 0;
    loop {
        let logical_end = chars[logical_start..]
            .iter()
            .position(|character| *character == '\n')
            .map_or(chars.len(), |offset| logical_start + offset);
        if logical_start == logical_end {
            output.push((String::new(), logical_start, logical_end));
        } else {
            let mut chunk_start = logical_start;
            let mut chunk = String::new();
            let mut cells = 0;
            for (index, character) in chars[logical_start..logical_end].iter().enumerate() {
                let absolute = logical_start + index;
                let character_width = character.width().unwrap_or(0);
                if cells > 0 && cells + character_width > width {
                    output.push((std::mem::take(&mut chunk), chunk_start, absolute));
                    chunk_start = absolute;
                    cells = 0;
                }
                chunk.push(*character);
                cells += character_width;
            }
            output.push((chunk, chunk_start, logical_end));
        }
        if logical_end == chars.len() {
            break;
        }
        logical_start = logical_end + 1;
        if logical_start == chars.len() {
            output.push((String::new(), logical_start, logical_start));
            break;
        }
    }
    output
}

fn chars_between(value: &str, start: usize, end: usize) -> String {
    value
        .chars()
        .skip(start)
        .take(end.saturating_sub(start))
        .collect()
}

fn draw_region_line(frame: &mut RenderFrame, x: u16, y: u16, width: u16, line: &StyledLine) {
    frame.draw_region(x, y, width, line);
}

fn render_frame<W: Write>(
    output: &mut W,
    frame: &RenderFrame,
    previous: Option<&RenderFrame>,
) -> Result<()> {
    if previous == Some(frame) {
        return Ok(());
    }
    let redraw_all = previous
        .is_none_or(|previous| previous.width != frame.width || previous.height != frame.height);
    let changed_rows = (0..frame.height)
        .filter(|y| {
            redraw_all
                || previous.is_none_or(|previous| {
                    previous.rows[usize::from(*y)] != frame.rows[usize::from(*y)]
                })
        })
        .collect::<Vec<_>>();
    let cursor_changed = previous.is_none_or(|previous| previous.cursor != frame.cursor);
    if changed_rows.is_empty() && !cursor_changed {
        return Ok(());
    }

    queue!(output, BeginSynchronizedUpdate)
        .map_err(|error| ReviewError::io("could not begin terminal frame", error))?;
    for y in changed_rows {
        render_frame_row(output, frame, y)?;
    }
    match frame.cursor {
        Some((x, y)) => queue!(output, MoveTo(x, y), Show)
            .map_err(|error| ReviewError::io("could not position comment cursor", error))?,
        None => queue!(output, Hide)
            .map_err(|error| ReviewError::io("could not hide terminal cursor", error))?,
    }
    queue!(output, EndSynchronizedUpdate)
        .map_err(|error| ReviewError::io("could not finish terminal frame", error))
}

fn render_frame_row<W: Write>(output: &mut W, frame: &RenderFrame, y: u16) -> Result<()> {
    queue!(
        output,
        MoveTo(0, y),
        SetAttribute(Attribute::Reset),
        ResetColor
    )
    .map_err(|error| ReviewError::io("could not draw terminal frame", error))?;
    let mut regions = frame.rows[usize::from(y)].iter().collect::<Vec<_>>();
    regions.sort_by_key(|region| region.x);
    let mut column = 0_u16;
    for region in regions {
        if region.x < column {
            continue;
        }
        write_blank_cells(output, usize::from(region.x - column))?;
        draw_line_content(output, region.width, &region.line)?;
        column = region.x.saturating_add(region.width).min(frame.width);
    }
    write_blank_cells(output, usize::from(frame.width.saturating_sub(column)))?;
    queue!(output, ResetColor, SetAttribute(Attribute::Reset))
        .map_err(|error| ReviewError::io("could not finish terminal row", error))
}

fn draw_line_content<W: Write>(output: &mut W, width: u16, line: &StyledLine) -> Result<()> {
    let mut remaining = usize::from(width);
    for segment in &line.segments {
        if remaining == 0 {
            break;
        }
        let text = truncate_width(&segment.text, remaining);
        let used = text.width();
        apply_style(output, segment.style)?;
        queue!(output, Print(text))
            .map_err(|error| ReviewError::io("could not draw terminal UI", error))?;
        remaining = remaining.saturating_sub(used);
    }
    if remaining > 0 {
        queue!(output, SetAttribute(Attribute::Reset))
            .map_err(|error| ReviewError::io("could not draw terminal UI", error))?;
        if let Some(background) = line.fill_background {
            queue!(output, SetBackgroundColor(background))
                .map_err(|error| ReviewError::io("could not draw terminal UI", error))?;
        } else {
            queue!(output, ResetColor)
                .map_err(|error| ReviewError::io("could not draw terminal UI", error))?;
        }
        queue!(output, Print(" ".repeat(remaining)))
            .map_err(|error| ReviewError::io("could not draw terminal UI", error))?;
    }
    Ok(())
}

fn write_blank_cells<W: Write>(output: &mut W, count: usize) -> Result<()> {
    if count == 0 {
        return Ok(());
    }
    queue!(
        output,
        SetAttribute(Attribute::Reset),
        ResetColor,
        Print(" ".repeat(count))
    )
    .map_err(|error| ReviewError::io("could not erase stale terminal cells", error))
}

fn apply_style<W: Write>(output: &mut W, style: Style) -> Result<()> {
    queue!(output, SetAttribute(Attribute::Reset), ResetColor)
        .map_err(|error| ReviewError::io("could not apply terminal style", error))?;
    if let Some(foreground) = style.foreground {
        queue!(output, SetForegroundColor(foreground))
            .map_err(|error| ReviewError::io("could not apply terminal style", error))?;
    }
    if let Some(background) = style.background {
        queue!(output, SetBackgroundColor(background))
            .map_err(|error| ReviewError::io("could not apply terminal style", error))?;
    }
    if style.bold {
        queue!(output, SetAttribute(Attribute::Bold))
            .map_err(|error| ReviewError::io("could not apply terminal style", error))?;
    }
    if style.italic {
        queue!(output, SetAttribute(Attribute::Italic))
            .map_err(|error| ReviewError::io("could not apply terminal style", error))?;
    }
    if style.reverse {
        queue!(output, SetAttribute(Attribute::Reverse))
            .map_err(|error| ReviewError::io("could not apply terminal style", error))?;
    }
    Ok(())
}

fn truncate_width(text: &str, width: usize) -> String {
    let mut output = String::new();
    let mut cells = 0;
    for character in text.chars() {
        let character = terminal_safe_character(character);
        let next = character.width().unwrap_or(0);
        if cells + next > width {
            break;
        }
        output.push(character);
        cells += next;
    }
    output
}

fn terminal_safe_character(character: char) -> char {
    match character {
        '\u{0}'..='\u{1f}' => char::from_u32(0x2400 + u32::from(character)).unwrap_or('�'),
        '\u{7f}' => '␡',
        value if value.is_control() => '�',
        value => value,
    }
}

fn is_ctrl_char(key: KeyEvent, character: char) -> bool {
    key.modifiers.contains(KeyModifiers::CONTROL)
        && matches!(key.code, KeyCode::Char(value) if value.eq_ignore_ascii_case(&character))
}

fn actionable_key_event(key: KeyEvent) -> bool {
    match key.kind {
        KeyEventKind::Press => true,
        KeyEventKind::Repeat => match key.code {
            KeyCode::Up
            | KeyCode::Down
            | KeyCode::Left
            | KeyCode::Right
            | KeyCode::PageUp
            | KeyCode::PageDown
            | KeyCode::Home
            | KeyCode::End
            | KeyCode::Backspace
            | KeyCode::Delete => true,
            KeyCode::Char(_) => !key.modifiers.contains(KeyModifiers::CONTROL),
            _ => false,
        },
        KeyEventKind::Release => false,
    }
}

fn single_line_paste(text: &str) -> String {
    text.chars()
        .filter(|character| !character.is_control())
        .collect()
}

fn char_to_byte(value: &str, char_index: usize) -> usize {
    value
        .char_indices()
        .nth(char_index)
        .map_or(value.len(), |(byte, _)| byte)
}

fn ensure_scroll(scroll: &mut usize, selected: usize, height: usize) {
    if height == 0 {
        return;
    }
    if selected < *scroll {
        *scroll = selected;
    } else if selected >= *scroll + height {
        *scroll = selected + 1 - height;
    }
}

fn clamp_pane_scroll(scroll: &mut usize, row_count: usize, body_height: usize) {
    if row_count <= body_height {
        *scroll = 0;
        return;
    }
    let visible_height = body_height.saturating_sub(1).max(1);
    *scroll = (*scroll).min(row_count.saturating_sub(visible_height));
}

fn scroll_pane(scroll: &mut usize, delta: isize, row_count: usize, body_height: usize) {
    clamp_pane_scroll(scroll, row_count, body_height);
    let maximum = if row_count <= body_height {
        0
    } else {
        row_count.saturating_sub(body_height.saturating_sub(1).max(1))
    };
    *scroll = if delta.is_negative() {
        scroll.saturating_sub(delta.unsigned_abs())
    } else {
        scroll.saturating_add(delta.unsigned_abs()).min(maximum)
    };
}

fn selectable_near_offset(
    items: &[DocumentItem],
    layout: &[usize],
    offset: usize,
) -> Option<usize> {
    if layout.is_empty() {
        return None;
    }
    let offset = offset.min(layout.len() - 1);
    (offset..layout.len())
        .chain((0..offset).rev())
        .map(|row| layout[row])
        .find(|index| items.get(*index).is_some_and(DocumentItem::selectable))
}

fn offset_index(index: usize, delta: isize, len: usize) -> usize {
    if len == 0 {
        return 0;
    }
    if delta < 0 {
        index.saturating_sub(delta.unsigned_abs())
    } else {
        index.saturating_add(delta.unsigned_abs()).min(len - 1)
    }
}

fn base64_encode(value: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut output = String::with_capacity(value.len().div_ceil(3) * 4);
    for chunk in value.chunks(3) {
        let first = chunk[0];
        let second = chunk.get(1).copied().unwrap_or(0);
        let third = chunk.get(2).copied().unwrap_or(0);
        output.push(char::from(TABLE[usize::from(first >> 2)]));
        output.push(char::from(
            TABLE[usize::from((first & 0x03) << 4 | second >> 4)],
        ));
        output.push(if chunk.len() > 1 {
            char::from(TABLE[usize::from((second & 0x0f) << 2 | third >> 6)])
        } else {
            '='
        });
        output.push(if chunk.len() > 2 {
            char::from(TABLE[usize::from(third & 0x3f)])
        } else {
            '='
        });
    }
    output
}

fn osc52_sequence(text: &str, tmux: bool) -> String {
    let sequence = format!("\x1b]52;c;{}\x07", base64_encode(text.as_bytes()));
    if tmux {
        format!("\x1bPtmux;\x1b{sequence}\x1b\\")
    } else {
        sequence
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    use crate::model::{FileStatus, ReviewKind, ReviewSource, create_review_file};

    fn state_with_visible_lines(count: usize) -> ReviewState {
        let old = (0..count)
            .map(|index| format!("line {index}"))
            .collect::<Vec<_>>();
        let mut new = old.clone();
        new[count / 2] = "changed".into();
        ReviewState::new(
            Path::new("/tmp/repo"),
            ReviewSource {
                kind: ReviewKind::Uncommitted,
                target_branch: None,
                base_ref: "HEAD".into(),
            },
            vec![create_review_file(
                "src/a.rs".into(),
                FileStatus::Modified,
                &old,
                &new,
                None,
                false,
                vec![],
            )],
        )
    }

    fn state_with_files(count: usize) -> ReviewState {
        let files = (0..count)
            .map(|index| {
                create_review_file(
                    format!("src/file_{index:02}.rs"),
                    FileStatus::Modified,
                    &["old".into()],
                    &["new".into()],
                    None,
                    false,
                    vec![],
                )
            })
            .collect();
        ReviewState::new(
            Path::new("/tmp/repo"),
            ReviewSource {
                kind: ReviewKind::Uncommitted,
                target_branch: None,
                base_ref: "HEAD".into(),
            },
            files,
        )
    }

    fn file_with_status(status: FileStatus) -> ReviewFile {
        create_review_file(
            "tree.rs".into(),
            status,
            &["old".into()],
            &["new".into()],
            None,
            false,
            vec![],
        )
    }

    #[test]
    fn base64_encoding_matches_rfc_examples() {
        assert_eq!(base64_encode(b"f"), "Zg==");
        assert_eq!(base64_encode(b"fo"), "Zm8=");
        assert_eq!(base64_encode(b"foo"), "Zm9v");
    }

    #[test]
    fn editor_visual_lines_preserve_immediate_blank_line() {
        let lines = editor_visual_lines("one\n\ntwo", 20);
        assert_eq!(lines[1].0, "");
        assert_eq!(lines.len(), 3);
    }

    #[test]
    fn far_right_mouse_coordinates_do_not_affect_scroll_math() {
        assert_eq!(offset_index(10, 3, 100), 13);
        assert_eq!(offset_index(10, -3, 100), 7);
    }

    #[test]
    fn mouse_hover_routes_wheel_events_to_the_visible_pane() {
        let mut state = state_with_files(20);
        let mut app = ReviewApp::new(&mut state);
        app.file_pane_visible = true;
        app.last_width = 120;
        app.last_height = 40;
        app.last_left_width = 40;

        assert_eq!(app.mouse_region(10, 5), MouseRegion::FileTree);
        assert_eq!(app.mouse_region(10, 18), MouseRegion::FileTree);
        assert_eq!(app.mouse_region(10, 19), MouseRegion::Comments);
        assert_eq!(app.mouse_region(39, 38), MouseRegion::Comments);
        assert_eq!(app.mouse_region(40, 5), MouseRegion::Outside);
        assert_eq!(app.mouse_region(41, 5), MouseRegion::Review);
        assert_eq!(app.mouse_region(119, 38), MouseRegion::Review);
        assert_eq!(app.mouse_region(120, 5), MouseRegion::Outside);
        assert_eq!(app.mouse_region(10, 39), MouseRegion::Outside);

        app.file_pane_visible = false;
        assert_eq!(app.mouse_region(10, 5), MouseRegion::Review);
    }

    #[test]
    fn wheel_over_file_tree_scrolls_its_view_without_moving_the_review() {
        let mut state = state_with_files(20);
        let selected_file = state.file_pane_index;
        let mut app = ReviewApp::new(&mut state);
        app.file_pane_visible = true;
        app.last_width = 120;
        app.last_height = 20;
        app.last_left_width = 40;

        app.handle_mouse(MouseEvent {
            kind: MouseEventKind::ScrollDown,
            column: 10,
            row: 3,
            modifiers: KeyModifiers::NONE,
        });

        assert_eq!(app.file_scroll, MOUSE_SCROLL_LINES);
        assert_eq!(app.review_scroll, 0);
        assert_eq!(app.state.file_pane_index, selected_file);
        assert!(!app.file_scroll_follows_selection);

        let mut frame = RenderFrame::new(40, 9);
        app.draw_file_tree(&mut frame, 0, 9, 40);
        assert_eq!(app.file_scroll, MOUSE_SCROLL_LINES);

        app.handle_mouse(MouseEvent {
            kind: MouseEventKind::ScrollUp,
            column: 10,
            row: 3,
            modifiers: KeyModifiers::NONE,
        });
        assert_eq!(app.file_scroll, 0);
    }

    #[test]
    fn wheel_over_comment_list_scrolls_comments_independently() {
        let mut state = state_with_visible_lines(30);
        for row in 0..12 {
            state.selection = Some(Selection::Code {
                file_path: "src/a.rs".into(),
                anchor_row: row,
                active_row: row,
            });
            assert!(state.add_comment(&format!("comment {row}")).is_some());
        }
        let mut app = ReviewApp::new(&mut state);
        app.file_pane_visible = true;
        app.last_width = 120;
        app.last_height = 20;
        app.last_left_width = 40;

        app.handle_mouse(MouseEvent {
            kind: MouseEventKind::ScrollDown,
            column: 10,
            row: 12,
            modifiers: KeyModifiers::NONE,
        });

        assert_eq!(app.comment_scroll, MOUSE_SCROLL_LINES);
        assert_eq!(app.review_scroll, 0);
        assert!(!app.comment_scroll_follows_selection);

        let mut frame = RenderFrame::new(40, 10);
        app.draw_comment_pane(&mut frame, 0, 10, 40);
        assert_eq!(app.comment_scroll, MOUSE_SCROLL_LINES);
    }

    #[test]
    fn upward_mouse_drag_preserves_the_original_bottom_anchor() {
        let mut state = state_with_visible_lines(20);
        let item_for_row = |state: &ReviewState, row| {
            state
                .document_items()
                .iter()
                .position(|item| item.row_index == Some(row))
                .unwrap()
        };
        let bottom = item_for_row(&state, 8);
        let middle = item_for_row(&state, 5);
        let top = item_for_row(&state, 2);
        state.select_document_index(bottom);

        let mut app = ReviewApp::new(&mut state);
        app.screen_map.insert(10, middle);
        app.screen_map.insert(7, top);
        app.handle_mouse(MouseEvent {
            kind: MouseEventKind::Drag(MouseButton::Left),
            column: 20,
            row: 10,
            modifiers: KeyModifiers::NONE,
        });
        app.handle_mouse(MouseEvent {
            kind: MouseEventKind::Drag(MouseButton::Left),
            column: 20,
            row: 7,
            modifiers: KeyModifiers::NONE,
        });

        assert_eq!(app.state.selected_range(), Some(("src/a.rs", 2, 8)));
        assert!(matches!(
            app.state.selection,
            Some(Selection::Code {
                anchor_row: 8,
                active_row: 2,
                ..
            })
        ));
    }

    #[test]
    fn held_navigation_repeats_without_turning_held_interrupt_into_double_interrupt() {
        let mut state = state_with_visible_lines(20);
        let mut app = ReviewApp::new(&mut state);
        let initial = app.state.active_document_index().unwrap();
        app.handle_event(Event::Key(KeyEvent::new_with_kind(
            KeyCode::Down,
            KeyModifiers::NONE,
            KeyEventKind::Repeat,
        )));
        assert!(app.state.active_document_index().unwrap() > initial);

        let interrupt = KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL);
        app.handle_event(Event::Key(interrupt));
        assert!(app.interrupt_armed);
        app.handle_event(Event::Key(KeyEvent::new_with_kind(
            KeyCode::Char('c'),
            KeyModifiers::CONTROL,
            KeyEventKind::Repeat,
        )));
        assert!(app.interrupt_armed);
        assert!(!app.quit_requested);
    }

    #[test]
    fn bracketed_paste_reaches_search_command_and_multiline_comment_inputs() {
        let mut state = state_with_visible_lines(20);
        let mut app = ReviewApp::new(&mut state);

        app.handle_key(KeyEvent::new(KeyCode::Char('/'), KeyModifiers::NONE));
        app.handle_event(Event::Paste("changed\r\n".into()));
        assert_eq!(app.search_buffer, "changed");
        app.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));

        app.handle_key(KeyEvent::new(KeyCode::Char(':'), KeyModifiers::NONE));
        app.handle_event(Event::Paste("center\n".into()));
        assert_eq!(app.command_buffer, "center");
        app.handle_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE));

        app.start_new_comment();
        app.handle_event(Event::Paste("first\nsecond".into()));
        assert_eq!(app.comment_buffer, "first\nsecond");
    }

    #[test]
    fn opening_and_typing_a_comment_preserves_a_contextual_viewport() {
        let mut state = state_with_visible_lines(60);
        state.selection = Some(Selection::Code {
            file_path: "src/a.rs".into(),
            anchor_row: 25,
            active_row: 25,
        });
        let mut app = ReviewApp::new(&mut state);
        app.last_width = 100;
        app.last_height = 20;
        let active = app.state.active_document_index().unwrap();
        app.review_scroll = active.saturating_sub(6);
        let contextual_scroll = app.review_scroll;

        app.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert!(app.comment_mode);
        assert_eq!(app.review_scroll, contextual_scroll);

        app.handle_key(KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE));
        assert_eq!(app.review_scroll, contextual_scroll);
    }

    #[test]
    fn typing_scrolls_only_enough_to_reveal_an_offscreen_comment_editor() {
        let mut state = state_with_visible_lines(60);
        state.selection = Some(Selection::Code {
            file_path: "src/a.rs".into(),
            anchor_row: 25,
            active_row: 25,
        });
        let mut app = ReviewApp::new(&mut state);
        app.last_width = 100;
        app.last_height = 10;
        let active = app.state.active_document_index().unwrap();
        app.review_scroll = active.saturating_sub(8);
        let contextual_scroll = app.review_scroll;

        app.handle_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(app.review_scroll, contextual_scroll);

        app.handle_key(KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE));
        assert!(app.review_scroll > contextual_scroll);
        assert!(app.review_scroll < active);
    }

    #[test]
    fn long_comment_editor_window_keeps_the_cursor_visible() {
        let mut state = state_with_visible_lines(20);
        let mut app = ReviewApp::new(&mut state);
        app.start_new_comment();
        app.comment_buffer = (0..10)
            .map(|index| format!("line {index}"))
            .collect::<Vec<_>>()
            .join("\n");
        app.comment_cursor = app.comment_buffer.chars().count();
        let lines = app.comment_editor_lines(80);
        let window = app.comment_editor_window(lines, 3);

        assert_eq!(window.len(), 3);
        assert_eq!(app.comment_editor_scroll, 7);
        assert!(window.last().is_some_and(|(_, cursor)| cursor.is_some()));
    }

    #[test]
    fn search_marks_literal_character_ranges() {
        assert_eq!(
            search_match_chars("one one", "one"),
            vec![true, true, true, false, true, true, true]
        );
    }

    #[test]
    fn terminal_text_renders_control_characters_as_visible_glyphs() {
        let safe = truncate_width("before\x1b[2J\nafter\t\u{7f}", 80);
        assert_eq!(safe, "before␛[2J␊after␉␡");
        assert!(!safe.chars().any(char::is_control));
    }

    #[test]
    fn light_terminal_backgrounds_match_the_previous_palette() {
        assert_eq!(
            code_background(false, LineKind::Addition),
            Some(Color::AnsiValue(194))
        );
        assert_eq!(
            code_background(true, LineKind::Addition),
            Some(Color::AnsiValue(193))
        );
        assert_eq!(
            code_background(false, LineKind::Deletion),
            Some(Color::AnsiValue(224))
        );
        assert_eq!(
            code_background(true, LineKind::Deletion),
            Some(Color::AnsiValue(223))
        );
        assert_eq!(
            code_background(true, LineKind::Context),
            Some(Color::AnsiValue(229))
        );
        assert_eq!(code_background(false, LineKind::Context), None);
        assert_eq!(COMMENT_BACKGROUND, Color::AnsiValue(230));
        assert_eq!(SEARCH_BACKGROUND, Color::AnsiValue(226));
    }

    #[test]
    fn file_tree_status_colors_are_light_theme_safe_and_distinct() {
        assert_eq!(file_status_color(FileStatus::Unchanged), Color::DarkGrey);
        assert_eq!(file_status_color(FileStatus::Added), Color::DarkGreen);
        assert_eq!(file_status_color(FileStatus::Modified), Color::DarkBlue);
        assert_eq!(file_status_color(FileStatus::Deleted), Color::DarkRed);
        assert_eq!(file_status_color(FileStatus::Renamed), Color::DarkMagenta);
        assert_eq!(file_status_color(FileStatus::Binary), Color::DarkCyan);
        assert_eq!(file_status_color(FileStatus::Mode), Color::DarkYellow);
        assert_eq!(
            file_status_color(FileStatus::TypeChanged),
            Color::DarkMagenta
        );
    }

    #[test]
    fn file_tree_colors_only_the_status_marker_and_preserves_selection() {
        let added = file_with_status(FileStatus::Added);
        let line = file_tree_file_line(&added, "new.rs", 1, 2, false, false, 30);
        assert_eq!(line.segments.len(), 3);
        assert_eq!(line.segments[0].text, "  ");
        assert_eq!(line.segments[0].style.foreground, None);
        assert_eq!(line.segments[1].text, "A ");
        assert_eq!(line.segments[1].style.foreground, Some(Color::DarkGreen));
        assert!(line.segments[1].style.bold);
        assert_eq!(line.segments[2].text, "new.rs (2)");
        assert_eq!(line.segments[2].style.foreground, None);

        let deleted = file_with_status(FileStatus::Deleted);
        let selected = file_tree_file_line(&deleted, "old.rs", 0, 0, true, true, 30);
        assert_eq!(selected.segments[0].style.foreground, Some(Color::DarkRed));
        assert!(selected.segments[0].style.reverse);
        assert!(selected.segments[0].style.bold);
        assert!(selected.segments[1].style.reverse);
        assert!(selected.segments[1].style.bold);
        let selected_text = selected
            .segments
            .iter()
            .map(|segment| segment.text.as_str())
            .collect::<String>();
        assert_eq!(selected_text.width(), 30);
    }

    #[test]
    fn syntax_roles_use_terminal_colors_and_background_contrast() {
        assert_eq!(syntax_foreground(DEFAULT_FOREGROUND, false), None);
        assert_eq!(
            syntax_foreground(DEFAULT_FOREGROUND, true),
            Some(Color::Black)
        );
        assert_eq!(
            syntax_foreground(STRING_FOREGROUND, false),
            Some(Color::DarkGreen)
        );
        assert_eq!(
            syntax_foreground(STRING_FOREGROUND, true),
            Some(Color::DarkMagenta)
        );
        assert_eq!(
            syntax_foreground(TYPE_FOREGROUND, false),
            Some(Color::DarkCyan)
        );
        assert_eq!(
            syntax_foreground(TYPE_FOREGROUND, true),
            Some(Color::DarkBlue)
        );
        assert_eq!(
            syntax_foreground(OPERATOR_FOREGROUND, false),
            Some(Color::DarkRed)
        );
        assert_eq!(
            syntax_foreground(OPERATOR_FOREGROUND, true),
            Some(Color::Black)
        );
    }

    #[test]
    fn focus_and_selection_restore_reverse_video() {
        let focused_header = pane_header_style(true);
        assert!(focused_header.reverse && focused_header.bold);
        assert_eq!(focused_header.foreground, None);
        let unfocused_header = pane_header_style(false);
        assert!(!unfocused_header.reverse);
        assert_eq!(unfocused_header.foreground, Some(Color::DarkCyan));
        assert!(selection_style(false).reverse);
        assert!(selection_style(true).bold);
    }

    #[test]
    fn retained_frame_emits_nothing_for_an_identical_redraw() {
        let mut frame = RenderFrame::new(12, 3);
        draw_region_line(
            &mut frame,
            0,
            1,
            12,
            &StyledLine::plain("stable", Style::default()),
        );

        let mut initial = Vec::new();
        render_frame(&mut initial, &frame, None).unwrap();
        let initial = String::from_utf8(initial).unwrap();
        assert!(initial.contains("\x1b[?2026h"));
        assert!(initial.contains("\x1b[?2026l"));
        assert!(!initial.contains("\x1b[2J"));

        let mut unchanged = Vec::new();
        render_frame(&mut unchanged, &frame, Some(&frame)).unwrap();
        assert!(unchanged.is_empty());
    }

    #[test]
    fn retained_frame_only_rewrites_changed_rows() {
        let mut previous = RenderFrame::new(12, 3);
        for y in 0..3 {
            draw_region_line(
                &mut previous,
                0,
                y,
                12,
                &StyledLine::plain(format!("row {y}"), Style::default()),
            );
        }
        let mut current = previous.clone();
        current.rows[1].clear();
        draw_region_line(
            &mut current,
            0,
            1,
            12,
            &StyledLine::plain("changed", Style::default()),
        );

        let mut output = Vec::new();
        render_frame(&mut output, &current, Some(&previous)).unwrap();
        let output = String::from_utf8(output).unwrap();
        assert!(output.contains("\x1b[2;1H"));
        assert!(!output.contains("\x1b[1;1H"));
        assert!(!output.contains("\x1b[3;1H"));
        assert!(!output.contains("row 0"));
        assert!(!output.contains("row 2"));
        assert!(!output.contains("\x1b[2J"));
    }

    #[test]
    fn second_consecutive_interrupt_cancels_instead_of_delivering() {
        let mut state = ReviewState::new(
            Path::new("/tmp/repo"),
            ReviewSource {
                kind: ReviewKind::Uncommitted,
                target_branch: None,
                base_ref: "HEAD".into(),
            },
            vec![],
        );
        let mut app = ReviewApp::new(&mut state);
        let interrupt = KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL);
        app.handle_key(interrupt);
        assert!(app.interrupt_armed);
        assert!(!app.quit_requested);
        app.handle_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE));
        assert!(!app.interrupt_armed);
        app.handle_key(interrupt);
        app.handle_key(interrupt);
        assert!(app.quit_requested);
        assert!(app.cancel_requested);
    }
}
