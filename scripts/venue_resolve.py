#!/usr/bin/env python3
"""Recover where each candidate was formally published, so rank.py can look up grades.

Search layers (paper-search-mcp) drop venue/ISSN fields. This step restores them from
evidence, strongest first, and records which evidence was used:

1. ACL Anthology ID in the DOI/URL (10.18653/2026.findings-emnlp.889): venue + track are explicit.
2. OpenAlex locations looked up by DOI in batches: journal/conference source name and ISSN.
3. arXiv comment / journal-ref ("Accepted to ICLR 2026"): kept only as an unverified claim.

Nothing is invented: unresolved records stay unresolved.
"""
import argparse
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (acl_anthology_id, arxiv_id_of, http_get, http_json, load_env, normalize_doi,  # noqa: E402
                    parse_acl_id, read_jsonl, write_json, write_jsonl)

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
FORMAL_SOURCE_TYPES = {"journal", "conference", "book series"}
CLAIM_PATTERN = re.compile(
    r"\b(?i:accepted|to appear|published|camera[- ]ready|presented)\b[^.;]{0,40}?"
    r"\b(?i:at|to|in|by|for)?\s*(?i:the\s+)?([A-Z][A-Za-z&-]{1,15}(?:\s+[A-Z][A-Za-z&-]{1,15}){0,3})"
    r"\s*['’]?\s*((?:19|20)\d{2})",
)


def openalex_by_doi(dois, settings, timeout):
    key = settings.get("OPENALEX_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    found = {}
    dois = sorted(set(dois))
    for start in range(0, len(dois), 50):
        chunk = dois[start:start + 50]
        params = {
            "filter": "doi:" + "|".join(chunk),
            "per-page": 50,
            "select": "id,doi,type,primary_location,locations,best_oa_location",
        }
        payload = http_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params, safe=":|/"),
                            headers=headers, timeout=timeout)
        for work in payload.get("results") or []:
            found[normalize_doi(work.get("doi"))] = work
    return found


def formal_location(work):
    """Pick the published (non-repository) location; fall back to the primary one."""
    locations = [work.get("primary_location") or {}] + list(work.get("locations") or [])
    for location in locations:
        source = location.get("source") or {}
        if source.get("type") in FORMAL_SOURCE_TYPES and source.get("display_name"):
            return location, source
    primary = work.get("primary_location") or {}
    return primary, primary.get("source") or {}


def arxiv_metadata(ids, timeout):
    found = {}
    ids = sorted(set(ids))
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
            {"id_list": ",".join(chunk), "max_results": len(chunk)})
        root = ET.fromstring(http_get(url, timeout=timeout))
        for entry in root.findall(ATOM + "entry"):
            match = re.search(r"(\d{4}\.\d{4,5})", entry.findtext(ATOM + "id") or "")
            if match:
                found[match.group(1)] = {
                    "comment": (entry.findtext(ARXIV_NS + "comment") or "").strip(),
                    "journal_ref": (entry.findtext(ARXIV_NS + "journal_ref") or "").strip(),
                    "doi": (entry.findtext(ARXIV_NS + "doi") or "").strip(),
                }
    return found


def venue_claim(text):
    match = CLAIM_PATTERN.search(text or "")
    return f"{match.group(1)} {match.group(2)}" if match else ""


