import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "missing_fulltext_report.py"


class MissingFulltextReportTest(unittest.TestCase):
    def test_high_priority_only(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            fields = [
                "idx", "title", "year", "venue", "doi_or_arxiv", "url",
                "fulltext_status", "fulltext_error", "read_scope",
                "missing_fulltext_priority", "manual_retrieval_note", "abstract",
            ]
            with (task / "sheet.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "idx": 1, "title": "Important Paper", "read_scope": "pending_fulltext",
                    "missing_fulltext_priority": "high", "manual_retrieval_note": "Direct hit",
                })
                writer.writerow({
                    "idx": 2, "title": "Low Paper", "read_scope": "pending_fulltext",
                    "missing_fulltext_priority": "", "manual_retrieval_note": "",
                })
            subprocess.run([
                sys.executable, str(SCRIPT),
                "--sheet", str(task / "sheet.csv"),
                "--out-md", str(task / "missing.md"),
                "--out-csv", str(task / "missing.csv"),
            ], check=True)
            markdown = (task / "missing.md").read_text(encoding="utf-8")
            report = (task / "missing.csv").read_text(encoding="utf-8-sig")
        self.assertIn("Important Paper", markdown)
        self.assertNotIn("Low Paper", markdown)
        self.assertIn("Important Paper", report)


if __name__ == "__main__":
    unittest.main()
