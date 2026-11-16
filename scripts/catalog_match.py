#!/usr/bin/env python3
"""Match candidates to official venue catalogs without changing their relevance decision."""
import argparse
import hashlib
import html
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path


def norm(value):
    value = unicodedata.normalize("NFKC", html.unescape(str(value or ""))).casefold()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def record_doi(row):
    value = str(row.get("doi") or (row.get("externalIds") or {}).get("DOI") or "").lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value).rstrip("/")
    return value if value.startswith("10.") else ""


def record_acl(row):
    values = [row.get("acl_id"), (row.get("externalIds") or {}).get("ACL")]
    for key in ("url", "pdf_url", "ee"):
        match = re.search(r"aclanthology\.org/([^/?#]+)", str(row.get(key) or ""), re.I)
        if match:
            values.append(match.group(1))
    return next((str(value).lower() for value in values if value), "")


def record_openreview(row):
    values = [row.get("openreview_id"), row.get("forum"), (row.get("externalIds") or {}).get("OpenReview")]
    for key in ("url", "pdf_url", "ee"):
        match = re.search(r"openreview\.net/(?:forum|pdf)\?id=([^&#]+)", str(row.get(key) or ""), re.I)
        if match:
            values.append(match.group(1))
    return next((str(value) for value in values if value), "")


def keys(row):
    result = []
    for prefix, value in (
        ("doi", record_doi(row)),
        ("acl", record_acl(row)),
        ("openreview", record_openreview(row)),
        ("title", norm(row.get("title"))),
    ):
        if value:
            result.append(f"{prefix}:{value.casefold()}")
    return result


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def catalog_indexes(catalogs):
    index = {}
    rows = {}
    for label, path in catalogs:
        catalog_rows = load_jsonl(path)
        rows[label] = catalog_rows
        for row_index, row in enumerate(catalog_rows):
            for key in keys(row):
                index.setdefault(key, []).append((label, row_index))
    return index, rows


def match_candidate(row, index, catalogs):
    for key in keys(row):
        matches = index.get(key, [])
        if not matches:
            continue
        label, row_index = matches[0]
        return label, catalogs[label][row_index], key
    return None


def apply_match(row, label, official, key):
    result = dict(row)
    changed = []
    for field in ("venue", "venueid", "track", "record_type", "url", "pdf_url"):
        value = official.get(field)
        if value and not result.get(field):
            result[field] = value
            changed.append(field)
    if official.get("abstract") and len(str(official["abstract"])) > len(str(result.get("abstract") or "")):
        result["abstract"] = official["abstract"]
        changed.append("abstract")
    if official.get("authors") and not result.get("authors"):
        result["authors"] = official["authors"]
        changed.append("authors")
    matches = result.get("catalog_matches") or []
    evidence = {
        "catalog": label,
        "match_key": key,
        "record_id": official.get("record_id"),
        "title": official.get("title"),
        "changed_fields": changed,
    }
    unique = {
        (item.get("catalog"), item.get("record_id"), item.get("match_key")): item
        for item in matches + [evidence]
    }
    result["catalog_matches"] = list(unique.values())
    return result


def parse_input(value):
    if "=" not in value:
        raise ValueError("--catalog must use LABEL=PATH")
    label, path = value.split("=", 1)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", label):
        raise ValueError("catalog label contains invalid characters")
    return label, path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--catalog", action="append", required=True, help="LABEL=PATH; repeatable")
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    catalogs = [parse_input(value) for value in args.catalog]
    index, catalog_rows = catalog_indexes(catalogs)
    candidates = load_jsonl(args.candidate)
    output = []
    matched = defaultdict(int)
    unmatched = []
    for row in candidates:
        hit = match_candidate(row, index, catalog_rows)
        if hit:
            label, official, key = hit
            output.append(apply_match(row, label, official, key))
            matched[label] += 1
        else:
            output.append(row)
            unmatched.append({"title": row.get("title"), "record_id": row.get("record_id")})
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as stream:
        for row in output:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = {
        "candidate": {
            "path": str(Path(args.candidate).resolve()),
            "sha256": hashlib.sha256(Path(args.candidate).read_bytes()).hexdigest(),
            "records": len(candidates),
        },
        "catalogs": [
            {
                "label": label,
                "path": str(Path(path).resolve()),
                "records": len(catalog_rows[label]),
            }
            for label, path in catalogs
        ],
        "matched": sum(matched.values()),
        "matched_by_catalog": dict(matched),
        "unmatched": len(unmatched),
        "unmatched_examples": unmatched[:100],
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"CATALOG_MATCH matched={report['matched']} unmatched={len(unmatched)}")


if __name__ == "__main__":
    main()
