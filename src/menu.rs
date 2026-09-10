use std::io::{self, IsTerminal, Write};

use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, size};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::error::{Result, ReviewError};

const BRANCH_PAGE_SIZE: usize = 5;

#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct MenuFrame {
    terminal_width: usize,
    rows: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MenuOption {
    pub label: String,
    pub value: String,
    pub detail: String,
}

impl MenuOption {
    #[must_use]
    pub fn new(label: impl Into<String>, value: impl Into<String>) -> Self {
        Self {
            label: label.into(),
            value: value.into(),
            detail: String::new(),
        }
    }

    #[must_use]
    pub fn detail(mut self, detail: impl Into<String>) -> Self {
        self.detail = detail.into();
        self
    }
}

pub fn select_option(
    title: &str,
    options: &[MenuOption],
    cancel_requires_double: bool,
) -> Result<String> {
    select_option_to(title, options, cancel_requires_double, false)
}

pub fn select_option_to(
    title: &str,
    options: &[MenuOption],
    cancel_requires_double: bool,
    stderr: bool,
) -> Result<String> {
    if options.is_empty() {
        return Err(ReviewError::Message(
            "menu requires at least one option".to_owned(),
        ));
    }
    let interactive = io::stdin().is_terminal()
        && if stderr {
            io::stderr().is_terminal()
        } else {
            io::stdout().is_terminal()
        };
    if !interactive {
        return select_option_text(title, options, stderr);
    }
    let mut output: Box<dyn Write> = if stderr {
        Box::new(io::stderr().lock())
    } else {
        Box::new(io::stdout().lock())
    };
    let _raw = RawMode::enter()?;
    let mut selected = 0;
    let mut previous_frame = MenuFrame::default();
    let mut cancel_armed = false;
    loop {
        let (width, height) = size().unwrap_or((80, 24));
        let lines = render_option_lines(title, options, selected, cancel_armed, width, height);
        previous_frame = replace_menu(&mut output, &previous_frame, &lines)?;
        let Event::Key(key) =
            event::read().map_err(|error| ReviewError::io("could not read menu input", error))?
        else {
            continue;
        };
        if !actionable_menu_key_event(key) {
            continue;
        }
        match menu_key(key) {
            MenuKey::Up => {
                selected = selected.saturating_sub(1);
                cancel_armed = false;
            }
            MenuKey::Down => {
                selected = (selected + 1).min(options.len() - 1);
                cancel_armed = false;
            }
            MenuKey::Home => {
                selected = 0;
                cancel_armed = false;
            }
            MenuKey::End => {
                selected = options.len() - 1;
                cancel_armed = false;
            }
            MenuKey::Enter => {
                clear_menu(&mut output, &previous_frame)?;
                return Ok(options[selected].value.clone());
            }
            MenuKey::Cancel => {
                if cancel_requires_double && !cancel_armed {
                    cancel_armed = true;
                } else {
                    clear_menu(&mut output, &previous_frame)?;
                    return Err(ReviewError::Cancelled);
                }
            }
            MenuKey::Other => cancel_armed = false,
        }
    }
}

pub fn prompt_positive_count(title: &str) -> Result<usize> {
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        println!("{title}:");
        let mut input = String::new();
        io::stdin()
            .read_line(&mut input)
            .map_err(|error| ReviewError::io("could not read commit count", error))?;
        return input
            .trim()
            .parse::<usize>()
            .ok()
            .filter(|count| *count > 0)
            .ok_or_else(|| {
                ReviewError::InvalidArgument("commit count must be a positive integer".to_owned())
            });
    }
    let _raw = RawMode::enter()?;
    let mut output = io::stdout().lock();
    let mut frame = MenuFrame::default();
    let mut input = String::new();
    loop {
        frame = replace_menu(
            &mut output,
            &frame,
            &[
                format!("{title}: {input}"),
                "Enter: review · Esc/Ctrl+C: cancel".to_owned(),
            ],
        )?;
        let Event::Key(key) =
            event::read().map_err(|error| ReviewError::io("could not read count", error))?
        else {
            continue;
        };
        if !actionable_menu_key_event(key) {
            continue;
        }
        match key.code {
            KeyCode::Esc => {
                clear_menu(&mut output, &frame)?;
                return Err(ReviewError::Cancelled);
            }
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                clear_menu(&mut output, &frame)?;
                return Err(ReviewError::Cancelled);
            }
            KeyCode::Char(digit) if digit.is_ascii_digit() => input.push(digit),
            KeyCode::Backspace => {
                input.pop();
            }
            KeyCode::Enter => {
                if let Some(count) = input.parse::<usize>().ok().filter(|count| *count > 0) {
                    clear_menu(&mut output, &frame)?;
                    return Ok(count);
                }
            }
            _ => {}
        }
    }
}

