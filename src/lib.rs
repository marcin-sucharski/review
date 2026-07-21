pub mod archive;
pub mod cli;
pub mod error;
pub mod file_tree;
pub mod format;
pub mod git;
pub mod menu;
pub mod model;
pub mod state;
pub mod syntax;
pub mod tmux;
pub mod tui;
pub mod watch;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
