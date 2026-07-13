use std::fmt::Write as _;

use crate::model::{ReviewComment, ReviewFile, ReviewLine};
use crate::state::ReviewState;
use crate::syntax::fence_language;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OutputFormat {
    Markdown,
    Xml,
}

impl OutputFormat {
    #[must_use]
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "md" => Some(Self::Markdown),
            "xml" => Some(Self::Xml),
            _ => None,
        }
    }
}

#[must_use]
pub fn format_review(state: &ReviewState, output_format: OutputFormat) -> String {
    if state.comments.is_empty() {
        return "No review comments.\n".to_owned();
    }
    let output = match output_format {
        OutputFormat::Markdown => format_markdown(state),
        OutputFormat::Xml => format_xml(state),
    };
    sanitize_formatted_output(&output)
}

fn sanitize_formatted_output(output: &str) -> String {
    output
        .chars()
        .map(|character| match character {
            '\n' | '\t' => character,
            '\u{0}'..='\u{1f}' => char::from_u32(0x2400 + u32::from(character)).unwrap_or('�'),
            '\u{7f}' => '␡',
            value if value.is_control() => '�',
            value => value,
        })
        .collect()
}

fn format_markdown(state: &ReviewState) -> String {
    let mut output = format!(
        "# Review comments for {}\n## Source: {}\n\n",
        state.repository_root.display(),
        state.source.label()
    );
    for file in &state.files {
        let comments = state.comments_for_file(&file.path);
        if comments.is_empty() {
            continue;
        }
        let _ = writeln!(output, "### File: {}\n", file.display_path());
        for comment in comments {
            let reference = line_reference(&comment.selected_lines);
            let _ = writeln!(output, "{}", reference.label);
            let context = comment_context_lines(file, comment, 2)
                .into_iter()
                .map(format_context_line)
                .collect::<Vec<_>>();
            append_fenced_block(&mut output, &context, fence_language(&file.language), '`');
            output.push_str("Comment:\n");
            let body = if comment.body.is_empty() {
                vec![String::new()]
            } else {
                comment.body.lines().map(ToOwned::to_owned).collect()
            };
            append_fenced_block(&mut output, &body, "text", '~');
            output.push('\n');
        }
    }
    while output.ends_with("\n\n") {
        output.pop();
    }
    if !output.ends_with('\n') {
        output.push('\n');
    }
    output
}

fn format_xml(state: &ReviewState) -> String {
    let mut output = String::from(
        "<review_feedback>\n  <instructions>Use these review comments as feedback on the referenced code changes. For each review_comment, inspect the context and address the message.</instructions>\n  <metadata>\n",
    );
    let _ = writeln!(
        output,
        "    <repository path=\"{}\" />",
        xml_attribute(&state.repository_root.to_string_lossy())
    );
    let mut source_attributes = format!(
        "kind=\"{}\" base_ref=\"{}\"",
        state.source.kind_name(),
        xml_attribute(&state.source.base_ref)
    );
    if let Some(target) = &state.source.target_branch {
        let _ = write!(
            source_attributes,
            " target_branch=\"{}\"",
            xml_attribute(target)
        );
    }
    let _ = writeln!(
        output,
        "    <source {source_attributes}>{}</source>",
        xml_text(&state.source.label())
    );
    output.push_str("  </metadata>\n  <review_comments>\n");
    for file in &state.files {
        let comments = state.comments_for_file(&file.path);
        if comments.is_empty() {
            continue;
        }
        let _ = write!(
            output,
            "    <file path=\"{}\" display_path=\"{}\"",
            xml_attribute(&file.path),
            xml_attribute(&file.display_path())
        );
        if let Some(old_path) = &file.old_path {
            let _ = write!(output, " old_path=\"{}\"", xml_attribute(old_path));
        }
        output.push_str(">\n");
        for comment in comments {
            let reference = line_reference(&comment.selected_lines);
            let _ = writeln!(output, "      <review_comment id=\"c{}\">", comment.id);
            output.push_str("        <location>\n");
            let _ = writeln!(
                output,
                "          <line_range {}>{}</line_range>",
                reference.attributes,
                xml_text(&reference.label)
            );
            output.push_str("        </location>\n");
            let context = comment_context_lines(file, comment, 2)
                .into_iter()
                .map(format_context_line)
                .collect::<Vec<_>>()
                .join("\n");
            let _ = writeln!(
                output,
                "        <context radius=\"2\"><![CDATA[{}]]></context>",
                cdata_text(&context)
            );
            let _ = writeln!(
                output,
                "        <message>{}</message>",
                xml_text(&comment.body)
            );
            output.push_str("      </review_comment>\n");
        }
        output.push_str("    </file>\n");
    }
    output.push_str("  </review_comments>\n</review_feedback>\n");
    output
}

struct LineReference {
    label: String,
    attributes: String,
}

