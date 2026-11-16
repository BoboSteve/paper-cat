import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "plan.py"


class PlanTest(unittest.TestCase):
    def test_plan_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brief = {
                "topic": "Medical factuality",
                "topic_description": "Medical evidence internalization and RSI.",
                "time_window": "2026-07-01/2026-10-28",
                "inclusion": ["medical factuality", "transferable RSI"],
                "exclusion": ["pure medical application"],
                "sources": ["openalex", "arxiv"],
                "regression_subtopics": ["RSI", "external evidence"],
            }
            (root / "brief.json").write_text(json.dumps(brief), encoding="utf-8")
            queries = [
                {"query_id": "q1", "query": "medical RSI"},
                {"query_id": "q2", "query": "medical evidence distillation"},
                {"query_id": "q3", "query": "medical data co-evolution"},
            ]
            (root / "queries.jsonl").write_text(
                "\n".join(json.dumps(row) for row in queries) + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--brief", str(root / "brief.json"),
                    "--queries", str(root / "queries.jsonl"),
                    "--path", "B",
                    "--out", str(root / "plan.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["queries"]["count"], 3)
        self.assertEqual(plan["jev"]["workers"], 1)
        self.assertEqual(plan["regression"]["scope"], "target_topic_only")


if __name__ == "__main__":
    unittest.main()
