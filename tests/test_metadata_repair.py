import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from metadata_repair import merge_repair, normalize_doi, title_matches  # noqa: E402


class MetadataRepairTest(unittest.TestCase):
    def test_title_match_is_conservative(self):
        self.assertTrue(title_matches("Medical Factuality via Evidence", "Medical Factuality through Evidence"))
        self.assertFalse(title_matches("Medical Factuality", "Efficient LLM Inference"))

    def test_doi_normalization(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1000/ABC"), "10.1000/abc")

    def test_merge_keeps_existing_values(self):
        row = {"title": "Example Paper", "year": 2026, "venue": "ACL", "abstract": ""}
        candidate = {
            "title": "Example Paper",
            "year": 2026,
            "venue": "ACL",
            "abstract": "A complete abstract.",
        }
        self.assertEqual(merge_repair(row, candidate), {"abstract": "A complete abstract."})


if __name__ == "__main__":
    unittest.main()
