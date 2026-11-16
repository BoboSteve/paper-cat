import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from citation_expand import abstract_from_inverted, normalize_work, title_match  # noqa: E402


class CitationExpandTest(unittest.TestCase):
    def test_inverted_abstract(self):
        self.assertEqual(abstract_from_inverted({"medical": [1], "Evidence": [0]}), "Evidence medical")

    def test_title_match_and_normalize(self):
        self.assertTrue(title_match("Medical Factuality", "Medical Factuality: A Review"))
        row = normalize_work(
            {"id": "https://openalex.org/W1", "title": "Example", "publication_year": 2026},
            {"record_id": "seed:1", "title": "Seed"},
            "references",
            "hash",
        )
        self.assertEqual(row["relationship"], "references")


if __name__ == "__main__":
    unittest.main()
