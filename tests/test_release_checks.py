"""Zero-hit relaxation, real xiaohongshu-mcp formats, read-only XHS commands, packaging, missing grade tables."""
import datetime
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import clue_import  # noqa: E402
import package_skill  # noqa: E402
import quick  # noqa: E402
import xhs_setup  # noqa: E402

PROTEIN = [["protein structure", "protein folding"], ["diffusion", "generative"],
           ["benchmark", "evaluation"], ["uncertainty", "calibration"]]


class ZeroHitTest(unittest.TestCase):
    def test_core_group_is_never_relaxed(self):
        records = [{"title": "Uncertainty in diffusion benchmarks", "abstract": "calibration evaluation"}]
        self.assertNotEqual(quick.narrowest_group(PROTEIN, records), 0)
        self.assertEqual(quick.narrowest_group(PROTEIN, []), 3)  # ties: last-listed group

    def test_least_present_group_is_relaxed(self):
        records = [{"title": "Protein structure diffusion", "abstract": "uncertainty calibration"}]
        self.assertEqual(quick.narrowest_group(PROTEIN, records), 2)

    def test_empty_arxiv_is_reported_and_retried_without_core_loss(self):
        queries = []

        def arxiv(query, *rest):
            queries.append(query)
            return [] if len(queries) == 1 else [{"source": "arxiv", "title": "x"}]

        with mock.patch.object(quick.sources, "openalex_search", return_value=[]), \
                mock.patch.object(quick.sources, "arxiv_search", side_effect=arxiv):
            today = datetime.date(2026, 11, 15)
            found, report = quick.run_sources(PROTEIN, today, today, 10, {})
        self.assertEqual(report["openalex"]["status"], "empty")
        self.assertEqual(report["arxiv"]["status"], "ok")
        self.assertIn("protein structure", queries[1])
        self.assertNotIn("calibration", queries[1])
        self.assertEqual(report["arxiv"]["relaxed"]["dropped_group"], PROTEIN[3])
        self.assertIn("零命中", quick.source_line("openalex", report["openalex"]))
        self.assertIn("已去掉概念组", quick.source_line("arxiv", report["arxiv"]))

    def test_failed_source_does_not_end_the_run(self):
        with mock.patch.object(quick.sources, "openalex_search", side_effect=OSError("offline")), \
                mock.patch.object(quick.sources, "arxiv_search", return_value=[{"source": "arxiv", "title": "x"}]):
            today = datetime.date(2026, 11, 15)
            found, report = quick.run_sources(PROTEIN[:2], today, today, 10, {})
        self.assertEqual(report["openalex"]["status"], "failed")
        self.assertIn("失败", quick.source_line("openalex", report["openalex"]))
        self.assertEqual(len(found), 1)


