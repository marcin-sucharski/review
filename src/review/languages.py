from __future__ import annotations

from pathlib import Path


EXTENSION_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".java": "java",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".css": "css",
    ".html": "html",
    ".htm": "html",
    ".sql": "sql",
    ".xml": "xml",
    ".json": "json",
    ".properties": "properties",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".md": "markdown",
    ".markdown": "markdown",
    ".nix": "nix",
}

FENCE_LANGUAGES = {
    "python": "python",
    "java": "java",
    "javascript": "js",
    "jsx": "jsx",
    "typescript": "ts",
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
    "gitignore": "gitignore",
    "text": "text",
}


def language_for_path(path: str) -> str:
    lower = Path(path).name.lower()
    if lower in {"makefile", "dockerfile"}:
        return lower
    if lower in {".gitignore", ".ignore", ".dockerignore"}:
        return "gitignore"
    if lower.endswith(".lock"):
        return "json"
    return EXTENSION_LANGUAGES.get(Path(lower).suffix, "text")


def fence_language(language: str) -> str:
    return FENCE_LANGUAGES.get(language, "text")
