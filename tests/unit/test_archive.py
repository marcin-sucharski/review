import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from review.archive import archive_review, review_archive_dir
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


if __name__ == "__main__":
    unittest.main()