pub fn select_branch_target(
    title: &str,
    current_branch: &str,
    branches: &[String],
    cancel_requires_double: bool,
) -> Result<String> {
    if branches.is_empty() {
        return Err(ReviewError::Message(
            "branch menu requires at least one branch".to_owned(),
        ));
    }
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        let options = branches
            .iter()
            .map(|branch| MenuOption::new(format!("{current_branch} -> {branch}"), branch))
            .collect::<Vec<_>>();
        return select_option_text(title, &options, false);
    }
    let mut output = io::stdout().lock();
    let _raw = RawMode::enter()?;
    let mut selected = 0;
    let mut query = String::new();
    let mut previous_frame = MenuFrame::default();
    let mut cancel_armed = false;
    loop {
        let filtered = filter_branches(branches, &query);
        selected = selected.min(filtered.len().saturating_sub(1));
        let lines = render_branch_lines(
            title,
            current_branch,
            &filtered,
            selected,
            &query,
            cancel_armed,
        );
        previous_frame = replace_menu(&mut output, &previous_frame, &lines)?;
        let Event::Key(key) =
            event::read().map_err(|error| ReviewError::io("could not read branch input", error))?
        else {
            continue;
        };
        if !actionable_menu_key_event(key) {
            continue;
        }
        match branch_menu_key(key) {
            MenuKey::Up => {
                selected = selected.saturating_sub(1);
                cancel_armed = false;
            }
            MenuKey::Down => {
                selected = (selected + 1).min(filtered.len().saturating_sub(1));
                cancel_armed = false;
            }
            MenuKey::Home => {
                selected = 0;
                cancel_armed = false;
            }
            MenuKey::End => {
                selected = filtered.len().saturating_sub(1);
                cancel_armed = false;
            }
            MenuKey::Enter if !filtered.is_empty() => {
                let choice = filtered[selected].to_owned();
                clear_menu(&mut output, &previous_frame)?;
                return Ok(choice);
            }
            MenuKey::Cancel => {
                if cancel_requires_double && !cancel_armed {
                    cancel_armed = true;
                } else {
                    clear_menu(&mut output, &previous_frame)?;
                    return Err(ReviewError::Cancelled);
                }
            }
            _ => match key.code {
                KeyCode::Backspace => {
                    query.pop();
                    selected = 0;
                    cancel_armed = false;
                }
                KeyCode::Char(character)
                    if !key
                        .modifiers
                        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) =>
                {
                    query.push(character);
                    selected = 0;
                    cancel_armed = false;
                }
                _ => cancel_armed = false,
            },
        }
    }
}

fn select_option_text(title: &str, options: &[MenuOption], stderr: bool) -> Result<String> {
    let mut output: Box<dyn Write> = if stderr {
        Box::new(io::stderr().lock())
    } else {
        Box::new(io::stdout().lock())
    };
    writeln!(output, "{}:", terminal_safe_text(title))
        .map_err(|error| ReviewError::io("could not render menu", error))?;
    for (index, option) in options.iter().enumerate() {
        let detail = if option.detail.is_empty() {
            String::new()
        } else {
            format!(" - {}", option.detail)
        };
        let label = terminal_safe_text(&format!("{}{detail}", option.label));
        let default = if index == 0 { " (default)" } else { "" };
        writeln!(output, "  {}. {label}{default}", index + 1)
            .map_err(|error| ReviewError::io("could not render menu", error))?;
    }
    write!(output, "Select option [1]: ")
        .and_then(|()| output.flush())
        .map_err(|error| ReviewError::io("could not render menu", error))?;
    let mut choice = String::new();
    io::stdin()
        .read_line(&mut choice)
        .map_err(|error| ReviewError::io("could not read menu input", error))?;
    let choice = choice.lines().next().unwrap_or_default().trim();
    if choice.is_empty() {
        return Ok(options[0].value.clone());
    }
    if matches!(choice, "q" | "Q") {
        return Err(ReviewError::Cancelled);
    }
    match choice.parse::<usize>() {
        Ok(index) if (1..=options.len()).contains(&index) => {
            return Ok(options[index - 1].value.clone());
        }
        _ => {}
    }
    options
        .iter()
        .find(|option| option.value == choice || option.label == choice)
        .map(|option| option.value.clone())
        .ok_or_else(|| ReviewError::InvalidArgument("please select a listed option".to_owned()))
}

