"""Quick mode, report rendering, concepts, pool, reading manager, snowball, Xiaohongshu attach, packs."""
import csv
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import clue_import  # noqa: E402
import common  # noqa: E402
import concepts  # noqa: E402
import doctor  # noqa: E402
import pool  # noqa: E402
import quick  # noqa: E402
import reading  # noqa: E402
import report_html  # noqa: E402
import snowball  # noqa: E402

GROUPS = [["harness", "scaffold"], ["self-evolving", "self-improving"]]


class ConceptsTest(unittest.TestCase):
    def test_queries_are_sanitized_and_structured(self):
        groups = concepts.normalize_concepts([['harness"); DROP', "scaffold"], "self-evolving | self improving"])
        self.assertEqual(groups[0], ["harness drop", "scaffold"])
        self.assertEqual(concepts.arxiv_query([["self improving"]]), '(ti:"self improving" OR abs:"self improving")')
        self.assertEqual(concepts.openalex_query(GROUPS), '("harness" OR "scaffold") AND ("self-evolving" OR "self-improving")')

    def test_scoring_is_word_bounded_and_variant_tolerant(self):
        scorer = concepts.Scorer([["lora"], ["self-evolving"]])
        self.assertEqual(scorer.score({"title": "Exploration with self evolving agents"})["matched_terms"], ["self-evolving"])
        ranked = concepts.rank([{"title": "Scaffold only"}, {"title": "Self-evolving harness"}], GROUPS,
                               today=datetime.date(2026, 11, 15))
        self.assertEqual(ranked[0]["title"], "Self-evolving harness")
        self.assertEqual(concepts.exclusion_hints({"abstract": "we fine-tune with LoRA"}, ["lora", "rl"]), ["lora"])

    def test_empty_concepts_fall_back_to_topic_words(self):
        self.assertEqual(concepts.normalize_concepts([], "Agent memory for LLMs"), [["agent"], ["memory"], ["llms"]])
        with self.assertRaises(ValueError):
            concepts.normalize_concepts([], "智能体")


class QuickTest(unittest.TestCase):
    SHORTLIST = [{"id": "p01", "title": "A", "date": "2026-10-01", "authors": ["X", "Y", "Z"], "venue": "",
                  "url": "https://arxiv.org/abs/2610.00001", "pdf": None, "abstract": "a", "coverage": 1,
                  "matched_terms": ["harness"]},
                 {"id": "p02", "title": "B", "date": "2026-09-01", "authors": [], "venue": "ICLR",
                  "url": "javascript:alert(1)", "pdf": None, "abstract": "b", "coverage": 1, "matched_terms": []}]

    def test_dedupe_merges_same_work_across_sources(self):
        merged = quick.dedupe([
            {"source": "arxiv", "title": "MemPoison: Planting Backdoors in LLM Agent Memory", "doi": "10.48550/arxiv.2611.00001",
             "published_date": "2026-11-12", "abstract": "short"},
            {"source": "openalex", "title": "MemPoison: planting backdoors in LLM agent memory", "doi": "10.5555/x",
             "published_date": "2026-01-01", "abstract": "a much longer abstract", "venue": "Some Journal"}])
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0]["published_date"], merged[0]["venue"]), ("2026-11-12", "Some Journal"))
        self.assertEqual(merged[0]["abstract"], "a much longer abstract")

    def test_picks_cannot_invent_papers_and_are_bounded(self):
        clean, errors = quick.validate_picks({"picks": [{"id": "p09", "summary": "s", "why": "w", "cluster": "c"}]},
                                             self.SHORTLIST)
        self.assertTrue(any("不在初筛名单" in e for e in errors))
        clean, errors = quick.validate_picks({"picks": [{"id": "p01", "summary": "x" * 400, "why": "w", "cluster": "c"},
                                                        {"id": "p01", "summary": "s", "why": "w", "cluster": "c"}]},
                                             self.SHORTLIST)
        self.assertTrue(any("超过" in e for e in errors) and any("重复" in e for e in errors))
        clean, errors = quick.validate_picks({"highlights": ["h"], "also": ["p02"],
                                              "picks": [{"id": "p01", "summary": "s", "why": "w", "cluster": "c"}]},
                                             self.SHORTLIST)
        self.assertEqual((errors, clean["also"]), ([], ["p02"]))

    def test_window_defaults_and_validation(self):
        start, end = quick.parse_window(None, "2026-11-15")
        self.assertEqual((end - start).days, quick.DEFAULT_DAYS)
        with self.assertRaises(quick.UserError):
            quick.parse_window("2026-12-01", "2026-11-01")

    def test_rendered_report_escapes_and_drops_unsafe_links(self):
        clean, _ = quick.validate_picks({"picks": [{"id": "p02", "summary": "<script>x</script>", "why": "w", "cluster": "c"}]},
                                        self.SHORTLIST)
        page = quick.build_page({"topic": "T", "since": "2026-05-01", "until": "2026-11-15",
                                 "queries": {"arxiv": "q", "openalex": "q"}},
                                {"sources": {"arxiv": {"status": "ok", "records": 2}}, "raw": 2, "unique_in_window": 2,
                                 "shortlist": 2}, self.SHORTLIST, clean, [])
        html = report_html.render(page)
        self.assertNotIn("<script>x</script>", html)
        self.assertNotIn("javascript:alert", html)
        self.assertIn("基于标题和摘要的速览", html)
        self.assertEqual(report_html.safe_url("pdfs/3_arxiv.pdf"), "pdfs/3_arxiv.pdf")
        self.assertEqual(report_html.safe_url("file:///etc/passwd"), "")


