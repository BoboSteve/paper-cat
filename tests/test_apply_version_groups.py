import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply_version_groups.py"


class VersionGroupsTest(unittest.TestCase):
    def test_highest_rank_member_represents_group(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            rows = [
                {"record_id": "a", "title": "Preprint", "jev_score": 0.8},
                {"record_id": "b", "title": "Formal", "jev_score": 0.9, "venue": "ACL"},
                {"record_id": "c", "title": "Other", "jev_score": 0.2},
            ]
            (task / "rows.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            groups = [{"group_id": "g", "action": "merge", "record_ids": ["a", "b"]}]
            (task / "groups.json").write_text(json.dumps(groups), encoding="utf-8")
            subprocess.run([
                sys.executable, str(SCRIPT),
                "--input", str(task / "rows.jsonl"),
                "--groups", str(task / "groups.json"),
                "--out", str(task / "out.jsonl"),
                "--report", str(task / "report.json"),
            ], check=True)
            output = [json.loads(line) for line in (task / "out.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(output), 2)
        representative = next(row for row in output if row.get("version_group") == "g")
        self.assertEqual(representative["record_id"], "b")


if __name__ == "__main__":
    unittest.main()
