#!/usr/bin/env python3
"""Extract PDF text, verify paper identity, and collect page-level evidence snippets."""
import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PAGE_MARK, read_json, split_pages  # noqa: E402

from progress_dashboard import update as update_progress


def now():
    return __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()


def norm(value):
    return re.sub(r"[^a-z0-9]+", "", html.unescape(str(value or "")).casefold())


def tokens(value):
    return set(re.findall(r"[a-z0-9]+", html.unescape(str(value or "")).casefold()))


def title_match(title, text):
    expected = tokens(title)
    if not expected:
        return 0.0
    return len(expected & tokens(text)) / len(expected)


def pages_from_text(value):
    return split_pages(value)


def evidence_snippets(pages, queries, max_hits=2, radius=220):
    """Reading aids only: keyword-in-context snippets with page numbers."""
    results = []
    for query in queries:
        hits = 0
        pattern = re.compile(re.escape(query), re.I)
        for page_number, page in enumerate(pages, 1):
            for match in pattern.finditer(page):
                start, end = max(0, match.start() - radius), min(len(page), match.end() + radius)
                results.append({"query": query, "page": page_number,
                                "snippet": re.sub(r"\s+", " ", page[start:end]).strip()})
                hits += 1
                if hits >= max_hits:
                    break
            if hits >= max_hits:
                break
    return results


def extract_pages(pdf_path, timeout):
    """Page texts in reading order. PyMuPDF first (pip, every OS), then poppler pdftotext.

    No ``-layout``: layout mode interleaves the two columns of conference papers line by line,
    which breaks every sentence that the final judgment has to quote.
    """
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore  # older PyMuPDF name
        except ImportError:
            pymupdf = None
    if pymupdf is not None:
        with pymupdf.open(str(pdf_path)) as document:
            return [page.get_text("text") for page in document], "pymupdf"
    exe = shutil.which("pdftotext")
    if not exe:
        raise RuntimeError("no PDF text extractor: pip install pymupdf (recommended) or install poppler pdftotext")
    result = subprocess.run([exe, "-enc", "UTF-8", str(pdf_path), "-"], capture_output=True, check=False, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or f"pdftotext failed for {pdf_path}")
    return result.stdout.decode("utf-8", "replace").split(chr(12)), "pdftotext"  # pages end with a form feed


def write_pages(pages, txt_path):
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(PAGE_MARK.format(n=number) + page for number, page in enumerate(pages, 1))
    with open(txt_path, "w", encoding="utf-8", newline="\n") as stream:  # Path.write_text(newline=) is 3.10+
        stream.write(text)
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--manifest", help="defaults to <task>/pdfs/manifest.json")
    parser.add_argument("--text-dir", help="defaults to <task>/pdf_text")
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--progress-task", help="task directory for dashboard updates")
    parser.add_argument("--limit")
    args = parser.parse_args()
    task = Path(args.task)
    manifest_path = Path(args.manifest) if args.manifest else task / "pdfs" / "manifest.json"
    ranked_path = task / "ranked.jsonl"
    text_dir = Path(args.text_dir) if args.text_dir else task / "pdf_text"
    if not manifest_path.exists():
        parser.error(f"manifest not found: {manifest_path}")
    if not ranked_path.exists():
        parser.error(f"ranked not found: {ranked_path}")
    ranked = [json.loads(line) for line in ranked_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = read_json(task / "plan.json", {}) or {}
    queries = args.query or (plan.get("fulltext") or {}).get("queries") or [
        "method", "experiment", "result", "ablation", "limitation",
    ]
    output = []
    progress_total = min(len(manifest), int(args.limit)) if args.limit else len(manifest)
    if args.progress_task:
        update_progress(
            args.progress_task, stage="fulltext", status="running", completed=0,
            total=progress_total, errors=0, message="Starting full-text extraction",
        )
    for item in manifest:
        if args.limit and len(output) >= int(args.limit):
            break
        idx = item.get("idx")
        if not isinstance(idx, int) or idx < 0 or idx >= len(ranked) or not item.get("file"):
            continue
        pdf_path = Path(item["file"])
        if not pdf_path.is_absolute():
            pdf_path = task / pdf_path
        row = ranked[idx]
        txt_path = text_dir / f"{idx}_{norm(row.get('title'))[:60]}.txt"
        record = {
            "idx": idx,
            "title": row.get("title"),
            "pdf_path": str(pdf_path),
            "text_path": str(txt_path),
            "status": "failed",
        }
        try:
            if not pdf_path.exists():
                raise FileNotFoundError(str(pdf_path))
            pages, extractor = extract_pages(pdf_path, args.timeout)
            content = write_pages(pages, txt_path)
            first_pages = "\n".join(pages[:3])
            match_ratio = title_match(row.get("title"), first_pages)
            expected_ids = [
                value for value in (
                    (row.get("externalIds") or {}).get("ArXiv"),
                    row.get("doi"),
                    row.get("arxiv_id"),
                ) if value
            ]
            identifier_match = any(str(value).lower() in first_pages.lower() for value in expected_ids)
            snippets = evidence_snippets(pages, queries)
            record.update({
                # Scanned PDFs without a text layer cannot be read or quoted: never report them as ok.
                "status": ("no_text_layer" if len(content.strip()) < 1500 else
                           "ok" if match_ratio >= 0.6 or identifier_match else "identity_unverified"),
                "pages": len(pages),
                "extractor": extractor,
                "empty_pages": sum(not page.strip() for page in pages),
                "characters": len(content),
                "title_token_recall": round(match_ratio, 4),
                "identifier_match": identifier_match,
                "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                "evidence": snippets,
            })
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
        output.append(record)
        if args.progress_task:
            bad = sum(row["status"] != "ok" for row in output)
            update_progress(
                args.progress_task, stage="fulltext", status="running",
                completed=len(output), total=progress_total, errors=bad,
                message=f"Full text {len(output)}/{progress_total}: {record['status']}",
                summary=(
                    f"Full text {len(output)}/{progress_total}; non-ok={bad}"
                ) if len(output) % 10 == 0 or len(output) == progress_total else None,
            )
    out_path = task / "fulltext_manifest.json"
    temporary = out_path.with_name(out_path.name + ".tmp")
    temporary.write_text(
        json.dumps({"created_at": now(), "queries": queries, "records": output}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, out_path)
    ok = sum(item["status"] == "ok" for item in output)
    unverified = sum(item["status"] in {"identity_unverified", "no_text_layer"} for item in output)
    failed = sum(item["status"] == "failed" for item in output)
    print(f"FULLTEXT_FINISHED ok={ok} unverified={unverified} failed={failed} -> {out_path}")
    if args.progress_task:
        update_progress(
            args.progress_task, stage="fulltext",
            status="degraded" if unverified or failed else "done",
            completed=len(output), total=progress_total, errors=unverified + failed,
            message=f"Full text complete: ok={ok}, unverified={unverified}, failed={failed}",
            summary=f"Full text complete: ok={ok}, unverified={unverified}, failed={failed}",
        )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
