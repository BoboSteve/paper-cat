import sys
import tempfile
import json
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from merge_candidates import merge, near_title_similarity  # noqa: E402


class NearDuplicateTest(unittest.TestCase):
    def test_shortened_title_is_flagged_but_not_merged(self):
        rows = [
            {
                "title": "Medical Factuality: A Systematic Review of Evaluation and Training Methods",
                "authors": ["Ada Lovelace"],
                "year": 2023,
                "venue": "arXiv",
                "record_type": "preprint",
                "source": "arxiv",
            },
            {
                "title": "Medical Factuality: A Systematic Review",
                "authors": ["Ada Lovelace"],
                "year": 2025,
                "venue": "ACM Computing Surveys",
                "record_type": "journal",
                "source": "crossref",
                "doi": "10.1000/example",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            candidates, report = merge([("seed", str(path))])
        self.assertEqual(len(candidates), 2)
        self.assertEqual(report["counts"]["near_duplicate_candidates"], 1)
        self.assertGreater(near_title_similarity(rows[0]["title"], rows[1]["title"]), 0.86)


if __name__ == "__main__":
    unittest.main()
