#!/usr/bin/env python3
"""Create a review sheet for the final Agent judgment stage."""
import argparse
import csv
import json
from pathlib import Path


JUDGMENT_FIELDS = ["missing_fulltext_priority", "manual_retrieval_note", "decision", "paper_role", "relevance",
                   "reason", "one_line", "cluster", "evidence_quote", "evidence_page", "reading_notes", "subtopics"]
FIELDS = ["idx", "record_id", "jev_rank", "jev_score", "year", "scope_status", "scope_date", "title", "abstract",
          "venue", "track", "ccf_grade", "doi_or_arxiv", "url", "sources", "query_ids",
          "text_available", "read_scope", "fulltext_path", "fulltext_status", "fulltext_error", *JUDGMENT_FIELDS]


def load_previous(path):
    """Existing sheet rows keyed by (idx, record_id), so judgments survive a regenerated sheet."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return {(row.get("idx", ""), row.get("record_id", "")): row for row in csv.DictReader(stream)
                if (row.get("decision") or "").strip()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ranked", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--fulltext-manifest", help="defaults to fulltext_manifest.json beside ranked")
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-md", required=True)
    parser.add_argument("--idx", help="comma-separated rank positions to judge (overrides Top K, e.g. after user edits)")
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    selected_k = int(plan.get("jev", {}).get("selected_k") or len(Path(args.ranked).read_text(encoding="utf-8").splitlines()))
    all_rows = [
        json.loads(line)
        for line in Path(args.ranked).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.idx:
        wanted = [int(value) for value in args.idx.split(",") if value.strip()]
        selected = [(index, all_rows[index]) for index in wanted if 0 <= index < len(all_rows)]
    else:
        selected = [
            (index, row)
            for index, row in enumerate(all_rows)
            if (index < selected_k and not row.get("track_excluded")) or row.get("status") == "flagged"
        ]
    manifest_path = Path(args.fulltext_manifest) if args.fulltext_manifest else Path(args.ranked).parent / "fulltext_manifest.json"
    if not manifest_path.exists():
        parser.error(f"fulltext manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fulltext = {item["idx"]: item for item in manifest.get("records") or [] if isinstance(item.get("idx"), int)}
    csv_path = Path(args.out_csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    previous = load_previous(csv_path)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for index, row in selected:
            full = fulltext.get(index) or {}
            available = full.get("status") == "ok" and bool(full.get("text_path"))
            record_id = row.get("canonical_record_id") or row.get("record_id") or ""
            # Re-running fetch must never wipe judgments already written for the same paper.
            kept = previous.get((str(index), record_id), {})
            writer.writerow({**{field: kept.get(field, "") for field in JUDGMENT_FIELDS}, **{
                "idx": index,
                "record_id": row.get("canonical_record_id") or row.get("record_id") or "",
                "jev_rank": row.get("jev_rank"),
                "jev_score": row.get("jev_score"),
                "year": row.get("year"),
                "scope_status": row.get("scope_status"),
                "scope_date": row.get("scope_date"),
                "title": row.get("title"),
                "abstract": row.get("abstract"),
                "venue": row.get("venue") or row.get("claimed_venue"),
                "track": row.get("track"),
                "ccf_grade": row.get("ccf_grade") or "",
                "doi_or_arxiv": row.get("doi") or (row.get("externalIds") or {}).get("ArXiv", ""),
                "url": row.get("url") or row.get("pdf_url"),
                "sources": ";".join(row.get("sources") or [row.get("source") or ""]),
                "query_ids": ";".join(row.get("query_ids") or [row.get("query_id") or ""]),
                "text_available": "yes" if available else "no",
                # Left blank on purpose: the reader sets read_scope=fulltext only after actually reading.
                "read_scope": "" if available else "pending_fulltext",
                "fulltext_path": full.get("text_path") or "",
                "fulltext_status": full.get("status") or "missing",
                "fulltext_error": full.get("error") or "",
            }, **({"read_scope": kept["read_scope"]} if kept.get("read_scope") else {})})
    md = [
        f"# Final Agent judgment sheet ({len(selected)} records)",
        "",
        "Allowed decisions: `core`, `periphery`, `exclude`, `pending`, `merged`.",
        "Read the full text at `fulltext_path` before deciding, then set `read_scope=fulltext`. The abstract is navigation only.",
        "Use `paper_role`: direct_competitor, method_donor, failure_warning, data_eval_support.",
        "If full text is unavailable but the abstract clearly shows very high relevance, set `missing_fulltext_priority=high` and briefly explain the manual retrieval reason. The item remains pending, never core.",
        "Copy `evidence_quote` verbatim from the full text (one or two sentences) and give `evidence_page`; the importer checks it against the extracted text and rejects paraphrases. Jev score is not a decision.",
        "",
    ]
    for index, row in selected:
        md.extend([
            f"## {index}. {row.get('title')}",
            "",
            f"- Jev: {row.get('jev_rank')} / score {row.get('jev_score')}",
            f"- Year / scope: {row.get('year')} / {row.get('scope_status')} ({row.get('scope_date')})",
            f"- Sources: {', '.join(row.get('sources') or [row.get('source') or ''])}",
            f"- Queries: {', '.join(row.get('query_ids') or [row.get('query_id') or ''])}",
            f"- Venue: {row.get('venue', '')}",
            f"- ID: {row.get('doi') or (row.get('externalIds') or {}).get('ArXiv', '')}",
            f"- URL: {row.get('url') or row.get('pdf_url') or ''}",
            f"- Read scope: {fulltext.get(index, {}).get('status', 'missing')} / `{fulltext.get(index, {}).get('text_path', '')}`",
            "",
            str(row.get("abstract") or "(missing abstract)"),
            "",
        ])
    md_path = Path(args.out_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"JUDGMENT_SHEET rows={len(selected)} -> {csv_path}")


if __name__ == "__main__":
    main()
