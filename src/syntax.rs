use arborium::AnsiHighlighter;
use arborium::theme::{Color as ThemeColor, Style as ThemeStyle, Theme};
use arborium_theme::{ThemeSlot, slot_to_highlight_index};

use crate::model::ReviewFile;

// Arborium emits true-color escapes. These distinct sentinel values retain each semantic role
// until the TUI maps it to the terminal's configurable base ANSI palette.
pub(crate) const DEFAULT_FOREGROUND: (u8, u8, u8) = (0, 0, 0);
pub(crate) const KEYWORD_FOREGROUND: (u8, u8, u8) = (1, 0, 0);
pub(crate) const FUNCTION_FOREGROUND: (u8, u8, u8) = (2, 0, 0);
pub(crate) const STRING_FOREGROUND: (u8, u8, u8) = (3, 0, 0);
pub(crate) const COMMENT_FOREGROUND: (u8, u8, u8) = (4, 0, 0);
pub(crate) const TYPE_FOREGROUND: (u8, u8, u8) = (5, 0, 0);
pub(crate) const NUMBER_FOREGROUND: (u8, u8, u8) = (6, 0, 0);
pub(crate) const OPERATOR_FOREGROUND: (u8, u8, u8) = (7, 0, 0);
pub(crate) const PUNCTUATION_FOREGROUND: (u8, u8, u8) = (8, 0, 0);
pub(crate) const TAG_FOREGROUND: (u8, u8, u8) = (9, 0, 0);
pub(crate) const ATTRIBUTE_FOREGROUND: (u8, u8, u8) = (10, 0, 0);
pub(crate) const HEADING_FOREGROUND: (u8, u8, u8) = (11, 0, 0);
pub(crate) const EMPHASIS_FOREGROUND: (u8, u8, u8) = (12, 0, 0);
pub(crate) const ERROR_FOREGROUND: (u8, u8, u8) = (13, 0, 0);

#[must_use]
pub fn language_for_path(path: &str) -> &'static str {
    let file_name = path.rsplit('/').next().unwrap_or(path).to_ascii_lowercase();
    if matches!(
        file_name.as_str(),
        ".gitignore" | ".ignore" | ".dockerignore"
    ) {
        return "gitignore";
    }
    if file_name == "cargo.lock" {
        return "toml";
    }
    if file_name
        .rsplit_once('.')
        .is_some_and(|(_, extension)| extension == "lock")
    {
        return "json";
    }
    if file_name == "makefile" {
        return "makefile";
    }
    if file_name == "dockerfile" {
        return "dockerfile";
    }
    let extension = file_name.rsplit_once('.').map(|(_, extension)| extension);
    match extension {
        Some("py" | "pyi") => "python",
        Some("rs") => "rust",
        Some("toml") => "toml",
        Some("java") => "java",
        Some("js" | "mjs" | "cjs") => "javascript",
        Some("jsx") => "jsx",
        Some("ts") => "typescript",
        Some("tsx") => "tsx",
        Some("css") => "css",
        Some("html" | "htm") => "html",
        Some("sql") => "sql",
        Some("xml") => "xml",
        Some("json") => "json",
        Some("properties") => "properties",
        Some("yml" | "yaml") => "yaml",
        Some("md" | "markdown") => "markdown",
        Some("nix") => "nix",
        Some("tf" | "tfvars" | "hcl") => "hcl",
        _ => "text",
    }
}

