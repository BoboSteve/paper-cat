import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "progress_dashboard.py"


class ProgressDashboardTest(unittest.TestCase):
    def test_updates_state_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            result = subprocess.run([
                sys.executable, str(SCRIPT), "--task", str(task),
                "--stage", "jev", "--completed", "20", "--total", "100",
                "--message", "20 percent complete", "--summary", "First batch complete",
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads((task / "progress_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["percent"], 20.0)
            self.assertTrue((task / "dashboard.html").exists())
            self.assertIn("First batch complete", (task / "stage_summaries.jsonl").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
