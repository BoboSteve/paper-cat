"""Download fallback, arXiv adapter, Xiaohongshu clues, calibration, orchestrator helpers, installer."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import calibrate_k  # noqa: E402
import clue_import  # noqa: E402
import download  # noqa: E402
import install_skill  # noqa: E402
import jev_service  # noqa: E402
import psm_search  # noqa: E402
import run  # noqa: E402
import sources  # noqa: E402
from plan import parse_window  # noqa: E402

PDF = b"%PDF-1.7 fake"


class DownloadFallbackTest(unittest.TestCase):
    def test_first_channel_fails_next_channel_succeeds_and_attempts_are_logged(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if "broken" in url:
                raise OSError("timeout")
            if "landing" in url:
                return b"<html>login</html>"
            return PDF

        row = {"title": "x", "pdf_url": "https://broken.example/a.pdf", "openAccessPdf": "https://landing.example/b",
               "doi": "10.18653/2026.emnlp-main.5"}
        with tempfile.TemporaryDirectory() as directory:
            item = download.fetch_record(3, row, directory, {}, fetch=fetch)
            self.assertEqual((item["status"], item["channel"]), ("ok", "acl_anthology"))
            self.assertTrue(Path(item["file"]).exists())
        self.assertEqual([a["status"] for a in item["attempts"]], ["failed", "failed", "ok"])
        self.assertIn("aclanthology.org/2026.emnlp-main.5.pdf", calls[-1])

    def test_acm_failure_is_routed_to_browser_or_user(self):
        def fetch(url):
            raise OSError("403")

        with tempfile.TemporaryDirectory() as directory:
            item = download.fetch_record(0, {"title": "x", "pdf_url": "https://dl.acm.org/doi/pdf/10.1145/1"}, directory, {}, fetch=fetch)
        self.assertEqual(item["status"], "failed")

    def test_top_k_skips_excluded_tracks(self):
        rows = [{"track_excluded": True}, {}, {}, {"status": "flagged"}]
        self.assertEqual(download.select_indices(rows, None, None, 1), [1, 3])


class ArxivAdapterTest(unittest.TestCase):
    ATOM = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2604.04804v2</id><title>SkillX:  Automatically
 Constructing Skill Knowledge Bases</title><summary>Abstract text.</summary>
<published>2026-04-06T10:00:00Z</published><updated>2026-05-01T00:00:00Z</updated>
<author><name>Chenxi Wang</name></author><arxiv:comment>Accepted at ICML 2026</arxiv:comment>
<link title="pdf" href="https://arxiv.org/pdf/2604.04804v2"/></entry></feed>"""

    def test_query_and_parse(self):
        self.assertEqual(sources.arxiv_all_terms("skills for the LLM agents"), "all:skills AND all:LLM AND all:agents")
        original = sources.http_get
        sources.http_get = lambda *a, **k: self.ATOM
        try:
            papers = psm_search.arxiv_search({"query": "skill", "max_results": 5})
        finally:
            sources.http_get = original
        self.assertEqual(papers[0]["doi"], "10.48550/arxiv.2604.04804")
        self.assertEqual(papers[0]["title"], "SkillX: Automatically Constructing Skill Knowledge Bases")
        self.assertEqual((papers[0]["published_date"], papers[0]["date_precision"]), ("2026-04-06", "day"))
        self.assertEqual(papers[0]["arxiv_comment"], "Accepted at ICML 2026")

    def test_override_date_window_and_paging(self):
        self.assertEqual(sources.arxiv_date_clause("2026-08-01", "2026-11-15"),
                         " AND submittedDate:[202608010000 TO 202611152359]")
        seen = []
        original = sources.http_get

        def fake(url, **kwargs):
            seen.append(url)
            return self.ATOM  # one entry per page -> stops after the first short page
        sources.http_get = fake
        try:
            psm_search.arxiv_search({"query": "x", "max_results": 10, "arxiv_query": "abs:harness",
                                     "arxiv_max_results": 250, "date_from": "2026-08-01", "date_to": "2026-11-15"})
        finally:
            sources.http_get = original
        self.assertEqual(len(seen), 1)
        self.assertIn("abs%3Aharness+AND+submittedDate", seen[0])
        self.assertIn("max_results=100", seen[0])

    def test_rate_limited_partial_results_are_retried_not_cached(self):
        item = {"query_hash": "h"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "q.json"
            path.write_text(json.dumps({"query_hash": "h", "status": "partial",
                                        "errors": {"arxiv": "arxiv rate-limited: HTTP 429"}}), encoding="utf-8")
            self.assertIsNone(psm_search.existing_summary(path, item))
            path.write_text(json.dumps({"query_hash": "h", "status": "ok"}), encoding="utf-8")
            self.assertIsNotNone(psm_search.existing_summary(path, item))


class ClueImportTest(unittest.TestCase):
    def test_xiaohongshu_mcp_detail_with_sibling_comments(self):
        payload = {"data": {"note": {"noteId": "n1", "title": "论文推荐", "desc": "看这篇 arxiv.org/abs/2604.04804 和 DOI 10.1145/3796507。",
                                     "likedCount": "12"},
                            "comments": [{"content": "还有《GraSP: Graph-Structured Skill Compositions for LLM Agents》"}]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "detail.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            notes = clue_import.notes_from_file(path)
        self.assertEqual(len(notes), 1)
        found, in_post = clue_import.note_clues(notes[0])
        self.assertEqual(found["arxiv"], ["2604.04804"])
        self.assertEqual(found["doi"], ["10.1145/3796507"])
        self.assertIn("GraSP: Graph-Structured Skill Compositions for LLM Agents", found["titles"])
        self.assertNotIn("GraSP: Graph-Structured Skill Compositions for LLM Agents", in_post)
        self.assertEqual(notes[0]["likes"], "12")

    def test_pasted_text_and_mediacrawler_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            text = Path(directory) / "post.md"
            text.write_text("论文：Agentic Skills Beyond Tool Use in LLM Agents\n链接 https://openreview.net/forum?id=abc", encoding="utf-8")
            lines = Path(directory) / "mc.jsonl"
            lines.write_text(json.dumps({"note_id": "m1", "title": "t", "desc": "arXiv:2602.20867",
                                         "liked_count": "5"}, ensure_ascii=False) + "\n", encoding="utf-8")
            pasted = clue_import.note_clues(clue_import.notes_from_file(text)[0])[0]
            crawled = clue_import.note_clues(clue_import.notes_from_file(lines)[0])[0]
        self.assertEqual(pasted["titles"], ["Agentic Skills Beyond Tool Use in LLM Agents"])
        self.assertTrue(pasted["urls"][0].startswith("https://openreview.net/"))
        self.assertEqual(crawled["arxiv"], ["2602.20867"])


class CalibrationTest(unittest.TestCase):
    def test_sample_hides_scores_and_analysis_suggests_k(self):
        rows = [{"title": f"p{i}", "jev_score": 1 - i / 100, "record_id": str(i)} for i in range(60)]
        picked, bands, seed = calibrate_k.sample(rows, k=10)
        self.assertEqual(len(picked), 20)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            calibrate_k.write_sample(picked, bands, seed, out, 10)
            with (out / "calibration_sheet.csv").open(encoding="utf-8-sig", newline="") as stream:
                sheet = list(csv.DictReader(stream))
            self.assertNotIn("jev_score", sheet[0])
            key = json.loads((out / "calibration_key.json").read_text(encoding="utf-8"))
            band_of = {s["sample_id"]: s["band"] for s in key["samples"]}
            for row in sheet:  # useful far beyond K -> suggest a larger K
                row["label"] = "useful" if band_of[int(row["sample_id"])] != "tail" else "drop"
            with (out / "calibration_sheet.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=sheet[0].keys())
                writer.writeheader()
                writer.writerows(sheet)
            self.assertIn("K≈20", calibrate_k.analyze(out)["suggestion"])


class OrchestratorHelpersTest(unittest.TestCase):
    def test_topk_overrides(self):
        rows = [{}, {"track_excluded": True}, {}, {}, {}]
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            self.assertEqual(run.selected_positions(task, rows, 2), [0, 2])
            (task / "topk_overrides.json").write_text(json.dumps({"remove": [0], "add": [4], "k": 3}), encoding="utf-8")
            self.assertEqual(run.selected_positions(task, rows, 2), [2, 3, 4])

    def test_time_window_parsing(self):
        self.assertEqual(parse_window("2025-01-01 to 2026-11-15"), {"start": "2025-01-01", "end": "2026-11-15"})
        self.assertEqual(parse_window("2024–2026 年"), {"start": "2024-01-01", "end": "2026-12-31"})
        self.assertIsNone(parse_window("近几年"))


class SourceStatusTest(unittest.TestCase):
    def test_every_source_is_optional_and_reported(self):
        from common import source_status
        rows = {row["id"]: row for row in source_status({"OPENALEX_API_KEY": "k", "OPENREVIEW_USERNAME": "u"})}
        self.assertTrue(rows["openalex"]["configured"])
        self.assertFalse(rows["openreview"]["configured"], "username without password is not configured")
        self.assertFalse(rows["semantic"]["configured"])
        self.assertTrue(all(row["benefit"] and row["without"] and row["get"] for row in rows.values()))

    def test_checkpoint_hides_openreview_for_topic_search_and_flags_rejected_keys(self):
        import common
        lines = "\n".join(run.source_section({"openalex": {"status": "blocked", "http_status": 401}}, "B"))
        self.assertNotIn("| OpenReview |", lines)
        if common.load_env().get("OPENALEX_API_KEY"):  # depends on this machine's env file
            self.assertIn("OpenAlex 已配置但连接失败", lines)


class ServiceAndInstallTest(unittest.TestCase):
    def test_placeholders_and_command_mode_output_goes_to_a_file(self):
        settings = {"tool_root": "/opt/tools"}
        self.assertEqual(jev_service.expand("{tool_root}/x", settings), "/opt/tools/x")
        code = jev_service.run_command_mode("start", {"start": [sys.executable, "-c", "print('hello service')"]}, settings)
        self.assertEqual(code, 0)

    def test_install_excludes_machine_specific_files(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = install_skill.install(directory)
            self.assertTrue((dest / "SKILL.md").exists())
            self.assertFalse((dest / "runtime.local.json").exists())
            self.assertFalse(any(dest.rglob("__pycache__")))
            self.assertTrue((dest / "runtime.example.macos.json").exists())


if __name__ == "__main__":
    unittest.main()