class PoolTest(unittest.TestCase):
    def test_prefilter_keeps_seeds_and_reports_cut(self):
        rows = [{"title": f"unrelated {i}"} for i in range(10)] + [{"title": "self-evolving harness"},
                                                                   {"title": "seed", "sources": ["user_seed"]}]
        kept, dropped = pool.prefilter(rows, GROUPS, cap=3)
        titles = [r["title"] for r in kept]
        self.assertIn("seed", titles)
        self.assertIn("self-evolving harness", titles)
        self.assertEqual(dropped, 9)

    def test_lexical_fallback_and_recall_check(self):
        scored = pool.lexical_scores([{"title": "x"}, {"title": "self-evolving harness"}], GROUPS)
        self.assertEqual((scored[0]["jev_rank"], scored[0]["ranker"]), (1, "lexical"))
        recall = pool.recall_check(["arXiv:2610.22740", "2610.14224"],
                                   [{"title": "t", "doi": "10.48550/arxiv.2610.14224"}],
                                   [{"title": "t", "doi": "10.48550/arxiv.2610.14224"}])
        self.assertEqual((len(recall["found"]), recall["missing"][0]["paper"]), (1, "arXiv:2610.22740"))

    def test_rerank_prioritises_and_interleaves_clusters(self):
        rows = [{"title": str(i)} for i in range(6)] + [{"title": "F", "track_excluded": True}]
        sheet = {"0": {"priority": 3}, "1": {"priority": 1, "cluster": "A"}, "2": {"priority": 1, "cluster": "A"},
                 "3": {"priority": 1, "cluster": "B"}}
        out = pool.apply_rerank(rows, sheet, window=4)
        self.assertEqual([r["title"] for r in out], ["1", "3", "2", "0", "4", "5", "F"])
        self.assertEqual([r["rank_position"] for r in out], list(range(7)))
        with self.assertRaises(ValueError):
            pool.apply_rerank(rows, {"0": {"priority": 7}}, window=4)


