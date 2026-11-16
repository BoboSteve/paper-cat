"""Grading on records shaped like the 2026-11-15 demo run (tests/fixtures/demo_records.jsonl)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from venue_resolve import formal_location, resolve, venue_claim  # noqa: E402

REGISTRY = {
    "snapshot": "test",
    "conferences": {"conf/emnlp": {"title": "EMNLP", "ccf": "B"}, "conf/icml": {"title": "ICML", "ccf": "A"}},
    "journals_ccf": {"journals/ai": "A", "journals/fcs": "B"},
    "ccfrank_conferences": {},
    "ccf2026_entries": [
        {"name": "AI", "full": "Artificial Intelligence", "rank": "A", "kind": "journal", "dblp_key": "journals/ai"},
        {"name": "FCS", "full": "Frontiers of Computer Science", "rank": "B", "kind": "journal", "dblp_key": "journals/fcs"},
    ],
    "partitions_by_issn": {"2095-2228": {"cas2025": {"major": "计算机科学", "major_zone": "2"}},
                           "0269-2821": {"cas2025": {"major": "计算机科学", "major_zone": "1"}}},
}


def fixture_rows():
    rows = []
    for line in (ROOT / "tests" / "fixtures" / "demo_records.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        # Replay what OpenAlex / arXiv returned in the demo, without network access. ACL venues are
        # dropped on purpose: resolve() must rebuild them from the DOI alone.
        resolved = {key[len("resolved_"):]: row.pop(key) for key in [k for k in row if k.startswith("resolved_")]}
        if not resolved.get("venue_source_type"):
            resolved.pop("venue", None)
        rows.append({**row, **resolved})
    rows, _, _ = resolve(rows, {}, use_openalex=False, use_arxiv=False)
    return rows


class RankTracksTest(unittest.TestCase):
    def rank(self, policy):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            (task / "venue_registry.json").write_text(json.dumps(REGISTRY, ensure_ascii=False), encoding="utf-8")
            (task / "in.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in fixture_rows()), encoding="utf-8")
            subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "rank.py"), "--candidates", str(task / "in.jsonl"),
                            "--cache-dir", str(task), "--track-policy", policy, "--out", str(task / "out.jsonl")],
                           check=True, capture_output=True)
            return {r["_case"] + ("" if r["_case"] != "journal" else ":" + r["title"][:12]): r
                    for r in map(json.loads, (task / "out.jsonl").read_text(encoding="utf-8").splitlines())}, \
                [json.loads(l)["_case"] for l in (task / "out.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_main_only_excludes_findings_and_keeps_order(self):
        rows, order = self.rank("main_only")
        self.assertEqual((rows["acl_main"]["track"], rows["acl_main"]["ccf_grade"]), ("main", "B"))
        self.assertEqual(rows["acl_main"]["track_source"], "acl_anthology_id")
        self.assertTrue(rows["findings"]["track_excluded"])
        self.assertIsNone(rows["findings"]["ccf_grade"])
        self.assertEqual(rows["findings"]["main_venue_grade"], "B")
        self.assertEqual(order[-1], "findings", "excluded tracks go after every eligible record")

    def test_include_findings_switch(self):
        rows, _ = self.rank("include_findings")
        self.assertFalse(rows["findings"]["track_excluded"])
        self.assertIsNone(rows["findings"]["ccf_grade"], "Findings never inherits the main-conference grade")

    def test_journal_name_is_not_swallowed_by_a_shorter_title(self):
        rows, _ = self.rank("main_only")
        air = rows["journal:From languag"]
        self.assertIsNone(air["ccf_grade"], "Artificial Intelligence Review is not the CCF-A journal Artificial Intelligence")
        self.assertEqual(air["record_type"], "journal")
        self.assertEqual(air["partition"]["cas2025"]["major_zone"], "1")
        self.assertEqual(rows["journal:Tool learnin"]["ccf_grade"], "B")

    def test_arxiv_claim_is_reported_but_not_a_grade(self):
        rows, _ = self.rank("main_only")
        claim = rows["claim"]
        self.assertEqual(claim["record_type"], "preprint")
        self.assertIsNone(claim["ccf_grade"])
        self.assertEqual(claim["claimed_ccf_grade"], "A")


class VenueResolveUnitTest(unittest.TestCase):
    def test_claim_patterns(self):
        self.assertEqual(venue_claim("Accepted at ICML 2026 (spotlight); 22 pages"), "ICML 2026")
        self.assertEqual(venue_claim("Accepted by NeurIPS 2026. Code is available"), "NeurIPS 2026")
        self.assertEqual(venue_claim("Work in progress"), "")

    def test_formal_location_prefers_published_version(self):
        work = {"primary_location": {"source": {"type": "repository", "display_name": "arXiv"}},
                "locations": [{"source": {"type": "repository", "display_name": "arXiv"}},
                              {"source": {"type": "conference", "display_name": "ICLR"}}]}
        self.assertEqual(formal_location(work)[1]["display_name"], "ICLR")


if __name__ == "__main__":
    unittest.main()
