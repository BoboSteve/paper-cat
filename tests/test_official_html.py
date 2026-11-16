import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "official_html.py"


class OfficialHTMLTest(unittest.TestCase):
    def test_extract_links(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            (task / "page.html").write_text(
                '<a href="/paper/1.html">A sufficiently long paper title</a>'
                '<a href="/about.html">About</a>',
                encoding="utf-8",
            )
            subprocess.run([
                sys.executable, str(SCRIPT),
                "--url", "https://example.org/main",
                "--input", str(task / "page.html"),
                "--venue", "Example 2026",
                "--venueid", "example/main",
                "--track-label", "main",
                "--year", "2026",
                "--link-pattern", r"/paper/",
                "--out", str(task / "out.jsonl"),
                "--summary", str(task / "summary.json"),
            ], check=True)
            rows = [json.loads(line) for line in (task / "out.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["track"], "main")


if __name__ == "__main__":
    unittest.main()
