import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fulltext_verify import evidence_snippets, pages_from_text, title_match  # noqa: E402


class FulltextVerifyTest(unittest.TestCase):
    def test_pages_and_evidence(self):
        pages = pages_from_text("Title\nMethod uses evidence.\fResults improve medical factuality.")
        self.assertEqual(len(pages), 2)
        hits = evidence_snippets(pages, ["medical"], max_hits=1)
        self.assertEqual(hits[0]["page"], 2)
        self.assertIn("medical factuality", hits[0]["snippet"])

    def test_title_match(self):
        self.assertGreater(title_match("Medical Evidence Distillation", "Medical Evidence Distillation for QA"), 0.9)


if __name__ == "__main__":
    unittest.main()
