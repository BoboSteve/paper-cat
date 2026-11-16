import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(ROOT))

from common import PAGE_MARK  # noqa: E402

PAGE_ONE = "Skill libraries.\nThe extracted skills are associated with specific tool\nschemas, which makes direct re-\nuse across domains less straightforward."


class JudgmentSheetTest(unittest.TestCase):
    def run_flow(self, quote, read_scope="fulltext", extra=()):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            text = task / "pdf_text" / "0.txt"
            text.parent.mkdir()
            text.write_text(PAGE_MARK.format(n=1) + "Title page" + PAGE_MARK.format(n=2) + PAGE_ONE, encoding="utf-8")
            (task / "ranked.jsonl").write_text(
                json.dumps({"title": "Example", "abstract": "Abstract.", "jev_score": 0.9, "jev_rank": 1}) + "\n"
                + json.dumps({"title": "Findings paper", "jev_rank": 2, "track_excluded": True}) + "\n",
                encoding="utf-8",
            )
            (task / "plan.json").write_text(json.dumps({"topic": "t", "jev": {"selected_k": 2}}), encoding="utf-8")
            (task / "fulltext_manifest.json").write_text(
                json.dumps({"records": [{"idx": 0, "status": "ok", "text_path": str(text)}]}), encoding="utf-8")
            subprocess.run([sys.executable, str(ROOT / "judgment_sheet.py"), "--ranked", str(task / "ranked.jsonl"),
                            "--plan", str(task / "plan.json"), "--out-csv", str(task / "sheet.csv"),
                            "--out-md", str(task / "sheet.md")], check=True, capture_output=True)
            with (task / "sheet.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1, "track-excluded records must not enter the sheet")
            self.assertEqual(rows[0]["read_scope"], "", "read_scope is set by the reader, not prefilled")
            self.assertEqual(rows[0]["text_available"], "yes")
            rows[0].update({"decision": "core", "paper_role": "method_donor", "reason": "Directly relevant.",
                            "evidence_quote": quote, "read_scope": read_scope, "subtopics": "s"})
            with (task / "sheet.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            result = subprocess.run([sys.executable, str(ROOT / "judgment_import.py"), "--input", str(task / "sheet.csv"),
                                     "--ranked", str(task / "ranked.jsonl"), "--plan", str(task / "plan.json"),
                                     "--judged-by", "test harness", "--out", str(task / "judgments.jsonl"), *extra],
                                    capture_output=True, text=True, encoding="utf-8")
            lines = (task / "judgments.jsonl").read_text(encoding="utf-8").splitlines() if result.returncode == 0 else []
            return result, [json.loads(line) for line in lines]

    def test_verbatim_quote_across_line_breaks_is_verified_with_page(self):
        result, lines = self.run_flow("which makes direct reuse across domains less straightforward.")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lines[0]["meta"]["judged_by"], "test harness")
        self.assertTrue(lines[1]["evidence_verified"])
        self.assertEqual(lines[1]["evidence_page"], 2)

    def test_invented_quote_is_rejected(self):
        result, _ = self.run_flow("We prove the method solves every benchmark ever created.")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not found in the full text", result.stderr)

    def test_invented_quote_can_be_kept_as_warning(self):
        result, lines = self.run_flow("We prove the method solves every benchmark ever created.", extra=("--quote-check", "warn"))
        self.assertEqual(result.returncode, 0)
        self.assertFalse(lines[1]["evidence_verified"])
        self.assertTrue(lines[1]["evidence_warning"])

    def test_unread_paper_cannot_be_judged(self):
        result, _ = self.run_flow("which makes direct reuse across domains less straightforward.", read_scope="")
        self.assertEqual(result.returncode, 2)
        self.assertIn("read_scope=fulltext", result.stderr)


if __name__ == "__main__":
    unittest.main()
