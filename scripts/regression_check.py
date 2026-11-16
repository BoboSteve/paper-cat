#!/usr/bin/env python3
"""Check that a finished task's artifact chain is complete and honest.

Checks adapt to the task: Path B does not need official catalogs, Path A does not need topic
search, a topic may have a single subtopic, and "no paper met the criteria" is a valid outcome
(reported as a warning, never padded with weak core papers). Statuses: pass / warn / skip / fail;
only ``fail`` fails the run.
"""
import argparse
import collections
import json
from pathlib import Path


def load_json(path, required=True):
    if not path.exists():
        if required:
            raise FileNotFoundError(str(path))
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check(name, status, detail):
    if isinstance(status, bool):
        status = "pass" if status else "fail"
    return {"name": name, "status": status, "detail": detail}


def run_checks(task, require_fulltext=False):
    checks = []
    plan = load_json(task / "plan.json")
    path = plan.get("path", "B")
    subtopics = plan.get("regression", {}).get("subtopics") or [plan.get("topic")]
    checks.append(check("plan", bool(plan.get("topic")) and bool(subtopics), {"path": path, "subtopics": subtopics}))

    preflight = load_json(task / "preflight.json", required=False)
    checks.append(check(
        "preflight",
        "warn" if not preflight else bool(preflight.get("required_ok", True)),
        {"status": (preflight or {}).get("status"), "failed": (preflight or {}).get("failed_requirements")},
    ))

    psm_meta = load_json(task / "psm_meta.json", required=False)
    psm_queries = (psm_meta or {}).get("queries") or []
    statuses = collections.Counter(row.get("status") for row in psm_queries)
    if path in {"B", "hybrid"}:
        ok = bool(psm_queries) and statuses["failed"] < len(psm_queries)
        status = "fail" if not ok else ("warn" if statuses["partial"] or statuses["failed"] else "pass")
    else:
        status = "skip" if not psm_queries else "pass"
    checks.append(check("topic_search", status, {"queries": len(psm_queries), "statuses": dict(statuses)}))

    official_dir = task / "official"
    summaries = list(official_dir.glob("*.summary.json")) if official_dir.exists() else []
    catalogs = list(task.glob("*catalog*.jsonl"))
    official_counts = {}
    for summary_path in summaries:
        value = load_json(summary_path)
        official_counts[summary_path.stem] = {"records": value.get("records"), "track_policy": value.get("track_policy")}
    if path in {"A", "hybrid"}:
        ok = (bool(summaries) or bool(catalogs)) and all(
            int(v["records"] or 0) > 0 for v in official_counts.values())
        status = ok
    else:
        status = "skip" if not summaries else "pass"
    checks.append(check("official_catalogs", status, {"summaries": official_counts, "catalog_files": [p.name for p in catalogs]}))

    merge_report = load_json(task / "merge_report.json", required=False)
    merge_counts = (merge_report or {}).get("counts") or {}
    checks.append(check("merge", "warn" if not merge_report else int(merge_counts.get("candidates") or 0) > 0, merge_counts))

    ranked_path = task / "ranked.jsonl"
    ranked = load_jsonl(ranked_path) if ranked_path.exists() else []
    selected_k = int(plan.get("jev", {}).get("selected_k") or 0)
    scored = sum(row.get("status") in {"ok", "flagged"} for row in ranked)
    failed = sum(row.get("status") == "failed" for row in ranked)
    eligible = sum(not row.get("track_excluded") for row in ranked)
    checks.append(check(
        "ranking",
        "fail" if not ranked or scored < min(selected_k, len(ranked)) else ("warn" if failed else "pass"),
        {"ranked": len(ranked), "scored": scored, "failed": failed, "selected_k": selected_k,
         "eligible_after_track_policy": eligible, "track_policy": plan.get("track_policy", "main_only")},
    ))
    graded = sum(bool(row.get("ccf_grade") or row.get("partition")) for row in ranked)
    resolved = sum(bool(row.get("venue_evidence") or row.get("claimed_venue")) for row in ranked)
    checks.append(check("venue_resolution", "pass" if not ranked or resolved else "warn",
                        {"with_venue_evidence": resolved, "graded_or_partitioned": graded, "records": len(ranked)}))

    judgments_path = task / "judgments.jsonl"
    lines = load_jsonl(judgments_path) if judgments_path.exists() else []
    meta = next((row["meta"] for row in lines if "meta" in row), {})
    judgments = [row for row in lines if "idx" in row]
    final = [row for row in judgments if row.get("decision") != "pending"]
    indices = [row.get("idx") for row in judgments]
    unverified = [row["idx"] for row in final if not row.get("evidence_verified")]
    out_of_scope_core = [row["idx"] for row in judgments if row.get("decision") == "core"
                         and 0 <= row["idx"] < len(ranked) and ranked[row["idx"]].get("scope_status") == "out_of_window"]
    excluded_core = [row["idx"] for row in judgments if row.get("decision") == "core"
                     and 0 <= row["idx"] < len(ranked) and ranked[row["idx"]].get("track_excluded")]
    ok = (bool(judgments) and bool(meta.get("judged_by")) and len(indices) == len(set(indices))
          and all(0 <= idx < len(ranked) for idx in indices)
          and all(row.get("read_scope") == "fulltext" for row in final)
          and not out_of_scope_core and not excluded_core)
    status = "fail" if not ok else ("warn" if unverified else "pass")
    checks.append(check("final_judgments", status, {
        "judged_by": meta.get("judged_by"), "judgments": len(judgments), "final": len(final),
        "unverified_quotes": unverified, "out_of_scope_core": out_of_scope_core, "excluded_track_core": excluded_core,
    }))

    subtopic_core = collections.Counter()
    for row in judgments:
        if row.get("decision") == "core":
            for name in row.get("subtopics") or subtopics[:1]:
                subtopic_core[name] += 1
    empty = [name for name in subtopics if not subtopic_core[name]]
    checks.append(check("subtopic_coverage", "warn" if empty else "pass",
                        {"core_per_subtopic": {name: subtopic_core[name] for name in subtopics},
                         "note": "a subtopic without core papers is reported, not padded" if empty else ""}))

    high_missing = [row for row in judgments if row.get("missing_fulltext_priority") == "high"]
    checks.append(check("manual_fulltext_queue",
                        not high_missing or ((task / "missing_fulltext.md").exists() and (task / "missing_fulltext.csv").exists()),
                        {"high_priority": len(high_missing)}))

    fulltext = load_json(task / "fulltext_manifest.json", required=require_fulltext)
    records = (fulltext or {}).get("records") or []
    non_ok = [row.get("idx") for row in records if row.get("status") != "ok"]
    judged_on_bad_text = [row["idx"] for row in final if row["idx"] in non_ok]
    checks.append(check("fulltext",
                        "fail" if judged_on_bad_text else ("warn" if non_ok or not records else "pass"),
                        {"records": len(records), "non_ok": non_ok, "judged_on_unavailable_text": judged_on_bad_text}))

    report_path, results_path = task / "report.html", task / "results.json"
    checks.append(check("report", report_path.exists() and results_path.exists(),
                        {"report_bytes": report_path.stat().st_size if report_path.exists() else 0}))
    return checks, dict(subtopic_core)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task", required=True)
    parser.add_argument("--out", help="defaults to <task>/regression_report.json")
    parser.add_argument("--require-fulltext", action="store_true")
    args = parser.parse_args()
    task = Path(args.task)
    checks, subtopic_core = run_checks(task, args.require_fulltext)
    status = "fail" if any(item["status"] == "fail" for item in checks) else (
        "warn" if any(item["status"] == "warn" for item in checks) else "pass")
    report = {"status": status, "task": str(task.resolve()), "subtopic_core": subtopic_core, "checks": checks}
    output = Path(args.out) if args.out else task / "regression_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"REGRESSION {status} -> {output}")
    for item in checks:
        print(f"  {item['status']:<4} {item['name']}")
    if status == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
