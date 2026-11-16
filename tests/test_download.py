import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from download import record_arxiv  # noqa: E402


class DownloadTest(unittest.TestCase):
    def test_arxiv_id_from_urls_and_doi(self):
        self.assertEqual(record_arxiv({"url": "https://arxiv.org/abs/2607.14777v1"}), "2607.14777")
        self.assertEqual(record_arxiv({"doi": "10.48550/arxiv.2607.14777"}), "2607.14777")


if __name__ == "__main__":
    unittest.main()