struct RawMode;

impl RawMode {
    fn enter() -> Result<Self> {
        enable_raw_mode()
            .map_err(|error| ReviewError::io("could not enable terminal raw mode", error))?;
        Ok(Self)
    }
}

impl Drop for RawMode {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum MenuKey {
    Up,
    Down,
    Home,
    End,
    Enter,
    Cancel,
    Other,
}

fn menu_key(key: KeyEvent) -> MenuKey {
    if key.modifiers.contains(KeyModifiers::CONTROL) && matches!(key.code, KeyCode::Char('c' | 'C'))
    {
        return MenuKey::Cancel;
    }
    match key.code {
        KeyCode::Up | KeyCode::Char('k' | 'K') => MenuKey::Up,
        KeyCode::Down | KeyCode::Char('j' | 'J') => MenuKey::Down,
        KeyCode::Home => MenuKey::Home,
        KeyCode::End => MenuKey::End,
        KeyCode::Enter => MenuKey::Enter,
        KeyCode::Esc | KeyCode::Char('q' | 'Q') => MenuKey::Cancel,
        _ => MenuKey::Other,
    }
}

fn branch_menu_key(key: KeyEvent) -> MenuKey {
    if key.modifiers.contains(KeyModifiers::CONTROL) && matches!(key.code, KeyCode::Char('c' | 'C'))
    {
        return MenuKey::Cancel;
    }
    match key.code {
        KeyCode::Up => MenuKey::Up,
        KeyCode::Down => MenuKey::Down,
        KeyCode::Home => MenuKey::Home,
        KeyCode::End => MenuKey::End,
        KeyCode::Enter => MenuKey::Enter,
        KeyCode::Esc => MenuKey::Cancel,
        _ => MenuKey::Other,
    }
}

fn actionable_menu_key_event(key: KeyEvent) -> bool {
    match key.kind {
        KeyEventKind::Press => true,
        KeyEventKind::Repeat => match key.code {
            KeyCode::Up | KeyCode::Down | KeyCode::Home | KeyCode::End | KeyCode::Backspace => true,
            KeyCode::Char(_) => !key.modifiers.contains(KeyModifiers::CONTROL),
            _ => false,
        },
        KeyEventKind::Release => false,
    }
}

fn render_option_lines(
    title: &str,
    options: &[MenuOption],
    selected: usize,
    cancel_armed: bool,
    width: u16,
    height: u16,
) -> Vec<String> {
    let width = usize::from(width.saturating_sub(1).max(1));
    // Keep the cursor's trailing newline on screen, so repainting can reach every row.
    let height = usize::from(height.saturating_sub(1).max(1));
    let title = wrap_menu_line(
        &format!("{title}  (Use Up/Down and Enter; q/Esc cancels)"),
        width,
    )
    .remove(0);
    let reserved = 2 + usize::from(cancel_armed);
    let budget = height.saturating_sub(reserved).max(1);
    let rows = options
        .iter()
        .enumerate()
        .map(|(index, option)| {
            let prefix = if index == selected { '>' } else { ' ' };
            let detail = if option.detail.is_empty() {
                String::new()
            } else {
                format!(" - {}", option.detail)
            };
            let mut rows = wrap_menu_line(&format!("{prefix} {}{detail}", option.label), width);
            rows.truncate(budget);
            rows
        })
        .collect::<Vec<_>>();
    let mut start = selected;
    let mut end = selected + 1;
    let mut used = rows[selected].len();
    while start > 0 && used + rows[start - 1].len() <= budget {
        start -= 1;
        used += rows[start].len();
    }
    while end < rows.len() && used + rows[end].len() <= budget {
        used += rows[end].len();
        end += 1;
    }
    let mut lines = Vec::new();
    if height > reserved {
        lines.push(title);
    }
    lines.extend(rows[start..end].iter().flatten().cloned());
    if lines.len() < height {
        lines.push(
            wrap_menu_line(
                &format!(
                    "{}/{} · {} above · {} below",
                    selected + 1,
                    options.len(),
                    start,
                    options.len() - end
                ),
                width,
            )
            .remove(0),
        );
    }
    if cancel_armed && lines.len() < height {
        lines.push(wrap_menu_line("Press Ctrl+C again to cancel.", width).remove(0));
    }
    lines
}

fn render_branch_lines(
    title: &str,
    current: &str,
    branches: &[&str],
    selected: usize,
    query: &str,
    cancel_armed: bool,
) -> Vec<String> {
    let mut lines = vec![format!(
        "{title}  (type to filter; Up/Down and Enter; Esc cancels)"
    )];
    if branches.is_empty() {
        lines.push("  No branches match.".to_owned());
    } else {
        let window_start = branch_window_start(selected, branches.len());
        let visible =
            &branches[window_start..(window_start + BRANCH_PAGE_SIZE).min(branches.len())];
        for (offset, branch) in visible.iter().enumerate() {
            let index = window_start + offset;
            let prefix = if index == selected { '>' } else { ' ' };
            lines.push(format!("{prefix} {current} -> {branch}"));
        }
        let below = branches.len() - window_start - visible.len();
        if window_start > 0 || below > 0 {
            let mut counts = Vec::new();
            if window_start > 0 {
                counts.push(format!("{window_start} above"));
            }
            if below > 0 {
                counts.push(format!("{below} below"));
            }
            lines.push(format!("  {}", counts.join(", ")));
        }
    }
    lines.push(format!("Search: {query}"));
    if cancel_armed {
        lines.push("Press Ctrl+C again to cancel.".to_owned());
    }
    lines
}

fn filter_branches<'a>(branches: &'a [String], query: &str) -> Vec<&'a str> {
    let query = query.to_lowercase();
    branches
        .iter()
        .filter(|branch| query.is_empty() || branch.to_lowercase().contains(&query))
        .map(String::as_str)
        .collect()
}

