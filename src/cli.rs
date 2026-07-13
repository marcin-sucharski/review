use std::ffi::OsString;
use std::io::{self, IsTerminal, Write};
use std::path::Path;
use std::time::SystemTime;

use crate::VERSION;
use crate::archive::{ArchivedReview, archive_review, list_archived_reviews, save_review_file};
use crate::error::{Result, ReviewError};
use crate::format::{OutputFormat, format_review};
use crate::git::{
    collect_branch_comparison, collect_uncommitted, current_branch, default_branch_candidates,
    repository_root,
};
use crate::menu::{MenuOption, select_branch_target, select_option, select_option_to};
use crate::state::ReviewState;
use crate::tmux::{list_panes, send_text};
use crate::tui::ReviewApp;

const HELP: &str = concat!(
    "Usage: review [OPTIONS]\n",
    "       review <COMMAND>\n\n",
    "Review Git changes in a terminal UI.\n\n",
    "Options:\n",
    "  --source <uncommitted|branch>  Review source; prompts when omitted\n",
    "  --target <BRANCH>              Target branch for --source branch\n",
    "  -o, --output-format <md|xml>   Review message format [default: md]\n",
    "  --stdout                       Print comments without a delivery prompt\n",
    "  -h, --help                     Print help\n",
    "  -V, --version                  Print version\n\n",
    "Commands:\n",
    "  ls                             List up to 10 recent saved reviews\n",
    "  display                        Select and print a saved review\n",
);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SourceArgument {
    Uncommitted,
    Branch,
}

#[derive(Debug)]
struct Arguments {
    source: Option<SourceArgument>,
    target: Option<String>,
    output_format: OutputFormat,
    stdout: bool,
    no_tui: bool,
}

impl Default for Arguments {
    fn default() -> Self {
        Self {
            source: None,
            target: None,
            output_format: OutputFormat::Markdown,
            stdout: false,
            no_tui: false,
        }
    }
}

pub fn run<I>(args: I) -> i32
where
    I: IntoIterator<Item = OsString>,
{
    let args = args
        .into_iter()
        .map(|argument| argument.to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    match run_inner(&args) {
        Ok(code) => code,
        Err(ReviewError::Cancelled) => {
            eprintln!("review cancelled");
            130
        }
        Err(ReviewError::NoChanges(message)) => {
            println!("review: {message}");
            0
        }
        Err(error) => {
            eprintln!("review: {error}");
            1
        }
    }
}

fn run_inner(args: &[String]) -> Result<i32> {
    if matches!(args.first().map(String::as_str), Some("ls" | "display")) {
        return run_history(args);
    }
    if args
        .iter()
        .any(|argument| matches!(argument.as_str(), "-h" | "--help"))
    {
        print!("{HELP}");
        return Ok(0);
    }
    if args
        .iter()
        .any(|argument| matches!(argument.as_str(), "-V" | "--version"))
    {
        println!("review {VERSION}");
        return Ok(0);
    }
    let arguments = parse_arguments(args)?;
    let current_directory = std::env::current_dir()
        .map_err(|error| ReviewError::io("could not determine current directory", error))?;
    let root = repository_root(&current_directory)?;
    let source = match arguments.source {
        Some(value) => value,
        None => prompt_source()?,
    };
    let (review_source, files) = match source {
        SourceArgument::Uncommitted => collect_uncommitted(&root)?,
        SourceArgument::Branch => {
            let target = match arguments.target {
                Some(target) => target,
                None => prompt_branch(&root)?,
            };
            collect_branch_comparison(&root, &target)?
        }
    };
    let mut state = ReviewState::new(&root, review_source, files);
    if !arguments.no_tui {
        ReviewApp::new(&mut state).run()?;
        reset_terminal_after_tui();
    }
    let message = format_review(&state, arguments.output_format);
    if !state.comments.is_empty() {
        match archive_review(&state, &message) {
            Ok(_) => {}
            Err(error) => eprintln!("review: could not archive review: {error}"),
        }
    }
    if arguments.stdout || state.comments.is_empty() {
        print!("{message}");
        io::stdout()
            .flush()
            .map_err(|error| ReviewError::io("could not write review output", error))?;
        return Ok(0);
    }
    let markdown = if arguments.output_format == OutputFormat::Markdown {
        message.clone()
    } else {
        format_review(&state, OutputFormat::Markdown)
    };
    deliver_review(&message, &markdown, &current_directory)
}

fn parse_arguments(args: &[String]) -> Result<Arguments> {
    let mut parsed = Arguments::default();
    let mut index = 0;
    while index < args.len() {
        let argument = &args[index];
        match argument.as_str() {
            "--stdout" => parsed.stdout = true,
            "--no-tui" => parsed.no_tui = true,
            "--source" => {
                index += 1;
                parsed.source = Some(parse_source(value_at(args, index, "--source")?)?);
            }
            "--target" => {
                index += 1;
                parsed.target = Some(value_at(args, index, "--target")?.to_owned());
            }
            "-o" | "--output-format" => {
                index += 1;
                parsed.output_format = parse_output(value_at(args, index, argument)?)?;
            }
            value if value.starts_with("--source=") => {
                parsed.source = Some(parse_source(&value[9..])?);
            }
            value if value.starts_with("--target=") => {
                parsed.target = Some(value[9..].to_owned());
            }
            value if value.starts_with("--output-format=") => {
                parsed.output_format = parse_output(&value[16..])?;
            }
            value => {
                return Err(ReviewError::InvalidArgument(format!(
                    "unknown argument: {value}\n\n{HELP}"
                )));
            }
        }
        index += 1;
    }
    if parsed.target.is_some() && parsed.source == Some(SourceArgument::Uncommitted) {
        return Err(ReviewError::InvalidArgument(
            "--target can only be used with --source branch".to_owned(),
        ));
    }
    Ok(parsed)
}

fn value_at<'a>(args: &'a [String], index: usize, option: &str) -> Result<&'a str> {
    args.get(index)
        .map(String::as_str)
        .ok_or_else(|| ReviewError::InvalidArgument(format!("{option} requires a value")))
}