class OpenAlexPagingTest(unittest.TestCase):
    def test_page_numbers_without_cursor_and_partial_pages_kept(self):
        import sources
        urls = []

        def fake(url, **kwargs):
            urls.append(url)
            if "page=3" in url:
                raise OSError("quota")
            return {"results": [{"id": f"W{len(urls)}-{i}", "title": f"T{len(urls)}-{i}"} for i in range(200)]}

        with mock.patch.object(sources, "http_json", side_effect=fake):
            records = sources.openalex_search("q", max_results=1000, settings={})
        self.assertEqual(len(records), 400)
        self.assertTrue(all("cursor" not in url for url in urls))
        with mock.patch.object(sources, "http_json", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                sources.openalex_search("q", max_results=10, settings={})


SEARCH = {"count": 2, "feeds": [
    {"id": "n1", "xsecToken": "t1", "noteCard": {"displayTitle": "HarnessDev 论文速递", "user": {"nickname": "a"},
                                                 "interactInfo": {"likedCount": "1.2万", "collectedCount": "30", "commentCount": "4"}}},
    {"id": "n2", "xsecToken": "t2", "noteCard": {"displayTitle": "其他", "interactInfo": {}}}]}
DETAIL = {"feed_id": "n1", "data": {"note": {"noteId": "n1", "title": "HarnessDev 论文速递",
                                             "desc": "🤖 HarnessDev：自动改进 harness\n[一R] R-Quest：强化\n#话题",
                                             "time": 1760000000000, "interactInfo": {"likedCount": "5"},
                                             "user": {"nickname": "a"}},
                                    "comments": {"list": [{"content": "好文"}], "cursor": "", "hasMore": False}}}


class XhsFormatsTest(unittest.TestCase):
    def test_search_and_detail_payloads(self):
        notes = {n["note_id"]: n for n in clue_import.notes_from_mcp({"search": SEARCH, "details": [DETAIL]})}
        self.assertEqual(set(notes), {"n1", "n2"})
        self.assertIn("R-Quest", notes["n1"]["text"])  # detail wins over the search card
        self.assertEqual([c["text"] for c in notes["n1"]["comments"]], ["好文"])
        self.assertEqual(notes["n2"]["xsec_token"], "t2")
        self.assertEqual(clue_import._count("1.2万"), 12000)

    def test_paper_names_from_list_lines(self):
        self.assertEqual(clue_import.strip_list_marker("[一R] R-Quest"), "R-Quest")
        self.assertEqual(clue_import.paper_names(DETAIL["data"]["note"]["desc"]), ["HarnessDev", "R-Quest"])
        self.assertEqual(clue_import.paper_names("今天读了一篇很好的论文\nhttp://x.y"), [])

    def test_seeds_merge_across_notes_and_ids(self):
        seeds = clue_import.merge_seeds([
            {"title": "HarnessDev: X", "doi": "10.48550/arxiv.1", "clue_note_url": "u1", "clue_in_comments_only": True},
            {"title": "HarnessDev: X", "doi": "10.1/pub", "clue_note_url": "u2", "clue_in_comments_only": False},
            {"title": "Other", "clue_note_url": "u1"}])
        self.assertEqual(len(seeds), 2)
        self.assertEqual(seeds[0]["mention_count"], 2)
        self.assertFalse(seeds[0]["clue_in_comments_only"])


def tool_result(value):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}


class XhsCommandsTest(unittest.TestCase):
    def test_search_payload_uses_read_only_tools_only(self):
        calls = []

        def fake(port, name, arguments=None):
            calls.append(name)
            return tool_result(SEARCH if name == "search_feeds" else DETAIL)

        with mock.patch.object(xhs_setup, "call_tool", side_effect=fake), mock.patch.object(xhs_setup.time, "sleep"):
            payload = xhs_setup.search_payload(18060, "HarnessDev", details=5, must_mention="harnessdev")
        self.assertEqual(calls, ["search_feeds", "get_feed_detail"])  # n2 does not mention the paper
        self.assertEqual(len(payload["details"]), 1)
        self.assertTrue(set(calls) <= {"search_feeds", "get_feed_detail"})

    def test_logout_removes_saved_login(self):
        with tempfile.TemporaryDirectory() as tool:
            for name in ("cookies.json", "cookies.previous.json"):
                Path(tool, name).write_text("{}", encoding="utf-8")
            with mock.patch.object(xhs_setup, "stop_service", return_value="服务未运行"):
                message = xhs_setup.logout(tool, 18060)
            self.assertFalse(any(Path(tool).glob("cookies*.json")))
            self.assertIn("cookies.json", message)

    def test_bad_arguments_are_rejected(self):
        for args in (["--search", "x"], ["--check", "--details", "50"], ["--check", "--port", "80"],
                     ["--check", "--allow-defender-exclusion"]):
            result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "xhs_setup.py"), *args],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 2, args)