fn branch_window_start(selected: usize, count: usize) -> usize {
    if count <= BRANCH_PAGE_SIZE {
        0
    } else {
        selected
            .saturating_sub(BRANCH_PAGE_SIZE / 2)
            .min(count - BRANCH_PAGE_SIZE)
    }
}

fn replace_menu(
    output: &mut dyn Write,
    previous: &MenuFrame,
    lines: &[String],
) -> Result<MenuFrame> {
    let width = usize::from(size().map_or(80, |(width, _)| width).max(1));
    let previous_rows = reflowed_previous_rows(previous, width);
    let drawable_width = width.saturating_sub(1).max(1);
    let rows = menu_display_rows(lines, drawable_width);
    replace_menu_rows(output, &previous_rows, &rows, width)?;
    Ok(MenuFrame {
        terminal_width: width,
        rows,
    })
}

fn reflowed_previous_rows(previous: &MenuFrame, width: usize) -> Vec<String> {
    if previous.terminal_width == 0 || previous.terminal_width == width {
        previous.rows.clone()
    } else {
        previous
            .rows
            .iter()
            .flat_map(|row| wrap_menu_line(row, width))
            .collect()
    }
}

#[cfg(test)]
fn replace_menu_at_width(
    output: &mut dyn Write,
    previous_rows: &[String],
    lines: &[String],
    width: usize,
) -> Result<Vec<String>> {
    let rows = menu_display_rows(lines, width);
    replace_menu_rows(output, previous_rows, &rows, width)?;
    Ok(rows)
}

