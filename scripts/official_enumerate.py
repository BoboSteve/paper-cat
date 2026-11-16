#!/usr/bin/env python3
"""Normalize official ACL Anthology and PMLR venue indexes."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path


def now():
    return __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()


def clean_markdown(value):
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    return re.sub(r"\s+", " ", value.replace("\\", "")).strip()


def pages_text(source):
    lines = source.splitlines()
    blocks = []
    current = []
    for line in lines:
        if line.startswith("[pdf](") and current:
            blocks.append(current)
            current = [line]
        elif line.startswith("[pdf]("):
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def parse_acl(source, label, venue, year):
    records = []
    for block in pages_text(source):
        text = "\n".join(block)
        pdf_match = re.search(
            r'\[pdf\]\((https://aclanthology\.org/[^)\s]+\.pdf)(?:\s+"[^"]*")?\)',
            text,
        )
        title_match = re.search(r"\*\*\[([^\]]+)\]\((/[^)]+/)\)\*\*", text)
        if not pdf_match or not title_match:
            continue
        acl_id = title_match.group(2).strip("/")
        title = clean_markdown(title_match.group(1))
        if acl_id.endswith(".0") or title.startswith("Proceedings of "):
            continue
        after_title = text[title_match.end():]
        lines = after_title.splitlines()
        author_index = next(
            (index for index, line in enumerate(lines) if line.startswith("[") and "](/people/" in line),
            None,
        )
        authors = []
        abstract = ""
        if author_index is not None:
            author_parts = []
            cursor = author_index
            while cursor < len(lines) and lines[cursor].strip():
                author_parts.append(lines[cursor].strip())
                cursor += 1
            authors = [
                clean_markdown(value.replace("\\|", "|"))
                for value in "\n".join(author_parts).split("|")
                if value.strip()
            ]
            abstract_parts = []
            for line in lines[cursor:]:
                if line.startswith("[pdf]("):
                    break
                if line.strip() and not line.startswith("[") and not line.startswith("**"):
                    abstract_parts.append(line.strip())
            abstract = clean_markdown(" ".join(abstract_parts))
        records.append({
            "record_id": f"acl:{acl_id}",
            "acl_id": acl_id,
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "year": year,
            "venue": venue,
            "venueid": label,
            "track": "main",
            "record_type": "conference",
            "status": "published",
            "source": "acl_anthology",
            "url": f"https://aclanthology.org/{acl_id}/",
            "pdf_url": pdf_match.group(1),
        })
    return records


def parse_pmlr(source, label, venue, year):
    records = []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", source) if part.strip()]
    for index, paragraph in enumerate(paragraphs):
        if "[abs](" not in paragraph or "Download" not in paragraph or index < 1:
            continue
        citation_block = paragraphs[index - 1]
        citation_line = re.sub(r"\s+", " ", citation_block)
        citation = re.search(r"\*Proceedings of .*?\*, PMLR\s+(\d+):([0-9ivxlcdm-]+)", citation_line)
        if not citation:
            continue
        block_lines = [line.strip() for line in citation_block.splitlines() if line.strip()]
        citation_line_index = next(
            (line_index for line_index, line in enumerate(block_lines) if "*Proceedings of" in line),
            None,
        )
        if citation_line_index is None:
            continue
        same_line_prefix = block_lines[citation_line_index].split("*Proceedings of", 1)[0].strip()
        if same_line_prefix:
            title_line = " ".join(block_lines[:citation_line_index]).strip()
            authors_text = same_line_prefix.rstrip(" ;")
        elif index >= 2:
            title_line = re.sub(r"\s+", " ", paragraphs[index - 2]).strip()
            authors_text = " ".join(block_lines[:citation_line_index]).rstrip(" ;")
        else:
            continue
        if not title_line or title_line.startswith("[") or title_line.startswith("#"):
            continue
        abs_match = re.search(r"\[abs\]\(([^)]+)\)", paragraph)
        pdf_match = re.search(r"\[Download\s+PDF\]\(([^)]+)\)", paragraph)
        if not citation or not abs_match or not pdf_match:
            continue
        authors = [value.strip() for value in authors_text.split(",") if value.strip()]
        title = clean_markdown(title_line)
        records.append({
            "record_id": f"pmlr:{citation.group(1)}:{citation.group(2)}",
            "title": title,
            "authors": authors,
            "abstract": "",
            "year": year,
            "venue": venue,
            "venueid": label,
            "dblp_volume": f"v{citation.group(1)}",
            "pages": citation.group(2),
            "track": "main",
            "record_type": "conference",
            "status": "published",
            "source": "pmlr",
            "url": abs_match.group(1),
            "pdf_url": pdf_match.group(1),
        })
    return records


def fetch_markdown(url, timeout):
    pandoc = shutil.which("pandoc")
    if not pandoc:
        try:
            from pypandoc import get_pandoc_path
            pandoc = get_pandoc_path()
        except (ImportError, OSError):
            pass
    if not pandoc:
        raise RuntimeError("pandoc is required when --url is used; pass a saved Markdown/HTML page with --input otherwise")
    request = urllib.request.Request(url, headers={"User-Agent": "paper-cat/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "page.html"
        source.write_bytes(body)
        result = subprocess.run(
            [pandoc, "-f", "html", "-t", "gfm-raw_html", str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=timeout,
        )
    return result.stdout


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("acl", "pmlr"), required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--input", help="saved Markdown or HTML page")
    parser.add_argument("--url", help="official venue page; converted with pandoc")
    parser.add_argument("--out", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--limit", type=int, help="testing only")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if bool(args.input) == bool(args.url):
        parser.error("provide exactly one of --input or --url")

    if args.input:
        source = Path(args.input).read_text(encoding="utf-8")
        source_ref = str(Path(args.input).resolve())
    else:
        source = fetch_markdown(args.url, args.timeout)
        source_ref = args.url
    parser_fn = parse_acl if args.source == "acl" else parse_pmlr
    records = parser_fn(source, args.label, args.venue, args.year)
    if args.limit:
        records = records[: args.limit]
    if not records:
        parser.error("no records parsed; inspect the official page format")
    output = Path(args.out)
    write_jsonl(output, records)
    summary = {
        "status": "ok",
        "source": args.source,
        "source_ref": source_ref,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "label": args.label,
        "venue": args.venue,
        "year": args.year,
        "track_policy": "main_only",
        "records": len(records),
        "with_abstract": sum(bool(row["abstract"]) for row in records),
        "created_at": now(),
        "output": str(output),
    }
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OFFICIAL_FINISHED source={args.source} records={len(records)} with_abstract={summary['with_abstract']}")


if __name__ == "__main__":
    main()
