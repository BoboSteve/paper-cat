#!/usr/bin/env python3
"""Annotate records against a publication-date window without inventing date precision.

A date is an interval: a day, a month (arXiv IDs), or a whole year (year-only metadata,
OpenAlex YYYY-01-01 placeholders, ACL volumes). Records whose interval lies inside the window are
``in_window``; fully outside is ``out_of_window``; an interval that straddles a window edge is
``boundary`` and is kept for review, never silently dropped.
"""
import argparse
import datetime
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import infer_date, read_jsonl, window_status, write_json, write_jsonl  # noqa: E402,F401


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--start", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--drop-out", action="store_true", help="drop records fully outside the window")
    parser.add_argument("--drop-unknown", action="store_true", help="drop records without any date")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)
    if start > end:
        parser.error("--start is after --end")
    rows = read_jsonl(args.input)
    output, counts, dropped = [], Counter(), Counter()
    for row in rows:
        status, value, precision, source = window_status(row, start, end)
        counts[status] += 1
        item = {**row, "scope_status": status, "scope_date": value,
                "scope_date_precision": precision, "scope_date_source": source}
        if value and not item.get("year"):
            item["year"] = int(value[:4])
        if (status == "out_of_window" and args.drop_out) or (status == "unknown" and args.drop_unknown):
            dropped[status] += 1
            continue
        output.append(item)
    write_jsonl(args.out, output)
    write_json(args.report, {
        "input_records": len(rows), "output_records": len(output), "window": [args.start, args.end],
        "counts": dict(counts), "dropped": dict(dropped),
        "note": "boundary = date known only to month/year and the period straddles a window edge; kept for review",
    })
    print(f"SCOPE_FILTER output={len(output)} counts={dict(counts)} dropped={dict(dropped)}")


if __name__ == "__main__":
    main()
