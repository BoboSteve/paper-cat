#!/usr/bin/env python3
"""Compare two venue catalogs using DOI and normalized-title identity."""
import argparse
import hashlib
import html
import json
import re
import unicodedata
from pathlib import Path


def norm(value):
    value = unicodedata.normalize("NFKC", html.unescape(str(value or ""))).casefold()
    value = re.sub(r"\\(?:ell|boldsymbol|mathbf|textbf|textit|mathrm|text)\b", "", value)
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def doi(row):
    value = str(row.get("doi") or (row.get("externalIds") or {}).get("DOI") or "").lower()
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value).rstrip("/")


def keys(row):
    values = []
    if doi(row):
        values.append("doi:" + doi(row))
    if norm(row.get("title")):
        values.append("title:" + norm(row["title"]))
    return values


def load(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        if not str(row.get("title") or "").strip():
            raise ValueError(f"{path}: record without title")
    return rows


def compare(primary, secondary):
    secondary_keys = {}
    for index, row in enumerate(secondary):
        for key in keys(row):
            secondary_keys.setdefault(key, index)
    matched_secondary = set()
    primary_matches = []
    only_primary = []
    for row in primary:
        match = None
        for key in keys(row):
            if key in secondary_keys:
                match = secondary_keys[key]
                break
        if match is None:
            only_primary.append(row)
        else:
            matched_secondary.add(match)
            primary_matches.append(match)
    only_secondary = [row for index, row in enumerate(secondary) if index not in matched_secondary]
    return {
        "primary_records": len(primary),
        "secondary_records": len(secondary),
        "matched": len(primary_matches),
        "only_primary": only_primary,
        "only_secondary": only_secondary,
    }


def slim(row):
    return {
        "title": row.get("title"),
        "doi": doi(row),
        "url": row.get("url"),
        "source": row.get("source"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", required=True)
    parser.add_argument("--primary-label", default="primary")
    parser.add_argument("--secondary", required=True)
    parser.add_argument("--secondary-label", default="secondary")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-examples", type=int, default=100)
    args = parser.parse_args()
    primary, secondary = load(args.primary), load(args.secondary)
    result = compare(primary, secondary)
    report = {
        "primary": {
            "label": args.primary_label,
            "path": str(Path(args.primary).resolve()),
            "sha256": hashlib.sha256(Path(args.primary).read_bytes()).hexdigest(),
            "records": len(primary),
        },
        "secondary": {
            "label": args.secondary_label,
            "path": str(Path(args.secondary).resolve()),
            "sha256": hashlib.sha256(Path(args.secondary).read_bytes()).hexdigest(),
            "records": len(secondary),
        },
        "matched": result["matched"],
        "count_delta": len(primary) - len(secondary),
        "only_primary_count": len(result["only_primary"]),
        "only_secondary_count": len(result["only_secondary"]),
        "only_primary_examples": [slim(row) for row in result["only_primary"][: args.max_examples]],
        "only_secondary_examples": [slim(row) for row in result["only_secondary"][: args.max_examples]],
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"PATH_A_AUDIT {args.primary_label}={len(primary)} {args.secondary_label}={len(secondary)} "
        f"matched={result['matched']} only={len(result['only_primary'])}/{len(result['only_secondary'])}"
    )


if __name__ == "__main__":
    main()
