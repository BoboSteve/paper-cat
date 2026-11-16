#!/usr/bin/env python3
"""Extract paper links from an explicitly supplied official main-track page."""
import argparse
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self.parts = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.parts = []

    def handle_data(self, data):
        if self.href:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            title = re.sub(r"\s+", " ", html.unescape(" ".join(self.parts))).strip()
            if title:
                self.links.append((self.href, title))
            self.href = None
            self.parts = []


def fetch(url, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": "paper-cat-official/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def absolute(base, href):
    if href.startswith(("http://", "https://")):
        return href
    return urllib.parse.urljoin(base, href)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--input", help="saved HTML instead of live fetch")
    parser.add_argument("--venue", required=True)
    parser.add_argument("--venueid", required=True)
    parser.add_argument("--track-label", required=True, help="e.g. NeurIPS main, CVPR main, USENIX ATC main")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--link-pattern", default=r"/")
    parser.add_argument("--title-min-length", type=int, default=12)
    parser.add_argument("--out", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.track_label.strip().lower() not in {"main", "main conference", "main track"}:
        parser.error("--track-label must be main/main conference/main track; use a dedicated main-track page")
    source = Path(args.input).read_text(encoding="utf-8") if args.input else fetch(args.url, args.timeout)
    link_parser = LinkParser()
    link_parser.feed(source)
    pattern = re.compile(args.link_pattern)
    rows = []
    seen = set()
    for href, title in link_parser.links:
        if len(title) < args.title_min_length or not pattern.search(href):
            continue
        url = absolute(args.url, href)
        if url in seen:
            continue
        seen.add(url)
        rows.append({
            "record_id": "official_html:" + hashlib.sha1(url.encode()).hexdigest()[:16],
            "title": title,
            "authors": [],
            "abstract": "",
            "year": args.year,
            "venue": args.venue,
            "venueid": args.venueid,
            "track": "main",
            "source_track_label": args.track_label,
            "record_type": "conference",
            "status": "published_main",
            "source": "official_proceedings_html",
            "url": url,
        })
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        parser.error("no paper links matched; verify the official main-track page and link pattern")
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "status": "ok",
        "source_url": args.url,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "venue": args.venue,
        "venueid": args.venueid,
        "track_label": args.track_label,
        "year": args.year,
        "link_pattern": args.link_pattern,
        "records": len(rows),
        "main_track_declared": True,
        "note": "The caller must supply a main-track official proceedings page; do not point this connector at workshop or findings indexes.",
    }
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OFFICIAL_HTML_FINISHED records={len(rows)} venue={args.venue}")


if __name__ == "__main__":
    main()