fn parse_source(value: &str) -> Result<SourceArgument> {
    match value {
        "uncommitted" => Ok(SourceArgument::Uncommitted),
        "branch" => Ok(SourceArgument::Branch),
        _ => Err(ReviewError::InvalidArgument(format!(
            "unsupported review source: {value}; expected uncommitted or branch"
        ))),
    }
}

fn parse_output(value: &str) -> Result<OutputFormat> {
    OutputFormat::parse(value).ok_or_else(|| {
        ReviewError::InvalidArgument(format!(
            "unsupported output format: {value}; expected md or xml"
        ))
    })
}

fn prompt_source() -> Result<SourceArgument> {
    let choice = select_option(
        "Review source",
        &[
            MenuOption::new("Review PR-style changes", "branch")
                .detail("compare branch and current uncommitted changes"),
            MenuOption::new("Review uncommitted changes", "uncommitted")
                .detail("working tree and staged changes"),
        ],
        false,
    )?;
    parse_source(&choice)
}

fn prompt_branch(root: &Path) -> Result<String> {
    let current = current_branch(root)?;
    let branches = default_branch_candidates(root)?
        .into_iter()
        .filter(|branch| branch != &current)
        .collect::<Vec<_>>();
    if branches.is_empty() {
        return Err(ReviewError::Message(
            "no branches are available for comparison".to_owned(),
        ));
    }
    select_branch_target("Target branch", &current, &branches, true)
}

fn reset_terminal_after_tui() {
    if io::stdout().is_terminal() {
        print!("\x1b[0m\x1b[?25h");
        let _ = io::stdout().flush();
    }
}

fn deliver_review(message: &str, markdown: &str, output_directory: &Path) -> Result<i32> {
    let panes = list_panes().unwrap_or_default();
    let mut options = vec![
        MenuOption::new("Save to file", "file")
            .detail("write Markdown review to ./review-YYYYMMDD-HHMM.md"),
        MenuOption::new("Send to terminal", "stdout").detail("print review to stdout"),
    ];
    options.extend(
        panes
            .iter()
            .map(|pane| MenuOption::new(pane.display(), pane.pane_id.clone())),
    );
    let choice = select_option("Delivery target", &options, true)?;
    match choice.as_str() {
        "file" => match save_review_file(markdown, output_directory, SystemTime::now()) {
            Ok(path) => {
                println!("Saved review to {}.", path.display());
                Ok(0)
            }
            Err(error) => {
                eprintln!("review: {error}");
                print!("{markdown}");
                Ok(1)
            }
        },
        "stdout" => {
            print!("{message}");
            Ok(0)
        }
        pane_id => match send_text(pane_id, message) {
            Ok(()) => {
                println!("Sent review to tmux pane {pane_id}.");
                Ok(0)
            }
            Err(error) => {
                eprintln!("review: tmux delivery failed: {error}");
                print!("{message}");
                Ok(1)
            }
        },
    }
}

