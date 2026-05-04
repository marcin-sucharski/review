import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from review.archive import archive_review, list_archived_reviews, load_archived_review, review_archive_dir
from review.diff_model import ReviewSource, create_review_file
from review.review_state import ReviewState


class ArchiveTests(unittest.TestCase):
    def test_review_archive_dir_uses_xdg_data_home(self):
        self.assertEqual(
            review_archive_dir({"XDG_DATA_HOME": "/tmp/xdg-data"}),
            Path("/tmp/xdg-data/review/reviews"),
        )

    def test_archive_review_writes_json_review_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            xdg_data = Path(temp) / "xdg"
            root.mkdir()
            file = create_review_file("app.py", "modified", ["old"], ["new"])
            state = ReviewState(root, ReviewSource("uncommitted"), [file])
            state.add_comment("Needs work.")
            message = "review output\n"

            with (
                mock.patch.dict("os.environ", {"XDG_DATA_HOME": str(xdg_data)}),
                mock.patch("review.archive.current_branch", return_value="feature/review"),
            ):
                path = archive_review(state, message)

            self.assertEqual(path.parent, xdg_data / "review" / "reviews")
            self.assertEqual(path.suffix, ".json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["path"], str(root))
            self.assertEqual(payload["branch"], "feature/review")
            self.assertEqual(payload["review_message"], message)

    def test_list_archived_reviews_returns_latest_ten_valid_reviews(self):
        with tempfile.TemporaryDirectory() as temp:
            xdg_data = Path(temp) / "xdg"
            archive_dir = xdg_data / "review" / "reviews"
            archive_dir.mkdir(parents=True)
            for index in range(12):
                path = archive_dir / f"20260504T120{index:02d}000000Z-{index}.json"
                path.write_text(
                    json.dumps(
                        {
                            "path": f"/repo/{index}",
                            "branch": f"branch-{index}",
                            "review_message": f"message {index}\n",
                        }
                    ),
                    encoding="utf-8",
                )
                os.utime(path, (100 + index, 100 + index))
            invalid = archive_dir / "20260504T130000000000Z-invalid.json"
            invalid.write_text("{not json", encoding="utf-8")
            os.utime(invalid, (999, 999))

            reviews = list_archived_reviews(environ={"XDG_DATA_HOME": str(xdg_data)})

            self.assertEqual(len(reviews), 10)
            self.assertEqual([review.review_message for review in reviews[:3]], ["message 11\n", "message 10\n", "message 9\n"])
            self.assertEqual(reviews[-1].review_message, "message 2\n")

    def test_load_archived_review_ignores_missing_or_invalid_message(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "review.json"
            archive_path.write_text(json.dumps({"path": "/repo", "branch": "main"}), encoding="utf-8")

            self.assertIsNone(load_archived_review(archive_path))

    def test_list_archived_reviews_returns_empty_for_non_positive_limit(self):
        self.assertEqual(list_archived_reviews(limit=0, environ={"XDG_DATA_HOME": "/does/not/matter"}), [])

    def test_load_archived_review_allows_legacy_missing_path_and_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "20260504T120000000000Z-review.json"
            archive_path.write_text(json.dumps({"review_message": "message\n"}), encoding="utf-8")

            review = load_archived_review(archive_path)

            self.assertIsNotNone(review)
            assert review is not None
            self.assertEqual(review.repository_path, "unknown-path")
            self.assertEqual(review.branch, "unknown-branch")
            self.assertEqual(review.timestamp_label, "20260504T120000000000Z")


if __name__ == "__main__":
    unittest.main()
