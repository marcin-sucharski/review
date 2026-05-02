from __future__ import annotations

from functools import lru_cache

try:
    from pygments import lex
    from pygments.lexers import get_lexer_by_name
    from pygments.token import Comment, Error, Generic, Keyword, Name, Number, Operator, Punctuation, String, Token
    from pygments.util import ClassNotFound
except ImportError:  # pragma: no cover - exercised only in an undeclared dependency environment.
    lex = None
    get_lexer_by_name = None
    ClassNotFound = Exception
    Comment = Error = Generic = Keyword = Name = Number = Operator = Punctuation = String = Token = None


LEXER_ALIASES = {
    "python": "python",
    "java": "java",
    "javascript": "javascript",
    "jsx": "jsx",
    "typescript": "typescript",
    "tsx": "tsx",
    "css": "css",
    "html": "html",
    "sql": "sql",
    "xml": "xml",
    "json": "json",
    "properties": "properties",
    "yaml": "yaml",
    "markdown": "markdown",
    "nix": "nix",
    "text": "text",
}

TOKEN_ROLE_GROUPS = ()
if Token is not None:
    TOKEN_ROLE_GROUPS = (
        (Comment, "comment"),
        (Keyword, "keyword"),
        (String, "string"),
        (Number, "number"),
        (Name.Function, "function"),
        (Name.Class, "type"),
        (Name.Builtin, "builtin"),
        (Name.Tag, "tag"),
        (Name.Attribute, "attribute"),
        (Generic.Heading, "heading"),
        (Generic.Strong, "emphasis"),
        (Generic.Emph, "emphasis"),
        (Operator, "operator"),
        (Punctuation, "punctuation"),
        (Error, "error"),
    )


def highlighting_available() -> bool:
    return lex is not None and get_lexer_by_name is not None


@lru_cache(maxsize=None)
def lexer_name_for_language(language: str) -> str:
    if not highlighting_available():
        return "text"
    alias = LEXER_ALIASES.get(language, language)
    try:
        get_lexer_by_name(alias)
        return alias
    except ClassNotFound:
        return "text"


@lru_cache(maxsize=None)
def _lexer(language: str):
    if not highlighting_available():
        return None
    try:
        return get_lexer_by_name(lexer_name_for_language(language), stripnl=False, ensurenl=False)
    except ClassNotFound:
        return get_lexer_by_name("text", stripnl=False, ensurenl=False)


@lru_cache(maxsize=8192)
def syntax_spans(text: str, language: str) -> list[tuple[int, int, str]]:
    if language == "gitignore":
        return _gitignore_spans(text)
    lexer = _lexer(language)
    if lexer is None or not text:
        return []
    spans: list[tuple[int, int, str]] = []
    offset = 0
    for token_type, value in lex(text, lexer):
        length = len(value)
        if length == 0:
            continue
        role = _role_for_token(token_type)
        if role is not None:
            spans.append((offset, offset + length, role))
        offset += length
    return spans


def _gitignore_spans(text: str) -> list[tuple[int, int, str]]:
    if not text:
        return []
    spans: list[tuple[int, int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line[:-1] if line.endswith("\n") else line
        spans.extend(_gitignore_line_spans(content, offset))
        offset += len(line)
    if not spans and "\n" not in text:
        spans.extend(_gitignore_line_spans(text, 0))
    return spans


def _gitignore_line_spans(text: str, base_offset: int) -> list[tuple[int, int, str]]:
    stripped = text.lstrip()
    leading = len(text) - len(stripped)
    if not stripped:
        return []
    if stripped.startswith("#"):
        return [(base_offset + leading, base_offset + len(text), "comment")]

    spans: list[tuple[int, int, str]] = []
    cursor = 0
    if stripped.startswith("!"):
        spans.append((base_offset + leading, base_offset + leading + 1, "operator"))
        cursor = leading + 1
    elif stripped.startswith("/"):
        spans.append((base_offset + leading, base_offset + leading + 1, "operator"))
        cursor = leading + 1

    for index, char in enumerate(text):
        if char in "*?[]":
            if cursor < index:
                spans.append((base_offset + cursor, base_offset + index, "string"))
            spans.append((base_offset + index, base_offset + index + 1, "operator"))
            cursor = index + 1
    if cursor < len(text):
        role = "operator" if text.endswith("/") and cursor == len(text) - 1 else "string"
        if text.endswith("/") and cursor < len(text) - 1:
            spans.append((base_offset + cursor, base_offset + len(text) - 1, "string"))
            spans.append((base_offset + len(text) - 1, base_offset + len(text), "operator"))
        else:
            spans.append((base_offset + cursor, base_offset + len(text), role))
    return spans


def _role_for_token(token_type) -> str | None:
    if Token is None:
        return None
    for token_group, role in TOKEN_ROLE_GROUPS:
        if token_type in token_group:
            return role
    return None
