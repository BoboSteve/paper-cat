import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from psm_search import convert_paper  # noqa: E402


class PSMConvertTest(unittest.TestCase):
    def test_untitled_record_is_skipped(self):
        item = {"query_id": "q1", "query": "medical", "query_hash": "hash", "sources": "openalex"}
        self.assertIsNone(convert_paper({"abstract": "No title"}, item, {}))


if __name__ == "__main__":
    unittest.main()
