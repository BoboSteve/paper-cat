#!/usr/bin/env python3
"""Import and validate final Agent judgments from the judgment sheet CSV.

Besides field checks, every non-pending decision must quote the full text: the quote is located
in the extracted page texts (tolerating line breaks and ligatures, not paraphrase), and its page
is recorded. Invented or paraphrased quotes are rejected (``--quote-check strict``) or kept with a
visible warning (``--quote-check warn``).
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import locate_quote, read_json, split_pages  # noqa: E402
from progress_dashboard import update as update_progress  # noqa: E402

DECISIONS = {"core", "periphery", "exclude", "pending", "merged"}
PAPER_ROLES = {"direct_competitor", "method_donor", "failure_warning", "data_eval_support", "uncertain"}


def split(value):
    return [item.strip() for item in str(value or "").replace("|", ";").split(";") if item.strip()]


def verify_quote(row, cache):
    path = str(row.get("fulltext_path") or "").strip()
    if not path or not Path(path).exists():
        return {"found": False, "ratio": 0.0, "page": None, "reason": "fulltext_path does not exist"}
    if path not in cache:
        cache[path] = split_pages(Path(path).read_text(encoding="utf-8", errors="replace"))
    result = locate_quote(row.get("evidence_quote"), cache[path])
    claimed = str(row.get("evidence_page") or "").strip()
    if result["found"] and claimed.isdigit() and result["page"] and int(claimed) != result["page"]:
        result["page_note"] = f"quote found on page {result['page']}, sheet said {claimed}"
    return result


def validate(rows, ranked_count, quote_check="strict"):
    """Return (records, problems). problems are strings; records are judgments ready to write."""
    records, problems, seen, cache = [], [], set(), {}
    for row in rows:
        decision = str(row.get("decision") or "").strip()
        if not decision:
            continue
        try:
            idx = int(row["idx"])
        except (KeyError, ValueError):
            problems.append(f"row without a valid idx: {row.get('title', '')[:60]}")
            continue
        errors = []
        if idx in seen:
            errors.append("duplicate idx")
        if not 0 <= idx < ranked_count:
            errors.append("idx out of range")
        if decision not in DECISIONS:
            errors.append(f"invalid decision {decision!r}")
        read_scope = str(row.get("read_scope") or "").strip()
        missing_priority = str(row.get("missing_fulltext_priority") or "").strip().lower()
        if missing_priority not in {"", "high"}:
            errors.append("missing_fulltext_priority must be empty or high")
        if missing_priority == "high" and not str(row.get("manual_retrieval_note") or "").strip():
            errors.append("high-priority missing full text needs manual_retrieval_note")
        final = decision != "pending"
        if final:
            if read_scope != "fulltext":
                errors.append("final decision requires read_scope=fulltext (set it after reading the paper)")
            if missing_priority == "high":
                errors.append("missing full text cannot receive a final decision")
            for field in ("fulltext_path", "evidence_quote", "reason"):
                if not str(row.get(field) or "").strip():
                    errors.append(f"{field} required unless pending")
        roles = split(row.get("paper_role"))
        bad_roles = [role for role in roles if role not in PAPER_ROLES]
        if bad_roles:
            errors.append(f"invalid paper_role {bad_roles}")
        if final and not roles:
            errors.append("paper_role required unless pending")
        evidence = None
        if final and not errors:
            evidence = verify_quote(row, cache)
            if not evidence["found"]:
                message = (f"evidence_quote not found in the full text (best match {evidence['ratio']:.0%}"
                           f"{', page ' + str(evidence['page']) if evidence.get('page') else ''}); copy the sentence verbatim")
                if quote_check == "strict":
                    errors.append(message)
                else:
                    evidence["warning"] = message
        if errors:
            problems.extend(f"idx {idx}: {error}" for error in errors)
            continue
        seen.add(idx)
        records.append({
            "idx": idx,
            "record_id": str(row.get("record_id") or "").strip() or None,
            "decision": decision,
            "read_scope": read_scope or "pending_fulltext",
            "fulltext_path": str(row.get("fulltext_path") or "").strip(),
            "fulltext_status": str(row.get("fulltext_status") or "").strip(),
            "fulltext_error": str(row.get("fulltext_error") or "").strip(),
            "missing_fulltext_priority": missing_priority or None,
            "manual_retrieval_note": str(row.get("manual_retrieval_note") or "").strip(),
            "paper_roles": roles,
            "relevance": str(row.get("relevance") or "unknown").strip(),
            "reason": str(row.get("reason") or "").strip(),
            "evidence_quote": str(row.get("evidence_quote") or "").strip(),
            "evidence_page": (evidence or {}).get("page"),
            "evidence_verified": bool(evidence and evidence["found"]),
            "evidence_match_ratio": (evidence or {}).get("ratio"),
            "evidence_warning": (evidence or {}).get("warning") or (evidence or {}).get("page_note"),
            "reading_notes": str(row.get("reading_notes") or "").strip(),
            "one_line": str(row.get("one_line") or "").strip()[:200],
            "cluster": str(row.get("cluster") or "").strip()[:20],
            "subtopics": split(row.get("subtopics")),
            "evidence_basis": "fulltext" if final else "pending_fulltext",
        })
    return records, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, help="judgment_sheet.csv")
    parser.add_argument("--ranked", required=True)
    parser.add_argument("--plan", help="plan.json; supplies the criteria version and inclusion/exclusion rules")
    parser.add_argument("--criteria", help="optional extra criteria text (Markdown)")
    parser.add_argument("--judged-by", default=os.environ.get("PAPERCAT_JUDGE") or os.environ.get("CS_PAPER_SEARCH_JUDGE"),
                        help="who read and judged, e.g. 'Claude Code (claude-opus-5-5)' or 'Codex (gpt-5)'; "
                             "or set PAPERCAT_JUDGE")
    parser.add_argument("--quote-check", choices=("strict", "warn"), default="strict")
    parser.add_argument("--progress-task", help="task directory for dashboard updates")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not args.judged_by:
        parser.error("--judged-by is required: record the agent/harness and model that actually read the papers")
    ranked_count = sum(1 for line in Path(args.ranked).read_text(encoding="utf-8").splitlines() if line.strip())
    with Path(args.input).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    plan = read_json(args.plan, {}) if args.plan else {}
    records, problems = validate(rows, ranked_count, args.quote_check)
    if problems:
        print("JUDGMENT_IMPORT_REJECTED", file=sys.stderr)
        for problem in problems:
            print("  " + problem, file=sys.stderr)
        raise SystemExit(2)
    if not records:
        raise SystemExit("no judgments imported: fill `decision` in the sheet first")
    criteria = {
        "topic": plan.get("topic"),
        "inclusion": plan.get("inclusion"),
        "exclusion": plan.get("exclusion"),
        "extra": Path(args.criteria).read_text(encoding="utf-8") if args.criteria else None,
    }
    meta = {
        "criteria_version": plan.get("criteria_version") or (
            f"{plan.get('topic', 'task')}@{plan.get('created_at', '')[:10]}" if plan else "unspecified"),
        "criteria": criteria,
        "judged_by": args.judged_by,
        "quote_check": args.quote_check,
    }
    for record in records:
        record["verdict_source"] = args.judged_by
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"meta": meta}, ensure_ascii=False) + "\n")
        for item in records:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    counts = Counter(item["decision"] for item in records)
    verified = sum(item["evidence_verified"] for item in records)
    print(f"JUDGMENTS_IMPORTED records={len(records)} decisions={dict(counts)} quotes_verified={verified} -> {out_path}")
    if args.progress_task:
        update_progress(
            args.progress_task, stage="final", status="degraded" if counts.get("pending") else "done",
            completed=len(records), total=len(rows), errors=0,
            message=f"Final judgments imported: {dict(counts)}",
            summary=f"Final judgments complete: {dict(counts)}; verified quotes {verified}",
        )


if __name__ == "__main__":
    main()