fn replace_menu_rows(
    output: &mut dyn Write,
    previous_rows: &[String],
    rows: &[String],
    terminal_width: usize,
) -> Result<()> {
    if rows == previous_rows {
        return Ok(());
    }
    write!(output, "\x1b[?2026h\x1b[?7l")
        .map_err(|error| ReviewError::io("could not begin menu frame", error))?;
    if !previous_rows.is_empty() {
        write!(output, "\x1b[{}F", previous_rows.len())
            .map_err(|error| ReviewError::io("could not position menu frame", error))?;
    }
    let rendered_height = rows.len().max(previous_rows.len());
    for index in 0..rendered_height {
        let old = previous_rows.get(index).map_or("", String::as_str);
        let new = rows.get(index).map_or("", String::as_str);
        if old != new {
            let padding = terminal_width.saturating_sub(new.width());
            write!(output, "\r{new}{}", " ".repeat(padding))
                .map_err(|error| ReviewError::io("could not render menu row", error))?;
        }
        write!(output, "\r\n")
            .map_err(|error| ReviewError::io("could not advance menu frame", error))?;
    }
    if rendered_height > rows.len() {
        write!(output, "\x1b[{}F", rendered_height - rows.len())
            .map_err(|error| ReviewError::io("could not finish menu position", error))?;
    }
    write!(output, "\x1b[?7h\x1b[?2026l")
        .map_err(|error| ReviewError::io("could not finish menu frame", error))?;
    output
        .flush()
        .map_err(|error| ReviewError::io("could not render menu", error))?;
    Ok(())
}

fn menu_display_rows(lines: &[String], width: usize) -> Vec<String> {
    lines
        .iter()
        .flat_map(|line| wrap_menu_line(line, width))
        .collect()
}

fn wrap_menu_line(line: &str, width: usize) -> Vec<String> {
    let width = width.max(1);
    if line.is_empty() {
        return vec![String::new()];
    }
    let mut rows = Vec::new();
    let mut row = String::new();
    let mut cells = 0;
    for character in line.chars() {
        let character = terminal_safe_character(character);
        let character_width = character.width().unwrap_or(0);
        if cells > 0 && cells + character_width > width {
            rows.push(std::mem::take(&mut row));
            cells = 0;
        }
        row.push(character);
        cells += character_width;
    }
    rows.push(row);
    rows
}

fn terminal_safe_text(text: &str) -> String {
    text.chars().map(terminal_safe_character).collect()
}

fn terminal_safe_character(character: char) -> char {
    match character {
        '\u{0}'..='\u{1f}' => char::from_u32(0x2400 + u32::from(character)).unwrap_or('�'),
        '\u{7f}' => '␡',
        value if value.is_control() => '�',
        value => value,
    }
}

