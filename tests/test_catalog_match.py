import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from catalog_match import apply_match, keys  # noqa: E402


class CatalogMatchTest(unittest.TestCase):
    def test_keys_and_fields(self):
        self.assertIn("title:examplepaper", keys({"title": "Example Paper"}))
        row = apply_match(
            {"title": "Example", "abstract": ""},
            "official",
            {"record_id": "x", "title": "Example", "venue": "ACL", "track": "main", "abstract": "Full abstract."},
            "title:example",
        )
        self.assertEqual(row["venue"], "ACL")
        self.assertEqual(row["abstract"], "Full abstract.")


if __name__ == "__main__":
    unittest.main()
