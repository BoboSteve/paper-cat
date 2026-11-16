import datetime
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import infer_date, window_status  # noqa: E402

D = datetime.date


class ScopeFilterTest(unittest.TestCase):
    def test_arxiv_date_is_month_precision(self):
        self.assertEqual(infer_date({"url": "https://arxiv.org/abs/2608.12345v1"})[:2], ("2026-08-01", "month"))

    def test_unknown_date_is_not_invented(self):
        self.assertEqual(infer_date({"title": "No date"}), (None, None, None))

    def test_openalex_january_first_is_year_precision(self):
        # OpenAlex writes year-only works as YYYY-01-01 (seen for EMNLP 2026 in the demo run).
        self.assertEqual(infer_date({"published_date": "2026-01-01T00:00:00"})[1], "year")
        self.assertEqual(infer_date({"published_date": "2026-04-06T00:00:00"})[1], "day")

    def test_acl_volume_is_a_year_not_july_first(self):
        value, precision, source = infer_date({"doi": "10.18653/2026.emnlp-main.12"})
        self.assertEqual((value, precision, source), ("2026-01-01", "year", "acl_anthology_id"))

    def test_boundary_is_kept_not_guessed(self):
        start, end = D(2025, 7, 1), D(2026, 11, 15)
        self.assertEqual(window_status({"year": 2025}, start, end)[0], "boundary")
        self.assertEqual(window_status({"year": 2024}, start, end)[0], "out_of_window")
        self.assertEqual(window_status({"published_date": "2025-09-03"}, start, end)[0], "in_window")
        self.assertEqual(window_status({"url": "arxiv.org/abs/2511.00001"}, start, end)[0], "in_window")
        self.assertEqual(window_status({"url": "arxiv.org/abs/2507.00001"}, D(2025, 7, 15), end)[0], "boundary")
        self.assertEqual(window_status({"title": "x"}, start, end)[0], "unknown")

    def test_current_year_is_not_pushed_into_the_future(self):
        start, end, today = D(2025, 1, 1), D(2026, 11, 15), D(2026, 11, 15)
        self.assertEqual(window_status({"year": 2026}, start, end, today)[0], "in_window")
        self.assertEqual(window_status({"year": 2026}, start, D(2026, 6, 30), today)[0], "boundary")


if __name__ == "__main__":
    unittest.main()