#[must_use]
pub fn fence_language(language: &str) -> &str {
    match language {
        "javascript" => "js",
        "typescript" => "ts",
        "python" | "rust" | "toml" | "java" | "jsx" | "tsx" | "css" | "html" | "sql" | "xml"
        | "hcl" | "json" | "properties" | "yaml" | "markdown" | "nix" | "gitignore" => language,
        _ => "text",
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HighlightedSpan {
    pub text: String,
    pub foreground: (u8, u8, u8),
    pub bold: bool,
    pub italic: bool,
}

pub struct SyntaxHighlighter {
    highlighter: AnsiHighlighter,
}

impl Default for SyntaxHighlighter {
    fn default() -> Self {
        Self {
            highlighter: AnsiHighlighter::new(previous_light_theme()),
        }
    }
}

fn previous_light_theme() -> Theme {
    let mut theme = Theme::new("review light terminal palette");
    theme.is_dark = false;
    for (slot, foreground, bold) in [
        (ThemeSlot::Keyword, KEYWORD_FOREGROUND, true),
        (ThemeSlot::Function, FUNCTION_FOREGROUND, false),
        (ThemeSlot::String, STRING_FOREGROUND, false),
        (ThemeSlot::Comment, COMMENT_FOREGROUND, false),
        (ThemeSlot::Type, TYPE_FOREGROUND, false),
        (ThemeSlot::Variable, DEFAULT_FOREGROUND, false),
        (ThemeSlot::Constant, KEYWORD_FOREGROUND, true),
        (ThemeSlot::Number, NUMBER_FOREGROUND, false),
        (ThemeSlot::Operator, OPERATOR_FOREGROUND, false),
        (ThemeSlot::Punctuation, PUNCTUATION_FOREGROUND, false),
        (ThemeSlot::Property, ATTRIBUTE_FOREGROUND, false),
        (ThemeSlot::Attribute, ATTRIBUTE_FOREGROUND, false),
        (ThemeSlot::Tag, TAG_FOREGROUND, true),
        (ThemeSlot::Macro, FUNCTION_FOREGROUND, false),
        (ThemeSlot::Label, DEFAULT_FOREGROUND, false),
        (ThemeSlot::Namespace, TYPE_FOREGROUND, false),
        (ThemeSlot::Constructor, TYPE_FOREGROUND, false),
        (ThemeSlot::Title, HEADING_FOREGROUND, true),
        (ThemeSlot::Strong, EMPHASIS_FOREGROUND, true),
        (ThemeSlot::Emphasis, EMPHASIS_FOREGROUND, true),
        (ThemeSlot::Link, STRING_FOREGROUND, false),
        (ThemeSlot::Literal, STRING_FOREGROUND, false),
        (ThemeSlot::Strikethrough, DEFAULT_FOREGROUND, false),
        (ThemeSlot::DiffAdd, DEFAULT_FOREGROUND, false),
        (ThemeSlot::DiffDelete, DEFAULT_FOREGROUND, false),
        (ThemeSlot::Embedded, DEFAULT_FOREGROUND, false),
        (ThemeSlot::Error, ERROR_FOREGROUND, true),
    ] {
        let Some(index) = slot_to_highlight_index(slot) else {
            continue;
        };
        let mut style =
            ThemeStyle::new().fg(ThemeColor::new(foreground.0, foreground.1, foreground.2));
        if bold {
            style = style.bold();
        }
        theme.set_style(index, style);
    }
    theme
}

impl SyntaxHighlighter {
    #[must_use]
    pub fn highlight_line(
        &mut self,
        _path: &str,
        language: &str,
        line: &str,
    ) -> Vec<HighlightedSpan> {
        if language == "gitignore" {
            return highlight_gitignore(line);
        }
        let Some(language) = arborium_language(language) else {
            return plain_span(line);
        };
        self.highlighter
            .highlight(language, line)
            .map_or_else(|_| plain_span(line), |ansi| spans_from_ansi(&ansi, line))
    }

    #[must_use]
    pub fn highlight_file(&mut self, file: &ReviewFile) -> Vec<Vec<HighlightedSpan>> {
        file.lines
            .iter()
            .map(|line| self.highlight_line(&file.path, &file.language, &line.text))
            .collect()
    }
}

fn arborium_language(language: &str) -> Option<&'static str> {
    match language {
        "python" => Some("python"),
        "rust" => Some("rust"),
        "toml" => Some("toml"),
        "java" => Some("java"),
        "javascript" | "jsx" => Some("javascript"),
        "typescript" => Some("typescript"),
        "tsx" => Some("tsx"),
        "css" => Some("css"),
        "html" => Some("html"),
        "sql" => Some("sql"),
        "xml" => Some("xml"),
        "json" => Some("json"),
        "yaml" => Some("yaml"),
        "markdown" => Some("markdown"),
        "nix" => Some("nix"),
        "hcl" => Some("hcl"),
        "makefile" => Some("makefile"),
        "dockerfile" => Some("dockerfile"),
        "properties" => Some("ini"),
        _ => None,
    }
}

fn highlight_gitignore(line: &str) -> Vec<HighlightedSpan> {
    if line.is_empty() {
        return Vec::new();
    }
    let mut spans = Vec::new();
    let content = line.trim_start();
    let leading_length = line.len() - content.len();
    push_colored_span(
        &mut spans,
        &line[..leading_length],
        DEFAULT_FOREGROUND,
        false,
    );
    if content.starts_with('#') {
        push_colored_span(&mut spans, content, COMMENT_FOREGROUND, false);
        return spans;
    }
    for (index, (start, character)) in content.char_indices().enumerate() {
        let end = start + character.len_utf8();
        let operator = matches!(character, '*' | '?' | '[' | ']')
            || index == 0 && matches!(character, '!' | '/')
            || character == '/' && end == content.len();
        push_colored_span(
            &mut spans,
            &content[start..end],
            if operator {
                OPERATOR_FOREGROUND
            } else {
                STRING_FOREGROUND
            },
            false,
        );
    }
    spans
}

fn push_colored_span(
    spans: &mut Vec<HighlightedSpan>,
    text: &str,
    foreground: (u8, u8, u8),
    bold: bool,
) {
    if text.is_empty() {
        return;
    }
    match spans.last_mut() {
        Some(last) if last.foreground == foreground && last.bold == bold && !last.italic => {
            last.text.push_str(text);
        }
        _ => spans.push(HighlightedSpan {
            text: text.to_owned(),
            foreground,
            bold,
            italic: false,
        }),
    }
}

fn spans_from_ansi(ansi: &str, fallback: &str) -> Vec<HighlightedSpan> {
    let mut spans = Vec::new();
    let mut style = SpanStyle::default();
    let mut offset = 0;
    while let Some(relative) = ansi[offset..].find("\x1b[") {
        let escape_start = offset + relative;
        push_span(&mut spans, &ansi[offset..escape_start], style);
        let parameters_start = escape_start + 2;
        let Some(relative_end) = ansi[parameters_start..].find('m') else {
            push_span(&mut spans, &ansi[escape_start..], style);
            offset = ansi.len();
            break;
        };
        let escape_end = parameters_start + relative_end;
        apply_sgr(&mut style, &ansi[parameters_start..escape_end]);
        offset = escape_end + 1;
    }
    if offset < ansi.len() {
        push_span(&mut spans, &ansi[offset..], style);
    }
    if spans.is_empty() && !fallback.is_empty() {
        return plain_span(fallback);
    }
    spans
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct SpanStyle {
    foreground: (u8, u8, u8),
    bold: bool,
    italic: bool,
}

impl Default for SpanStyle {
    fn default() -> Self {
        Self {
            foreground: DEFAULT_FOREGROUND,
            bold: false,
            italic: false,
        }
    }
}

fn apply_sgr(style: &mut SpanStyle, parameters: &str) {
    let values = parameters
        .split(';')
        .filter_map(|value| value.parse::<u16>().ok())
        .collect::<Vec<_>>();
    let mut index = 0;
    while index < values.len() {
        match values[index] {
            0 => *style = SpanStyle::default(),
            1 => style.bold = true,
            3 => style.italic = true,
            22 => style.bold = false,
            23 => style.italic = false,
            38 if values.get(index + 1) == Some(&2) && index + 4 < values.len() => {
                style.foreground = (
                    u8::try_from(values[index + 2]).unwrap_or(u8::MAX),
                    u8::try_from(values[index + 3]).unwrap_or(u8::MAX),
                    u8::try_from(values[index + 4]).unwrap_or(u8::MAX),
                );
                index += 4;
            }
            39 => style.foreground = DEFAULT_FOREGROUND,
            _ => {}
        }
        index += 1;
    }
}

fn push_span(spans: &mut Vec<HighlightedSpan>, text: &str, style: SpanStyle) {
    if text.is_empty() {
        return;
    }
    match spans.last_mut() {
        Some(last)
            if last.foreground == style.foreground
                && last.bold == style.bold
                && last.italic == style.italic =>
        {
            last.text.push_str(text);
        }
        _ => spans.push(HighlightedSpan {
            text: text.to_owned(),
            foreground: style.foreground,
            bold: style.bold,
            italic: style.italic,
        }),
    }
}

fn plain_span(text: &str) -> Vec<HighlightedSpan> {
    if text.is_empty() {
        Vec::new()
    } else {
        vec![HighlightedSpan {
            text: text.to_owned(),
            foreground: DEFAULT_FOREGROUND,
            bold: false,
            italic: false,
        }]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_required_languages() {
        let cases = [
            ("a.py", "python"),
            ("a.rs", "rust"),
            ("Cargo.toml", "toml"),
            ("Cargo.lock", "toml"),
            ("a.java", "java"),
            ("a.js", "javascript"),
            ("a.ts", "typescript"),
            ("a.tsx", "tsx"),
            ("a.css", "css"),
            ("a.html", "html"),
            ("a.jsx", "jsx"),
            ("a.sql", "sql"),
            ("a.xml", "xml"),
            ("a.json", "json"),
            ("a.properties", "properties"),
            ("a.yaml", "yaml"),
            ("a.md", "markdown"),
            ("a.nix", "nix"),
            ("main.tf", "hcl"),
            ("prod.auto.tfvars", "hcl"),
            (".terraform.lock.hcl", "hcl"),
            ("main.tf.json", "json"),
            ("flake.lock", "json"),
            (".gitignore", "gitignore"),
            ("unknown.zzz", "text"),
        ];
        for (path, expected) in cases {
            assert_eq!(language_for_path(path), expected, "{path}");
        }
    }

    #[test]
    fn selected_grammars_cover_representative_language_files() {
        for language in [
            "python",
            "rust",
            "toml",
            "java",
            "javascript",
            "typescript",
            "tsx",
            "css",
            "html",
            "jsx",
            "sql",
            "xml",
            "json",
            "properties",
            "yaml",
            "markdown",
            "nix",
            "gitignore",
        ] {
            assert!(
                language == "gitignore" || arborium_language(language).is_some(),
                "{language}"
            );
        }
    }

    #[test]
    fn representative_source_lines_receive_syntax_styles() {
        let mut highlighter = SyntaxHighlighter::default();
        for (path, language, source) in [
            ("a.py", "python", "def greet(name: str) -> str:"),
            ("a.rs", "rust", "pub fn answer() -> u32 { 42 }"),
            ("Cargo.toml", "toml", "edition = \"2024\""),
            ("Cargo.lock", "toml", "version = 4"),
            ("a.java", "java", "public record User(String name) {}"),
            ("a.js", "javascript", "export const answer = () => 42;"),
            ("a.ts", "typescript", "const answer: number = 42;"),
            ("a.tsx", "tsx", "const view = <main>Hello</main>;"),
            ("a.css", "css", "body { color: rebeccapurple; }"),
            ("a.html", "html", "<strong class=\"title\">Hello</strong>"),
            ("a.jsx", "jsx", "const view = <h1>{name}</h1>;"),
            (
                "a.sql",
                "sql",
                "SELECT name FROM users WHERE active = true;",
            ),
            ("a.xml", "xml", "<user id=\"1\">Ada</user>"),
            ("a.json", "json", "{\"enabled\": true, \"count\": 3}"),
            ("a.properties", "properties", "feature.enabled=true"),
            ("a.yaml", "yaml", "enabled: true"),
            ("a.md", "markdown", "# Heading with **bold**"),
            ("a.nix", "nix", "{ pkgs }: pkgs.mkShell { }"),
            (
                "main.tf",
                "hcl",
                r#"resource "aws_instance" "web" { count = 2 }"#,
            ),
            ("package-lock.json", "json", "{\"lockfileVersion\": 3}"),
            (".gitignore", "gitignore", "!important.log"),
        ] {
            let styled = highlighter.highlight_line(path, language, source);
            assert_eq!(
                styled
                    .iter()
                    .map(|span| span.text.as_str())
                    .collect::<String>(),
                source,
                "highlighting changed content for {path}"
            );
            assert!(
                styled.iter().any(|span| {
                    span.foreground != DEFAULT_FOREGROUND || span.bold || span.italic
                }),
                "source received no distinct syntax style for {path}"
            );
        }
    }

    #[test]
    fn ansi_parser_preserves_utf8_and_style_transitions() {
        let spans = spans_from_ansi("plain \x1b[1;3;38;2;1;2;3mżółć\x1b[0m end", "");
        assert_eq!(
            spans
                .iter()
                .map(|span| span.text.as_str())
                .collect::<String>(),
            "plain żółć end"
        );
        assert_eq!(spans[1].foreground, (1, 2, 3));
        assert!(spans[1].bold && spans[1].italic);
    }

    #[test]
    fn syntax_theme_matches_previous_light_terminal_roles() {
        let theme = previous_light_theme();
        assert!(!theme.is_dark);
        assert_eq!(theme.background, None);
        assert_eq!(theme.foreground, None);
        for (slot, foreground, bold) in [
            (ThemeSlot::Keyword, KEYWORD_FOREGROUND, true),
            (ThemeSlot::Function, FUNCTION_FOREGROUND, false),
            (ThemeSlot::String, STRING_FOREGROUND, false),
            (ThemeSlot::Comment, COMMENT_FOREGROUND, false),
            (ThemeSlot::Type, TYPE_FOREGROUND, false),
            (ThemeSlot::Number, NUMBER_FOREGROUND, false),
            (ThemeSlot::Operator, OPERATOR_FOREGROUND, false),
            (ThemeSlot::Punctuation, PUNCTUATION_FOREGROUND, false),
            (ThemeSlot::Attribute, ATTRIBUTE_FOREGROUND, false),
            (ThemeSlot::Tag, TAG_FOREGROUND, true),
            (ThemeSlot::Title, HEADING_FOREGROUND, true),
            (ThemeSlot::Emphasis, EMPHASIS_FOREGROUND, true),
            (ThemeSlot::Error, ERROR_FOREGROUND, true),
        ] {
            let index = slot_to_highlight_index(slot).expect("theme slot has an index");
            let style = theme.style(index).expect("theme slot has a style");
            assert_eq!(
                style.fg,
                Some(ThemeColor::new(foreground.0, foreground.1, foreground.2)),
                "{slot:?}"
            );
            assert_eq!(style.modifiers.bold, bold, "{slot:?}");
            assert!(!style.modifiers.italic, "{slot:?}");
        }
    }

    #[test]
    fn arborium_output_keeps_previous_python_role_assignments() {
        let mut highlighter = SyntaxHighlighter::default();
        let spans = highlighter.highlight_line(
            "sample.py",
            "python",
            "def greet(name: str) -> str: return \"hello\"",
        );
        for (foreground, bold) in [
            (KEYWORD_FOREGROUND, true),
            (FUNCTION_FOREGROUND, false),
            (TYPE_FOREGROUND, false),
            (STRING_FOREGROUND, false),
        ] {
            assert!(
                spans
                    .iter()
                    .any(|span| span.foreground == foreground && span.bold == bold),
                "missing syntax role {foreground:?}"
            );
        }
        assert!(spans.iter().all(|span| !span.italic));
    }

    #[test]
    fn gitignore_highlighting_uses_previous_roles_without_italics() {
        let spans = highlight_gitignore("  !build/*/");
        assert_eq!(
            spans
                .iter()
                .map(|span| span.text.as_str())
                .collect::<String>(),
            "  !build/*/"
        );
        assert_eq!(spans[0].foreground, DEFAULT_FOREGROUND);
        assert_eq!(spans[1].foreground, OPERATOR_FOREGROUND);
        assert_eq!(spans[2].foreground, STRING_FOREGROUND);
        assert_eq!(spans[3].foreground, OPERATOR_FOREGROUND);
        assert!(spans.iter().all(|span| !span.italic));

        let comment = highlight_gitignore("  # generated");
        assert_eq!(comment[1].foreground, COMMENT_FOREGROUND);
        assert!(!comment[1].italic);
    }
}