class ReadingAndSnowballTest(unittest.TestCase):
    def make_task(self, directory):
        task = Path(directory)
        texts = []
        for i in range(3):
            path = task / "pdf_text" / f"{i}.txt"
            path.parent.mkdir(exist_ok=True)
            body = (common.PAGE_MARK.format(n=1) + f"Paper {i} studies self-evolving harnesses with frozen weights here."
                    + common.PAGE_MARK.format(n=2) + "References\nSmith. Harness evolution. arXiv:2609.11111, 2026.")
            path.write_text(body, encoding="utf-8")
            texts.append(path)
        (task / "plan.json").write_text(json.dumps({"topic": "t", "inclusion": ["a"], "exclusion": ["b"]}), encoding="utf-8")
        (task / "fulltext_manifest.json").write_text(json.dumps({"records": [
            {"idx": i, "status": "ok", "text_path": str(p), "pages": 10 * (i + 1)} for i, p in enumerate(texts)]}), encoding="utf-8")
        with (task / "judgment_sheet.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["idx", "record_id", "title", "text_available", "read_scope",
                                                        "decision", "paper_role", "evidence_quote", "evidence_page"])
            writer.writeheader()
            for i in range(3):
                writer.writerow({"idx": i, "record_id": f"r{i}", "title": f"P{i}", "text_available": "yes"})
        return task

    def test_plan_is_stable_check_corrects_pages_and_merge_fills_sheet(self):
        with tempfile.TemporaryDirectory() as directory:
            task = self.make_task(directory)
            args = type("A", (), {"pages_per_batch": 30, "replan": False})()
            reading.cmd_plan(args, task)
            first = json.loads((task / "reading" / "plan.json").read_text(encoding="utf-8"))
            reading.cmd_plan(args, task)
            self.assertEqual(first, json.loads((task / "reading" / "plan.json").read_text(encoding="utf-8")))
            for batch in first["batches"]:
                rows = [{"idx": i, "decision": "core", "paper_role": ["method_donor"], "reason": "r", "one_line": "o",
                         "cluster": "c", "evidence_page": 9,
                         "evidence_quote": f"Paper {i} studies self-evolving harnesses with frozen weights here."}
                        for i in batch["idx"]]
                (task / "reading" / f"batch_{batch['id']}.json").write_text(json.dumps(rows), encoding="utf-8")
            problems, checked = reading.check_batch(task, first["batches"][0]["id"], first["batches"][0]["idx"])
            self.assertEqual((problems, checked[0]["evidence_page"]), ([], 1))
            reading.cmd_merge(None, task)
            with (task / "judgment_sheet.csv").open(encoding="utf-8-sig", newline="") as stream:
                merged = list(csv.DictReader(stream))
            self.assertEqual({(r["decision"], r["read_scope"], r["evidence_page"]) for r in merged}, {("core", "fulltext", "1")})

    def test_incomplete_batch_blocks_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            task = self.make_task(directory)
            reading.cmd_plan(type("A", (), {"pages_per_batch": 30, "replan": False})(), task)
            with self.assertRaises(SystemExit):
                reading.cmd_merge(None, task)

    def test_reference_section_extraction(self):
        text = snowball.reference_text(["Intro mentions arXiv:2601.00001 in passing.", "References\n[1] A. arXiv:2609.11111"])
        self.assertIn("2609.11111", text)
        self.assertNotIn("2601.00001", text)


class CommunityAndPacksTest(unittest.TestCase):
    def test_counts_and_short_names(self):
        self.assertEqual([clue_import._count(v) for v in ("1.2万", "3k", "56", None)], [12000, 3000, 56, 0])
        self.assertEqual(clue_import.short_name("HarnessDev: Can LLMs Evolve Harnesses?"), "HarnessDev")
        self.assertEqual(clue_import.short_name("A Very Long Title Without Colon Here"), "A Very Long Title Without")

    def test_attach_keeps_only_notes_about_the_paper(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            (task / "ranked.jsonl").write_text(json.dumps({"title": "HarnessDev: Can LLMs Evolve"}) + "\n", encoding="utf-8")
            (task / "clues").mkdir()
            (task / "clues" / "lookup_plan.json").write_text(json.dumps(
                [{"position": 0, "keyword": "HarnessDev", "title": "HarnessDev: Can LLMs Evolve"}]), encoding="utf-8")
            (task / "clues" / "lookup_0.json").write_text(json.dumps([
                {"noteId": "a", "title": "HarnessDev 复现", "desc": "", "likedCount": "10",
                 "comments": [{"content": "held-out 上没提升", "likeCount": "5"}]},
                {"noteId": "b", "title": "周末去哪玩", "desc": "爬山", "likedCount": "99"}], ensure_ascii=False), encoding="utf-8")
            clue_import.attach(task)
            row = json.loads((task / "ranked.jsonl").read_text(encoding="utf-8"))
        self.assertEqual((row["community"]["posts"], row["community"]["line"]), (1, "held-out 上没提升"))

    def test_packs_and_doctor(self):
        self.assertEqual(set(common.list_packs()), {"cs", "general"})
        self.assertIn("ccf", common.load_pack("cs")["grading"])
        with self.assertRaises(ValueError):
            common.load_pack("../../etc")
        status = {"sources": {g["id"]: False for g in common.SOURCE_GUIDE}, "local_model": True,
                  "state": {"dismissed": ["xiaohongshu"]}}
        ids = [item["id"] for item in doctor.ladder(status)]
        self.assertNotIn("xiaohongshu", ids)
        self.assertEqual(ids[0], "openalex")


if __name__ == "__main__":
    unittest.main()