fn run_history(args: &[String]) -> Result<i32> {
    match args.first().map(String::as_str) {
        Some("ls") => {
            if args.len() > 1 {
                if matches!(args[1].as_str(), "-h" | "--help") {
                    println!("Usage: review ls\n\nList up to 10 recent saved reviews.");
                    return Ok(0);
                }
                return Err(ReviewError::InvalidArgument(format!(
                    "unknown argument for review ls: {}",
                    args[1]
                )));
            }
            list_saved_reviews()
        }
        Some("display") => {
            let mut save = false;
            for argument in &args[1..] {
                match argument.as_str() {
                    "-f" | "--file" => save = true,
                    "-h" | "--help" => {
                        println!(
                            "Usage: review display [--file]\n\nSelect and print a saved review."
                        );
                        return Ok(0);
                    }
                    value => {
                        return Err(ReviewError::InvalidArgument(format!(
                            "unknown argument for review display: {value}"
                        )));
                    }
                }
            }
            display_saved_review(save)
        }
        _ => unreachable!(),
    }
}

fn list_saved_reviews() -> Result<i32> {
    let reviews = list_archived_reviews(10)?;
    if reviews.is_empty() {
        println!("No saved reviews.");
        return Ok(0);
    }
    for (index, review) in reviews.iter().enumerate() {
        println!("{}", archived_review_label(review, Some(index + 1)));
    }
    Ok(0)
}

fn display_saved_review(save_to_file: bool) -> Result<i32> {
    let reviews = list_archived_reviews(10)?;
    if reviews.is_empty() {
        println!("No saved reviews.");
        return Ok(0);
    }
    let options = reviews
        .iter()
        .enumerate()
        .map(|(index, review)| {
            MenuOption::new(archived_review_label(review, None), index.to_string()).detail(
                review
                    .archive_path
                    .file_name()
                    .and_then(|value| value.to_str())
                    .unwrap_or_default(),
            )
        })
        .collect::<Vec<_>>();
    let stderr_menu = !io::stdout().is_terminal();
    let choice = select_option_to("Saved reviews", &options, true, stderr_menu)?;
    let index = choice
        .parse::<usize>()
        .map_err(|_| ReviewError::Message("invalid saved review selection".to_owned()))?;
    let review = &reviews[index];
    if save_to_file {
        let directory = std::env::current_dir()
            .map_err(|error| ReviewError::io("could not determine current directory", error))?;
        match save_review_file(&review.review_message, &directory, SystemTime::now()) {
            Ok(path) => println!("Saved review to {}.", path.display()),
            Err(error) => {
                eprintln!("review: {error}");
                print!("{}", review.review_message);
                return Ok(1);
            }
        }
    } else {
        print!("{}", review.review_message);
    }
    Ok(0)
}

fn archived_review_label(review: &ArchivedReview, index: Option<usize>) -> String {
    let prefix = index.map_or_else(String::new, |index| format!("{index}. "));
    format!(
        "{prefix}{}  {}  {}",
        review.timestamp_label(),
        review.branch,
        review.repository_path
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parser_accepts_short_xml_output_option() {
        let parsed = parse_arguments(&[
            "--source".into(),
            "uncommitted".into(),
            "-o".into(),
            "xml".into(),
            "--stdout".into(),
        ])
        .unwrap();
        assert_eq!(parsed.source, Some(SourceArgument::Uncommitted));
        assert_eq!(parsed.output_format, OutputFormat::Xml);
        assert!(parsed.stdout);
    }

    #[test]
    fn parser_rejects_target_for_uncommitted_source() {
        let error =
            parse_arguments(&["--source=uncommitted".into(), "--target=main".into()]).unwrap_err();
        assert!(error.to_string().contains("--target"));
    }
}