def resolve(rows, settings, use_openalex=True, use_arxiv=True, timeout=30, log=print):
    stats = Counter()
    errors = []
    for row in rows:
        anthology = acl_anthology_id(row)
        parsed = parse_acl_id(anthology)
        if parsed and parsed["venue_key"]:
            label = parsed["venue_abbrev"] if parsed["track"] != "findings" else f"Findings of {parsed['venue_abbrev']}"
            suffix = f" ({parsed['track']} track)" if parsed["track"] not in ("main", "findings", "journal") else ""
            row.update({
                "acl_id": anthology,
                "venue": f"{label} {parsed['year']}{suffix}",
                "venue_key": parsed["venue_key"],
                "track": parsed["track"],
                "track_source": "acl_anthology_id",
                "venue_evidence": "acl_anthology_id",
            })
            stats["acl_anthology_id"] += 1
        elif parsed:
            row.update({"acl_id": anthology, "track": "workshop", "track_source": "acl_anthology_id",
                        "venue_evidence": "acl_anthology_id"})
            stats["acl_workshop"] += 1

    if use_openalex:
        dois = [normalize_doi(row.get("doi")) for row in rows if not row.get("venue_key")]
        dois = [doi for doi in dois if doi]
        try:
            works = openalex_by_doi(dois, settings, timeout) if dois else {}
        except Exception as error:  # keep going; the gap is reported
            works = {}
            errors.append(f"openalex:{type(error).__name__}")
        for row in rows:
            work = works.get(normalize_doi(row.get("doi")))
            if not work or row.get("venue_key"):
                continue
            location, source = formal_location(work)
            if source.get("display_name") and source.get("type") in FORMAL_SOURCE_TYPES:
                row["venue"] = row.get("venue") or source["display_name"]
                row["venue_issn"] = row.get("venue_issn") or source.get("issn_l") or (source.get("issn") or [None])[0]
                row["venue_source_type"] = source.get("type")
                row["venue_evidence"] = "openalex_location"
                stats["openalex_formal"] += 1
            elif source.get("display_name"):
                row.setdefault("repository", source["display_name"])
                stats["openalex_repository_only"] += 1
            oa = (work.get("best_oa_location") or {}).get("pdf_url")
            if oa and not row.get("openAccessPdf"):
                row["openAccessPdf"] = oa

    if use_arxiv:
        # Records found through the arXiv API already carry comment/journal-ref: no extra request.
        metadata = {arxiv_id_of(row): {"comment": row.get("arxiv_comment") or "",
                                       "journal_ref": row.get("arxiv_journal_ref") or "",
                                       "doi": row.get("published_doi") or ""}
                    for row in rows if "arxiv_comment" in row and arxiv_id_of(row)}
        ids = [arxiv_id_of(row) for row in rows if not row.get("venue_evidence")]
        ids = [value for value in ids if value and value not in metadata]
        try:
            metadata.update(arxiv_metadata(ids, timeout) if ids else {})
        except Exception as error:
            errors.append(f"arxiv:{type(error).__name__}")
        for row in rows:
            meta = metadata.get(arxiv_id_of(row))
            if not meta or row.get("venue_evidence"):
                continue
            row["arxiv_comment"] = meta["comment"]
            row["arxiv_journal_ref"] = meta["journal_ref"]
            claim = meta["journal_ref"] or venue_claim(meta["comment"])
            if claim:
                row["claimed_venue"] = claim
                row["claimed_venue_source"] = "arxiv_journal_ref" if meta["journal_ref"] else "arxiv_comment"
                stats["arxiv_claim"] += 1
            if meta["doi"] and not normalize_doi(row.get("doi", "")).startswith("10.48550"):
                row.setdefault("published_doi", meta["doi"])
    stats["unresolved"] = sum(not row.get("venue_evidence") and not row.get("claimed_venue") for row in rows)
    log(f"VENUE_RESOLVE records={len(rows)} " + " ".join(f"{k}={v}" for k, v in sorted(stats.items()))
        + (f" errors={errors}" if errors else ""))
    return rows, dict(stats), errors


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report")
    parser.add_argument("--env-file")
    parser.add_argument("--no-openalex", action="store_true")
    parser.add_argument("--no-arxiv", action="store_true")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    rows = read_jsonl(args.input)
    rows, stats, errors = resolve(rows, load_env(args.env_file), not args.no_openalex, not args.no_arxiv, args.timeout)
    write_jsonl(args.out, rows)
    if args.report:
        write_json(args.report, {"records": len(rows), "stats": stats, "errors": errors})


if __name__ == "__main__":
    main()
