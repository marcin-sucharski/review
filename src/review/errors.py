class ReviewError(Exception):
    """Base class for expected user-facing failures."""


class NotAGitRepository(ReviewError):
    """The current directory is not inside a Git repository."""


class NoChangesFound(ReviewError):
    """The selected review source has no changes."""


class GitCommandError(ReviewError):
    """A Git command failed."""

    def __init__(self, command: list[str], message: str, returncode: int | None = None):
        self.command = command
        self.returncode = returncode
        super().__init__(message)


class DiffParseError(ReviewError):
    """Raw diff metadata could not be parsed."""


class TmuxUnavailable(ReviewError):
    """tmux is not installed or cannot be reached."""


class TmuxSendError(ReviewError):
    """Sending feedback to tmux failed."""
