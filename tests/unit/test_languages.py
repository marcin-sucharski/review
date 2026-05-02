import unittest

from review.languages import fence_language, language_for_path
from review.tui.highlight import highlighting_available, lexer_name_for_language, syntax_spans


class LanguageTests(unittest.TestCase):
    def test_required_language_mapping(self):
        cases = {
            "Main.java": "java",
            "app.js": "javascript",
            "component.jsx": "jsx",
            "app.ts": "typescript",
            "component.tsx": "tsx",
            "styles.css": "css",
            "index.html": "html",
            "schema.sql": "sql",
            "config.xml": "xml",
            "data.json": "json",
            "application.properties": "properties",
            "settings.yaml": "yaml",
            "settings.yml": "yaml",
            "script.py": "python",
            "README.md": "markdown",
            "notes.markdown": "markdown",
            "flake.nix": "nix",
            "flake.lock": "json",
            ".gitignore": "gitignore",
            "unknown.xyz": "text",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(language_for_path(path), expected)

    def test_fence_language_mapping(self):
        self.assertEqual(fence_language("typescript"), "ts")
        self.assertEqual(fence_language("javascript"), "js")
        self.assertEqual(fence_language("python"), "python")
        self.assertEqual(fence_language("markdown"), "markdown")
        self.assertEqual(fence_language("nix"), "nix")
        self.assertEqual(fence_language("gitignore"), "gitignore")
        self.assertEqual(fence_language("unknown"), "text")

    def test_syntax_spans_find_keywords_strings_and_numbers(self):
        spans = syntax_spans('const answer = "yes"; return 42;', "javascript")
        roles = {role for _, _, role in spans}
        self.assertIn("keyword", roles)
        self.assertIn("string", roles)
        self.assertIn("number", roles)

    def test_pygments_backed_lexers_cover_required_languages(self):
        self.assertTrue(highlighting_available(), "Pygments must be installed for robust syntax highlighting")
        for language in [
            "java",
            "javascript",
            "typescript",
            "css",
            "html",
            "jsx",
            "sql",
            "xml",
            "json",
            "properties",
            "yaml",
            "tsx",
            "python",
            "markdown",
            "nix",
        ]:
            with self.subTest(language=language):
                self.assertEqual(lexer_name_for_language(language), language)

    def test_html_and_jsx_have_tag_spans(self):
        html_roles = {role for _, _, role in syntax_spans("<main class=\"app\">Hi</main>", "html")}
        jsx_roles = {role for _, _, role in syntax_spans("const el = <main className=\"app\">Hi</main>;", "jsx")}
        self.assertIn("tag", html_roles)
        self.assertIn("attribute", html_roles)
        self.assertIn("tag", jsx_roles)

    def test_python_and_markdown_have_distinct_syntax_roles(self):
        python_roles = {
            role
            for _, _, role in syntax_spans(
                "def answer(value):\n    # explain\n    return value + 42\n",
                "python",
            )
        }
        markdown_roles = {role for _, _, role in syntax_spans("# Heading\n\n`code` **bold**", "markdown")}

        self.assertIn("keyword", python_roles)
        self.assertIn("function", python_roles)
        self.assertIn("comment", python_roles)
        self.assertIn("number", python_roles)
        self.assertIn("heading", markdown_roles)
        self.assertIn("string", markdown_roles)
        self.assertIn("emphasis", markdown_roles)

    def test_nix_lock_and_gitignore_highlighting(self):
        nix_roles = {role for _, _, role in syntax_spans("{ pkgs }: pkgs.python313", "nix")}
        lock_roles = {role for _, _, role in syntax_spans('{"nodes": {"root": "value", "version": 1}}', language_for_path("flake.lock"))}
        gitignore_roles = {role for _, _, role in syntax_spans("# comment\n/dist/*.pyc\n!important", "gitignore")}

        self.assertIn("punctuation", nix_roles)
        self.assertIn("string", lock_roles)
        self.assertIn("number", lock_roles)
        self.assertIn("comment", gitignore_roles)
        self.assertIn("operator", gitignore_roles)
        self.assertIn("string", gitignore_roles)

    def test_syntax_spans_are_cached_for_redraws(self):
        syntax_spans.cache_clear()
        syntax_spans("def cached():\n    return 1\n", "python")
        before = syntax_spans.cache_info()
        syntax_spans("def cached():\n    return 1\n", "python")
        after = syntax_spans.cache_info()
        self.assertEqual(after.hits, before.hits + 1)


if __name__ == "__main__":
    unittest.main()
