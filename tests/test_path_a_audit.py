import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from path_a_audit import compare  # noqa: E402


class PathAAuditTest(unittest.TestCase):
    def test_compare_by_doi_or_title(self):
        primary = [
            {"title": "Example Paper", "doi": "10.1/x"},
            {"title": "Title Only"},
        ]
        secondary = [
            {"title": "Example Paper", "doi": "https://doi.org/10.1/X"},
            {"title": "Title Only"},
            {"title": "Extra"},
        ]
        result = compare(primary, secondary)
        self.assertEqual(result["matched"], 2)
        self.assertEqual(len(result["only_secondary"]), 1)


if __name__ == "__main__":
    unittest.main()
