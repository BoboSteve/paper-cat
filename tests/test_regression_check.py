import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "regression_check.py"


def build_task(task, path="B", judgment=None, ranked=None, official=False):
    (task / "plan.json").write_text(json.dumps({
        "topic": "agent skills", "path": path, "jev": {"selected_k": 1},
        "regression": {"scope": "target_topic_only", "subtopics": ["skills"]},
    }), encoding="utf-8")
    (task / "preflight.json").write_text(json.dumps({"status": "degraded", "required_ok": True}), encoding="utf-8")
    (task / "psm_meta.json").write_text(json.dumps({"queries": [{"status": "ok"}]}), encoding="utf-8")
    if official:
        (task / "official").mkdir()
        (task / "official" / "acl.summary.json").write_text(json.dumps({"records": 1, "track_policy": "main_only"}), encoding="utf-8")
    (task / "merge_report.json").write_text(json.dumps({"counts": {"candidates": 1}}), encoding="utf-8")
    rows = ranked or [{"status": "ok", "jev_score": 0.9, "title": "Example", "venue_evidence": "acl_anthology_id", "ccf_grade": "B"}]
    (task / "ranked.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    judgment = judgment if judgment is not None else {
        "idx": 0, "decision": "core", "read_scope": "fulltext", "evidence_quote": "Evidence sentence here.",
        "evidence_verified": True, "subtopics": ["skills"],
    }
    lines = [{"meta": {"criteria_version": "v1", "judged_by": "Claude Code (test)"}}] + ([judgment] if judgment else [])
    (task / "judgments.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    (task / "fulltext_manifest.json").write_text(json.dumps({"records": [{"idx": 0, "status": "ok"}]}), encoding="utf-8")
    (task / "report.html").write_text("report", encoding="utf-8")
    (task / "results.json").write_text("{}", encoding="utf-8")


def run(task):
    result = subprocess.run([sys.executable, str(SCRIPT), "--task", str(task)], capture_output=True, text=True, check=False)
    report = json.loads((task / "regression_report.json").read_text(encoding="utf-8"))
    return result.returncode, report["status"], {item["name"]: item["status"] for item in report["checks"]}


class RegressionCheckTest(unittest.TestCase):
    def test_path_b_without_official_catalog_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            build_task(Path(directory))
            code, status, checks = run(Path(directory))
            self.assertEqual((code, status), (0, "pass"), checks)
            self.assertEqual(checks["official_catalogs"], "skip")

    def test_path_a_requires_a_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            build_task(Path(directory), path="A")
            self.assertEqual(run(Path(directory))[2]["official_catalogs"], "fail")
        with tempfile.TemporaryDirectory() as directory:
            build_task(Path(directory), path="A", official=True)
            self.assertEqual(run(Path(directory))[0], 0)

    def test_no_core_paper_is_a_warning_not_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            build_task(Path(directory), judgment={"idx": 0, "decision": "exclude", "read_scope": "fulltext",
                                                  "evidence_quote": "x y z w", "evidence_verified": True})
            code, status, checks = run(Path(directory))
            self.assertEqual((code, status, checks["subtopic_coverage"]), (0, "warn", "warn"))

    def test_unverified_quote_warns_and_excluded_track_core_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            build_task(Path(directory), judgment={"idx": 0, "decision": "core", "read_scope": "fulltext",
                                                  "evidence_quote": "q", "evidence_verified": False, "subtopics": ["skills"]})
            self.assertEqual(run(Path(directory))[2]["final_judgments"], "warn")
        with tempfile.TemporaryDirectory() as directory:
            build_task(Path(directory), ranked=[{"status": "ok", "title": "F", "track_excluded": True}])
            code, _, checks = run(Path(directory))
            self.assertEqual((code, checks["final_judgments"]), (1, "fail"))


if __name__ == "__main__":
    unittest.main()