class PackageTest(unittest.TestCase):
    def test_machine_files_are_excluded_and_personal_data_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            for name in ("SKILL.md", "runtime.local.json", "cookies.json", ".env", "scripts/__pycache__/a.pyc",
                         "scripts/ok.py", "logs/run.log"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("x", encoding="utf-8")
            names = {str(rel).replace("\\", "/") for _, rel in package_skill.included(root)}
            self.assertEqual(names, {"SKILL.md", "scripts/ok.py"})
            # assembled at runtime so this test file itself passes the package scan
            user_path, mail = "C:" + "\\Users\\" + "alice\\x", "me" + "@" + "uni.edu"
            (root / "scripts/ok.py").write_text(f'p = "{user_path}"\nmail = "{mail}"\nfake = "a@x.org"\n',
                                                encoding="utf-8")
            problems = package_skill.scan(package_skill.included(root), ["secretlab"])
            self.assertEqual(len(problems), 2, problems)
            (root / "SKILL.md").write_text("built by SecretLab", encoding="utf-8")
            self.assertTrue(any("forbidden" in p for p in package_skill.scan(package_skill.included(root), ["secretlab"])))

    def test_frontmatter_version(self):
        meta = package_skill.frontmatter((ROOT / "SKILL.md").read_text(encoding="utf-8"))
        self.assertTrue(meta["version"] and meta["name"] == "paper-cat")


class Python39Test(unittest.TestCase):
    def test_no_path_text_newline_argument(self):
        """Path.write_text/read_text(newline=) is 3.10+/3.13+; the skill promises Python 3.9."""
        import ast
        offenders = []
        for path in (ROOT / "scripts").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("write_text", "read_text")
                        and any(k.arg == "newline" for k in node.keywords)):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])


class ManualPdfTest(unittest.TestCase):
    def test_user_supplied_pdf_wins_over_downloads(self):
        import download
        with tempfile.TemporaryDirectory() as out:
            (Path(out) / "manual").mkdir()
            (Path(out) / "manual" / "3.pdf").write_bytes(b"%PDF-1.7 user copy")

            def never(url):
                raise AssertionError("must not download when the user supplied the PDF")

            item = download.fetch_record(3, {"title": "T", "doi": "10.1/x"}, out, {}, fetch=never)
            self.assertEqual((item["status"], item["channel"]), ("ok", "manual"))
            (Path(out) / "manual" / "4.pdf").write_bytes(b"<html>not a pdf</html>")
            item = download.fetch_record(4, {"title": "T"}, out, {}, fetch=lambda url: b"")
            self.assertEqual(item["status"], "failed")


class GbkPipeTest(unittest.TestCase):
    def test_entry_points_survive_a_gbk_pipe(self):
        """Chinese Windows hands agents a GBK pipe; printing ✓ or a paper title must not crash."""
        import os
        env = {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}
        env["PYTHONIOENCODING"] = "gbk"
        for args in (["doctor.py"], ["package_skill.py", "--check"], ["install_skill.py", "--target", "claude", "--dry-run"]):
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / args[0]), *args[1:]], env=env,
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, (args, result.stderr.decode("utf-8", "replace")[-300:]))
        with open(os.devnull, "w") as null:  # NUL looks like a terminal to Python on Windows
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "doctor.py")], env=env, stdout=null,
                                    stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace")[-300:])


class MissingGradeTablesTest(unittest.TestCase):
    def test_rank_continues_without_registry_when_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            row = {"title": "A paper", "venue": "ACL", "record_type": "preprint", "jev_rank": 1, "status": "ok"}
            (tmp / "in.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            base = [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "rank.py"), "--candidates", str(tmp / "in.jsonl"),
                    "--cache-dir", str(tmp / "empty"), "--out", str(tmp / "out.jsonl")]
            strict = subprocess.run(base, capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(strict.returncode, 2)
            relaxed = subprocess.run(base + ["--allow-missing-registry"], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace")
            self.assertEqual(relaxed.returncode, 0, relaxed.stdout + relaxed.stderr)
            out = json.loads((tmp / "out.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue(out["grade_tables_missing"])
            self.assertFalse(out["ccf_grade"])


if __name__ == "__main__":
    unittest.main()
