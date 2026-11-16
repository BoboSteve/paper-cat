import io
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import common  # noqa: E402
from common import HostThrottle, acl_anthology_id, arxiv_id_of, locate_quote, parse_acl_id  # noqa: E402


class AclIdTest(unittest.TestCase):
    def test_tracks_from_anthology_ids(self):
        cases = {
            "10.18653/2026.findings-emnlp.889": ("conf/emnlp", "findings"),
            "10.18653/2026.emnlp-main.2086": ("conf/emnlp", "main"),
            "10.18653/v1/2024.acl-long.12": ("conf/acl", "main"),
            "https://aclanthology.org/2025.naacl-short.3/": ("conf/naacl", "short"),
            "10.18653/2025.acl-demo.4": ("conf/acl", "demo"),
            "https://aclanthology.org/2024.tacl-1.5.pdf": ("journals/tacl", "journal"),
            "https://aclanthology.org/2026.nlp4science-1.7/": (None, "workshop"),
        }
        for value, expected in cases.items():
            parsed = parse_acl_id(acl_anthology_id({"doi": value} if value.startswith("10.") else {"url": value}))
            self.assertEqual((parsed["venue_key"], parsed["track"]), expected, value)

    def test_arxiv_id_from_doi_and_urls(self):
        self.assertEqual(arxiv_id_of({"doi": "10.48550/arXiv.2604.04804"}), "2604.04804")
        self.assertEqual(arxiv_id_of({"url": "https://arxiv.org/abs/2604.04804v3"}), "2604.04804")
        self.assertEqual(arxiv_id_of({"doi": "10.1145/3796507"}), "")


class QuoteTest(unittest.TestCase):
    PAGES = ["Intro page.", "We find that typed contracts help.\nThe contract alone falls be-\nlow the prose baseline ﬁnally."]

    def test_verbatim_quote_across_hyphenated_line_break(self):
        result = locate_quote("The contract alone falls below the prose baseline finally.", self.PAGES)
        self.assertEqual((result["found"], result["page"]), (True, 2))

    def test_paraphrase_and_invention_are_rejected(self):
        self.assertFalse(locate_quote("Contracts alone are worse than prose, the authors say.", self.PAGES)["found"])
        self.assertFalse(locate_quote("Our method beats every baseline on all benchmarks.", self.PAGES)["found"])
        self.assertFalse(locate_quote("too short", self.PAGES)["found"])


class ThrottleTest(unittest.TestCase):
    def test_spacing_is_enforced_across_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = HostThrottle(directory), HostThrottle(directory)  # e.g. two processes
            first.wait("example.org", 0.3)
            start = time.monotonic()
            second.wait("example.org", 0.3)
            self.assertGreaterEqual(time.monotonic() - start, 0.25)


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)

    def open(self, request, timeout=None):
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return io.BytesIO(item)


class HttpRetryTest(unittest.TestCase):
    def test_429_honours_retry_after_then_succeeds(self):
        error = urllib.error.HTTPError("https://export.arxiv.org/api", 429, "Too Many", {"Retry-After": "7"}, None)
        sleeps = []
        common._THROTTLE = HostThrottle(tempfile.mkdtemp())
        body = common.http_get("https://export.arxiv.org/api/query", opener=FakeOpener([error, b"ok"]),
                               sleep=sleeps.append)
        self.assertEqual(body, "ok")
        self.assertEqual(sleeps, [7.0])

    def test_404_is_not_retried(self):
        error = urllib.error.HTTPError("https://api.openalex.org/x", 404, "Not Found", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            common.http_get("https://api.openalex.org/x", opener=FakeOpener([error]), sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