fn clear_menu(output: &mut dyn Write, frame: &MenuFrame) -> Result<()> {
    let _ = replace_menu(output, frame, &[])?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_menu_keeps_pr_style_first() {
        let options = [
            MenuOption::new("Review PR-style changes", "branch"),
            MenuOption::new("Review uncommitted changes", "uncommitted"),
        ];
        let lines = render_option_lines("Review source", &options, 0, false, 80, 24);
        assert!(lines[1].starts_with("> Review PR-style"));
    }

    #[test]
    fn option_menu_keeps_selection_visible_with_wrapped_labels_and_small_panes() {
        let options = (0..20)
            .map(|index| {
                MenuOption::new(
                    format!("commit {index:02} {}", "long subject ".repeat(8)),
                    "",
                )
            })
            .collect::<Vec<_>>();
        for height in [2, 4, 16, 24] {
            for selected in [0, 1, 10, 19] {
                for armed in [false, true] {
                    let lines = render_option_lines(
                        "Recent commits",
                        &options,
                        selected,
                        armed,
                        40,
                        height,
                    );
                    assert!(lines.len() < usize::from(height));
                    assert!(lines.iter().all(|line| line.width() <= 39));
                    assert!(
                        lines
                            .iter()
                            .any(|line| line.starts_with(&format!("> commit {selected:02}")))
                    );
                }
            }
        }
    }

    #[test]
    fn branch_menu_renders_at_most_five_branches() {
        let branches = (0..10).map(|index| format!("b{index}")).collect::<Vec<_>>();
        let filtered = filter_branches(&branches, "");
        let lines = render_branch_lines("Target", "feature", &filtered, 7, "", false);
        assert_eq!(lines.iter().filter(|line| line.contains(" -> ")).count(), 5);
    }

    #[test]
    fn branch_picker_keeps_printable_navigation_letters_available_for_filtering() {
        for character in ['q', 'j', 'k', 'Q', 'J', 'K'] {
            assert_eq!(
                branch_menu_key(KeyEvent::new(KeyCode::Char(character), KeyModifiers::NONE)),
                MenuKey::Other
            );
        }
        assert_eq!(
            branch_menu_key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE)),
            MenuKey::Up
        );
        assert_eq!(
            branch_menu_key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE)),
            MenuKey::Cancel
        );
        assert_eq!(
            menu_key(KeyEvent::new(KeyCode::Char('q'), KeyModifiers::NONE)),
            MenuKey::Cancel
        );
    }

    #[test]
    fn held_menu_navigation_repeats_but_held_cancel_does_not() {
        assert!(actionable_menu_key_event(KeyEvent::new_with_kind(
            KeyCode::Up,
            KeyModifiers::NONE,
            KeyEventKind::Repeat,
        )));
        assert!(!actionable_menu_key_event(KeyEvent::new_with_kind(
            KeyCode::Char('c'),
            KeyModifiers::CONTROL,
            KeyEventKind::Repeat,
        )));
    }

    #[test]
    fn unchanged_menu_frame_emits_no_terminal_output() {
        let lines = vec![
            "Title".to_owned(),
            "> first".to_owned(),
            "  second".to_owned(),
        ];
        let mut initial = Vec::new();
        let rows = replace_menu_at_width(&mut initial, &[], &lines, 80).unwrap();
        assert!(!initial.is_empty());

        let mut unchanged = Vec::new();
        let repeated = replace_menu_at_width(&mut unchanged, &rows, &lines, 80).unwrap();
        assert_eq!(repeated, rows);
        assert!(unchanged.is_empty());
    }

    #[test]
    fn menu_selection_only_rewrites_the_two_changed_rows() {
        let old = vec![
            "Title".to_owned(),
            "> first".to_owned(),
            "  second".to_owned(),
        ];
        let new = vec![
            "Title".to_owned(),
            "  first".to_owned(),
            "> second".to_owned(),
        ];
        let previous = menu_display_rows(&old, 80);
        let mut output = Vec::new();
        let rows = replace_menu_at_width(&mut output, &previous, &new, 80).unwrap();
        assert_eq!(rows, menu_display_rows(&new, 80));
        let output = String::from_utf8(output).unwrap();
        assert!(!output.contains("\x1b[2K"));
        assert!(!output.contains("Title"));
        assert!(output.contains("  first"));
        assert!(output.contains("> second"));
        assert!(output.contains("\x1b[?2026h"));
        assert!(output.contains("\x1b[?2026l"));
        assert!(output.contains("\x1b[?7l"));
        assert!(output.contains("\x1b[?7h"));
        assert!(output.contains("\r\n"));
        assert!(!output.contains("\x1b[1E"));
    }

    #[test]
    fn menu_wraps_to_explicit_physical_rows() {
        assert_eq!(
            wrap_menu_line("ab界cd", 4),
            vec!["ab界".to_owned(), "cd".to_owned()]
        );
    }

    #[test]
    fn menu_resize_reflows_each_previous_hard_row_at_the_new_width() {
        let previous = MenuFrame {
            terminal_width: 100,
            rows: vec!["a".repeat(70), "b".repeat(45)],
        };
        let reflowed = reflowed_previous_rows(&previous, 40);
        assert_eq!(
            reflowed.iter().map(String::len).collect::<Vec<_>>(),
            [40, 30, 40, 5]
        );
        assert!(reflowed.iter().all(|row| row.len() <= 40));
    }

    #[test]
    fn menu_rows_never_emit_literal_control_characters_from_labels() {
        let rows = wrap_menu_line("unsafe\x1b[2J\nname", 80);
        assert_eq!(rows, ["unsafe␛[2J␊name"]);
        assert!(!rows[0].chars().any(char::is_control));
    }
}