fn line_reference(lines: &[ReviewLine]) -> LineReference {
    let new_numbers = lines
        .iter()
        .filter_map(|line| line.new_line)
        .collect::<Vec<_>>();
    let old_numbers = lines
        .iter()
        .filter_map(|line| line.old_line)
        .collect::<Vec<_>>();
    let has_old_only = lines
        .iter()
        .any(|line| line.new_line.is_none() && line.old_line.is_some());
    if !new_numbers.is_empty() && !has_old_only {
        let (start, end) = min_max(&new_numbers);
        return LineReference {
            label: range_label("Line", "Lines", start, end),
            attributes: format!("side=\"new\" start=\"{start}\" end=\"{end}\""),
        };
    }
    if !old_numbers.is_empty() && new_numbers.is_empty() {
        let (start, end) = min_max(&old_numbers);
        return LineReference {
            label: range_label("Old line", "Old lines", start, end),
            attributes: format!("side=\"old\" start=\"{start}\" end=\"{end}\""),
        };
    }
    if !old_numbers.is_empty() && !new_numbers.is_empty() {
        let (old_start, old_end) = min_max(&old_numbers);
        let (new_start, new_end) = min_max(&new_numbers);
        return LineReference {
            label: format!(
                "{}; {}",
                range_label("Old line", "Old lines", old_start, old_end),
                range_label("New line", "New lines", new_start, new_end)
            ),
            attributes: format!(
                "side=\"mixed\" old_start=\"{old_start}\" old_end=\"{old_end}\" new_start=\"{new_start}\" new_end=\"{new_end}\""
            ),
        };
    }
    LineReference {
        label: "Lines: unavailable".to_owned(),
        attributes: "side=\"unknown\"".to_owned(),
    }
}

fn min_max(numbers: &[usize]) -> (usize, usize) {
    let start = numbers.iter().copied().min().unwrap_or_default();
    let end = numbers.iter().copied().max().unwrap_or_default();
    (start, end)
}

fn range_label(single: &str, plural: &str, start: usize, end: usize) -> String {
    if start == end {
        format!("{single}: {start}")
    } else {
        format!("{plural}: {start}-{end}")
    }
}

fn comment_context_lines<'a>(
    file: &'a ReviewFile,
    comment: &ReviewComment,
    radius: usize,
) -> Vec<&'a ReviewLine> {
    if file.lines.is_empty() {
        return Vec::new();
    }
    let (start, end) = comment.sorted_rows();
    file.lines[start.saturating_sub(radius)..=(end + radius).min(file.lines.len() - 1)]
        .iter()
        .collect()
}

fn format_context_line(line: &ReviewLine) -> String {
    let number = line
        .primary_line()
        .map_or_else(|| "?".to_owned(), |number| number.to_string());
    format!("{number:>4} {} {}", line.marker(), line.text)
}

fn append_fenced_block(output: &mut String, lines: &[String], info: &str, fence_char: char) {
    let fence = fence_for(lines, fence_char);
    let _ = writeln!(output, "{fence}{info}");
    for line in lines {
        let _ = writeln!(output, "{line}");
    }
    let _ = writeln!(output, "{fence}");
}

fn fence_for(lines: &[String], fence_char: char) -> String {
    let longest = lines
        .iter()
        .map(|line| {
            line.chars()
                .fold((0, 0), |(longest, current), character| {
                    if character == fence_char {
                        (longest.max(current + 1), current + 1)
                    } else {
                        (longest, 0)
                    }
                })
                .0
        })
        .max()
        .unwrap_or(0)
        .max(2);
    std::iter::repeat_n(fence_char, longest + 1).collect()
}

fn xml_text(value: &str) -> String {
    xml_safe(value)
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

fn xml_attribute(value: &str) -> String {
    xml_text(value).replace('"', "&quot;")
}

fn cdata_text(value: &str) -> String {
    xml_safe(value).replace("]]>", "]]]]><![CDATA[>")
}

fn xml_safe(value: &str) -> String {
    value
        .chars()
        .map(|character| {
            let code = u32::from(character);
            if matches!(code, 0x09 | 0x0a | 0x0d)
                || (0x20..=0xd7ff).contains(&code)
                || (0xe000..=0xfffd).contains(&code)
                || (0x1_0000..=0x0010_ffff).contains(&code)
            {
                character
            } else {
                '\u{fffd}'
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    use crate::model::{FileStatus, ReviewKind, ReviewSource, create_review_file};

    fn commented_state(body: &str) -> ReviewState {
        let old = vec!["before".to_owned(), "old".to_owned(), "after".to_owned()];
        let new = vec!["before".to_owned(), "new```".to_owned(), "after".to_owned()];
        let file = create_review_file(
            "a.py".into(),
            FileStatus::Modified,
            &old,
            &new,
            None,
            false,
            vec![],
        );
        let mut state = ReviewState::new(
            Path::new("/repo"),
            ReviewSource {
                kind: ReviewKind::Uncommitted,
                target_branch: None,
                base_ref: "HEAD".into(),
            },
            vec![file],
        );
        state.move_selection(2);
        state.add_comment(body).unwrap();
        state
    }

    #[test]
    fn markdown_uses_safe_dynamic_fences() {
        let output = format_review(&commented_state("body~~~text"), OutputFormat::Markdown);
        assert!(output.contains("````python"));
        assert!(output.contains("~~~~text"));
    }

    #[test]
    fn xml_escapes_message_and_splits_cdata_terminator() {
        let output = format_review(&commented_state("a < b & ]]>"), OutputFormat::Xml);
        assert!(output.contains("a &lt; b &amp; ]]&gt;"));
        assert_eq!(cdata_text("]]>"), "]]]]><![CDATA[>");
        assert!(output.starts_with("<review_feedback>"));
    }

    #[test]
    fn formatted_reviews_never_emit_terminal_control_sequences() {
        for format in [OutputFormat::Markdown, OutputFormat::Xml] {
            let output = format_review(&commented_state("unsafe\x1b[2J\u{7f}"), format);
            match format {
                OutputFormat::Markdown => assert!(output.contains("unsafe␛[2J␡")),
                OutputFormat::Xml => assert!(output.contains("unsafe�[2J␡")),
            }
            assert!(!output.contains('\x1b'));
            assert!(
                !output
                    .chars()
                    .any(|character| character.is_control() && !matches!(character, '\n' | '\t'))
            );
        }
    }
}
