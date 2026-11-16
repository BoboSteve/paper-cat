#!/usr/bin/env python3
"""Collapse explicitly reviewed version groups while preserving audit evidence."""
import argparse
import json
from pathlib import Path


def completeness(row):
    return sum(
        bool(row.get(field))
        for field in ("title", "abstract", "venue", "doi", "year", "pdf_url", "openAccessPdf")
    )


def choose_representative(rows):
    scored = [row for row in rows if isinstance(row.get("jev_score"), (int, float))]
    if scored:
        return max(scored, key=lambda row: (row["jev_score"], -row.get("input_index", 0)))
    return max(rows, key=completeness)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--groups", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    source_rows = [
        json.loads(line)
        for line in Path(args.input).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    groups = json.loads(Path(args.groups).read_text(encoding="utf-8"))
    by_id = {}
    for row in source_rows:
        for value in (row.get("record_id"), row.get("canonical_record_id"), row.get("doi")):
            if value:
                by_id[str(value)] = row
                by_id[str(value).removeprefix("doi:")] = row
    consumed = set()
    additions = {}
    group_report = []
    for group in groups:
        if group.get("action") != "merge":
            group_report.append({**group, "members_found": 0, "representative": None})
            continue
        members = []
        for record_id in group["record_ids"]:
            row = by_id.get(record_id) or by_id.get(record_id.removeprefix("doi:"))
            if row is not None and row not in members:
                members.append(row)
        group_report.append({
            "group_id": group["group_id"],
            "members_found": len(members),
            "member_ids": [row.get("record_id") for row in members],
        })
        if len(members) < 2:
            continue
        representative = dict(choose_representative(members))
        representative["version_group"] = group["group_id"]
        representative["version_members"] = [
            {
                "record_id": row.get("record_id"),
                "title": row.get("title"),
                "year": row.get("year"),
                "venue": row.get("venue"),
                "jev_score": row.get("jev_score"),
            }
            for row in members
        ]
        additions[representative["record_id"]] = representative
        consumed.update(row.get("record_id") for row in members)
        group_report[-1]["representative"] = representative["record_id"]
    output = []
    for row in source_rows:
        if row.get("record_id") not in consumed:
            output.append(row)
    output.extend(additions.values())
    if output and all(isinstance(row.get("jev_rank"), int) for row in output):
        output.sort(key=lambda row: row["jev_rank"])
    elif output and any(isinstance(row.get("jev_score"), (int, float)) for row in output):
        output.sort(key=lambda row: row.get("jev_score", -1), reverse=True)
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as stream:
        for row in output:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = {
        "input_records": len(source_rows),
        "output_records": len(output),
        "collapsed": len(source_rows) - len(output),
        "groups": group_report,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"VERSION_GROUPS input={len(source_rows)} output={len(output)} collapsed={report['collapsed']}")


if __name__ == "__main__":
    main()
