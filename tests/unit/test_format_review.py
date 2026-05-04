import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from review.diff_model import ReviewSource, create_review_file
from review.format_review import format_review
from review.review_state import ReviewState


class FormatReviewTests(unittest.TestCase):
    def test_empty_review_message(self):
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [])
        self.assertEqual(format_review(state), "No review comments.\n")
        self.assertEqual(format_review(state, "xml"), "No review comments.\n")

    def test_xml_single_comment_output(self):
        file = create_review_file("src/app.ts", "modified", ["const a = 1;"], ["const a = 2;"])
        state = ReviewState(Path("/repo"), ReviewSource("branch", target_branch="main"), [file])
        state.add_comment("Use a named constant.")
        output = format_review(state, "xml")
        root = ET.fromstring(output)

        self.assertEqual(root.tag, "review_feedback")
        self.assertEqual(root.attrib, {})
        self.assertEqual(root.find("metadata/repository").attrib["path"], "/repo")
        source = root.find("metadata/source")
        self.assertEqual(source.attrib["kind"], "branch")
        self.assertEqual(source.attrib["target_branch"], "main")
        self.assertEqual(source.text, "branch comparison against main")
        file_element = root.find("review_comments/file")
        self.assertEqual(file_element.attrib["path"], "src/app.ts")
        self.assertNotIn("status", file_element.attrib)
        self.assertNotIn("language", file_element.attrib)
        review_comment = file_element.find("review_comment")
        self.assertEqual(review_comment.attrib, {"id": "c1"})
        self.assertEqual(review_comment.find("message").text, "Use a named constant.")

    def test_comment_output_includes_two_context_lines_around_selection(self):
        file = create_review_file(
            "src/app.py",
            "modified",
            ["line 1", "line 2", "old value", "line 4", "line 5", "line 6"],
            ["line 1", "line 2", "new value", "line 4", "line 5", "line 6"],
        )
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        added_row = next(line.index for line in file.lines if line.kind == "addition")
        state.select_range("src/app.py", added_row, added_row)
        state.add_comment("Comment with surrounding context.")

        output = format_review(state, "xml")
        root = ET.fromstring(output)
        context = root.find("review_comments/file/review_comment/context").text

        self.assertIn("   2   line 2", context)
        self.assertIn("   3 - old value", context)
        self.assertIn("   3 + new value", context)
        self.assertIn("   4   line 4", context)
        self.assertIn("   5   line 5", context)
        self.assertNotIn("line 1", context)

    def test_deleted_line_labels_old_side(self):
        file = create_review_file("src/app.js", "deleted", ["const a = 1;"], [])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.add_comment("This deletion needs explanation.")
        output = format_review(state, "xml")
        root = ET.fromstring(output)
        line_range = root.find("review_comments/file/review_comment/location/line_range")
        context = root.find("review_comments/file/review_comment/context").text

        self.assertEqual(line_range.text, "Old line: 1")
        self.assertEqual(line_range.attrib["side"], "old")
        self.assertEqual(line_range.attrib["start"], "1")
        self.assertIn("   1 - const a = 1;", context)

    def test_xml_output_escapes_code_and_comment_text(self):
        file = create_review_file("README.md", "modified", ["<old & value>"], ["<new & value>"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.move_selection(1)
        state.add_comment("Comment with XML chars: <tag attr=\"value\"> & text")
        output = format_review(state, "xml")
        root = ET.fromstring(output)
        context = root.find("review_comments/file/review_comment/context").text

        self.assertIn("<![CDATA[", output)
        self.assertIn("<new & value>", output)
        self.assertIn("   1 + <new & value>", context)
        self.assertEqual(
            root.find("review_comments/file/review_comment/message").text,
            "Comment with XML chars: <tag attr=\"value\"> & text",
        )

    def test_context_cdata_splits_embedded_cdata_end_marker(self):
        file = create_review_file("README.md", "modified", ["old"], ["]]>"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.move_selection(1)
        state.add_comment("CDATA split")
        output = format_review(state, "xml")
        root = ET.fromstring(output)

        self.assertIn("]]]]><![CDATA[>", output)
        self.assertIn("   1 + ]]>", root.find("review_comments/file/review_comment/context").text)

    def test_xml_output_replaces_invalid_xml_control_characters(self):
        file = create_review_file("app.txt", "modified", ["old"], ["new\x01value"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.move_selection(1)
        state.add_comment("Bad control:\x02")
        output = format_review(state, "xml")
        root = ET.fromstring(output)

        self.assertIn("new\uFFFDvalue", root.find("review_comments/file/review_comment/context").text)
        self.assertEqual(root.find("review_comments/file/review_comment/message").text, "Bad control:\uFFFD")

    def test_comment_body_preserves_markdown_fences_without_markdown_wrapping(self):
        file = create_review_file("README.md", "modified", ["old"], ["new"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.add_comment("Comment with a fence:\n~~~\n```")
        output = format_review(state, "xml")
        root = ET.fromstring(output)

        self.assertEqual(root.find("review_comments/file/review_comment/message").text, "Comment with a fence:\n~~~\n```")
        self.assertNotIn("~~~text", output)
        self.assertNotIn("```markdown", output)

    def test_default_markdown_output_format_uses_fenced_context_and_comments(self):
        file = create_review_file("src/app.ts", "modified", ["const a = 1;"], ["const a = 2;"])
        state = ReviewState(Path("/repo"), ReviewSource("branch", target_branch="main"), [file])
        state.extend_selection(1)
        state.add_comment("Use a named constant.")

        output = format_review(state)

        self.assertIn("# Review comments for /repo", output)
        self.assertIn("## Source: branch comparison against main", output)
        self.assertIn("### File: src/app.ts", output)
        self.assertIn("Old line: 1; New line: 1", output)
        self.assertIn("```ts\n   1 - const a = 1;\n   1 + const a = 2;\n```", output)
        self.assertIn("Comment:\n~~~text\nUse a named constant.\n~~~", output)
        self.assertNotIn("<review_feedback>", output)

    def test_markdown_output_expands_fences_for_code_and_comment_body(self):
        file = create_review_file("README.md", "modified", ["```"], ["````"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.move_selection(1)
        state.add_comment("Comment with fence:\n~~~")

        output = format_review(state)

        self.assertIn("`````markdown", output)
        self.assertIn("~~~~text\nComment with fence:\n~~~\n~~~~", output)

    def test_unknown_output_format_raises_value_error(self):
        file = create_review_file("src/app.ts", "modified", ["const a = 1;"], ["const a = 2;"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [file])
        state.add_comment("Use a named constant.")

        with self.assertRaises(ValueError):
            format_review(state, "json")

    def test_multiple_comments_grouped_by_file(self):
        first = create_review_file("a.py", "modified", ["a", "b"], ["A", "b"])
        second = create_review_file("b.py", "modified", ["c"], ["C"])
        state = ReviewState(Path("/repo"), ReviewSource("uncommitted"), [first, second])
        state.select_file("a.py")
        state.add_comment("First")
        state.select_file("b.py")
        state.add_comment("Second")
        output = format_review(state, "xml")
        root = ET.fromstring(output)
        paths = [file.attrib["path"] for file in root.findall("review_comments/file")]

        self.assertEqual(paths, ["a.py", "b.py"])


if __name__ == "__main__":
    unittest.main()
