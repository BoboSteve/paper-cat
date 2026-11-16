#!/usr/bin/env python3
"""Create a user-facing queue for high-priority papers whose full text is missing."""
import argparse
import csv
from pathlib import Path


FIELDS = [
    "idx", "title", "year", "venue", "doi_or_arxiv", "url",
    "fulltext_status", "fulltext_error", "manual_retrieval_note", "abstract",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--out-md", required=True)
    parser.add_argument("--out-csv", required=True)
    args = parser.parse_args()
    with Path(args.sheet).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    selected = [
        row for row in rows
        if row.get("read_scope") == "pending_fulltext"
        and row.get("missing_fulltext_priority", "").strip().lower() == "high"
    ]
    csv_path = Path(args.out_csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in selected)
    lines = [
        "# 需要人工获取全文的高相关论文",
        "",
        f"共 {len(selected)} 篇。它们尚未完成全文判断，不能进入核心清单。",
        "",
    ]
    for number, row in enumerate(selected, 1):
        lines.extend([
            f"## {number}. {row.get('title', '')}",
            "",
            f"- 年份/venue：{row.get('year', '')} / {row.get('venue', '')}",
            f"- DOI/arXiv：{row.get('doi_or_arxiv', '') or '未提供'}",
            f"- 链接：{row.get('url', '') or '未提供'}",
            f"- 获取失败：{row.get('fulltext_error', '') or row.get('fulltext_status', '')}",
            f"- 为什么值得你手动找全文：{row.get('manual_retrieval_note', '') or '摘要显示出很强相关性，需全文确认。'}",
            "",
            "摘要依据：",
            "",
            f"> {row.get('abstract', '')[:1200]}",
            "",
        ])
    md_path = Path(args.out_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"MISSING_FULLTEXT_REPORT records={len(selected)} -> {md_path}")


if __name__ == "__main__":
    main()
