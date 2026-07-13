use std::fmt::{Display, Formatter};
use std::io;

#[derive(Debug)]
pub enum ReviewError {
    NotGitRepository,
    NoChanges(String),
    Git {
        command: String,
        status: Option<i32>,
        message: String,
    },
    Io {
        operation: String,
        source: io::Error,
    },
    InvalidArgument(String),
    Cancelled,
    Message(String),
}

impl ReviewError {
    pub fn io(operation: impl Into<String>, source: io::Error) -> Self {
        Self::Io {
            operation: operation.into(),
            source,
        }
    }
}

impl Display for ReviewError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NotGitRepository => write!(formatter, "not a Git repository"),
            Self::NoChanges(message) | Self::InvalidArgument(message) | Self::Message(message) => {
                formatter.write_str(message)
            }
            Self::Git {
                command, message, ..
            } => write!(formatter, "{command} failed: {message}"),
            Self::Io { operation, source } => write!(formatter, "{operation}: {source}"),
            Self::Cancelled => formatter.write_str("review cancelled"),
        }
    }
}

impl std::error::Error for ReviewError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io { source, .. } => Some(source),
            _ => None,
        }
    }
}

pub type Result<T> = std::result::Result<T, ReviewError>;
