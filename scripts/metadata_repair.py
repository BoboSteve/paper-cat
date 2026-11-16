#!/usr/bin/env python3
"""Fill missing abstracts and formal metadata with conservative source matching."""
import argparse
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


DEFAULT_ENV_FILE = Path.home() / ".config" / "paper-search-mcp" / ".env"
OPENALEX_FIELDS = (
    "id,doi,title,authorships,publication_year,primary_location,"
    "abstract_inverted_index,best_oa_location,type"
)
CROSSREF_SELECT = "DOI,title,author,container-title,issued,abstract,URL"


def load_env(path):
    values = {}
    path = Path(path)
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip().removeprefix("export ").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key.strip()] = value
    for key in ("OPENALEX_API_KEY", "OPENALEX_EMAIL"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def request_json(url, headers=None, timeout=30):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "paper-cat-metadata/1.0", **(headers or {})},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def normalize_doi(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value.rstrip("/") if value.startswith("10.") else ""


def record_doi(row):
    external = row.get("externalIds") or {}
    return normalize_doi(row.get("doi") or external.get("DOI"))


def record_arxiv(row):
    external = row.get("externalIds") or {}
    values = [row.get("arxiv_id"), external.get("ArXiv"), external.get("ARXIV")]
    for value in values:
        match = re.search(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?", str(value or ""))
        if match:
            return match.group(1)
    return ""


def norm(value):
    return re.sub(r"[^a-z0-9]+", "", html.unescape(str(value or "")).casefold())


def title_tokens(value):
    return set(re.findall(r"[a-z0-9]+", html.unescape(str(value or "")).casefold()))


def title_matches(left, right):
    left_norm, right_norm = norm(left), norm(right)
    if left_norm and left_norm == right_norm:
        return True
    left_tokens, right_tokens = title_tokens(left), title_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    shared = len(left_tokens & right_tokens)
    jaccard = shared / len(left_tokens | right_tokens)
    containment = shared / min(len(left_tokens), len(right_tokens))
    return shared >= 2 and (jaccard >= 0.7 or containment >= 0.75)


def year(value):
    match = re.search(r"(?:19|20|21)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None


def years_compatible(left, right):
    return not left or not right or abs(left - right) <= 1


def abstract_from_inverted(inverted):
    if not inverted:
        return ""
    positions = {position: word for word, indexes in inverted.items() for position in indexes}
    return " ".join(positions[index] for index in sorted(positions))


def clean_abstract(value):
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def openalex_candidates(row, settings, timeout):
    key = settings.get("OPENALEX_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    doi = record_doi(row)
    if doi:
        url = f"https://api.openalex.org/works/https://doi.org/{doi}"
        try:
            return [request_json(url, headers=headers, timeout=timeout)]
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    title = str(row.get("title") or "")
    params = {
        "search": title,
        "per-page": 5,
        "select": OPENALEX_FIELDS,
    }
    payload = request_json(
        "https://api.openalex.org/works?" + urllib.parse.urlencode(params),
        headers=headers,
        timeout=timeout,
    )
    return payload.get("results") or []


def crossref_candidates(row, timeout):
    doi = record_doi(row)
    if doi:
        try:
            payload = request_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}", timeout=timeout)
            return [payload.get("message") or {}]
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    params = {
        "query.bibliographic": str(row.get("title") or ""),
        "rows": 5,
        "select": CROSSREF_SELECT,
    }
    payload = request_json("https://api.crossref.org/works?" + urllib.parse.urlencode(params), timeout=timeout)
    return ((payload.get("message") or {}).get("items") or [])


def arxiv_candidate(row, timeout):
    arxiv_id = record_arxiv(row)
    if not arxiv_id:
        return None
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": arxiv_id})
    request = urllib.request.Request(url, headers={"User-Agent": "paper-cat-metadata/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        root = ET.fromstring(response.read())
    entry = root.find("{http://www.w3.org/2005/Atom}entry")
    if entry is None:
        return None
    return {
        "title": (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip(),
        "abstract": (entry.findtext("{http://www.w3.org/2005/Atom}summary") or "").strip(),
        "published": (entry.findtext("{http://www.w3.org/2005/Atom}published") or "").strip(),
        "authors": [
            (author.findtext("{http://www.w3.org/2005/Atom}name") or "").strip()
            for author in entry.findall("{http://www.w3.org/2005/Atom}author")
        ],
    }


def normalize_openalex(candidate):
    if not candidate:
        return None
    source = (candidate.get("primary_location") or {}).get("source") or {}
    return {
        "title": candidate.get("title") or "",
        "abstract": abstract_from_inverted(candidate.get("abstract_inverted_index")),
        "authors": [
            (item.get("author") or {}).get("display_name", "")
            for item in candidate.get("authorships") or []
        ],
        "year": candidate.get("publication_year"),
        "venue": source.get("display_name") or "",
        "venue_issn": (source.get("issn") or [None])[0],
        "doi": normalize_doi(candidate.get("doi")),
        "openAccessPdf": (candidate.get("best_oa_location") or {}).get("pdf_url"),
        "source": "openalex",
    }


def normalize_crossref(candidate):
    if not candidate:
        return None
    titles = candidate.get("title") or []
    issued = (candidate.get("issued") or {}).get("date-parts") or [[None]]
    authors = []
    for author in candidate.get("author") or []:
        name = " ".join(value for value in (author.get("given"), author.get("family")) if value)
        if name:
            authors.append(name)
    containers = candidate.get("container-title") or []
    return {
        "title": titles[0] if titles else "",
        "abstract": clean_abstract(candidate.get("abstract")),
        "authors": authors,
        "year": issued[0][0] if issued and issued[0] else None,
        "venue": containers[0] if containers else "",
        "doi": normalize_doi(candidate.get("DOI")),
        "url": candidate.get("URL"),
        "source": "crossref",
    }


def merge_repair(row, candidate, allowed=None):
    if not candidate or not title_matches(row.get("title"), candidate.get("title")):
        return None
    if not years_compatible(year(row), year(candidate.get("year"))):
        return None
    updates = {}
    for key in ("abstract", "doi", "venue", "venue_issn", "openAccessPdf"):
        if allowed and key not in allowed:
            continue
        value = candidate.get(key)
        if value and (not row.get(key) or (key == "abstract" and len(str(value)) > len(str(row.get(key) or "")))):
            updates[key] = value
    if candidate.get("authors") and not row.get("authors") and (not allowed or "authors" in allowed):
        updates["authors"] = candidate["authors"]
    if candidate.get("venue_issn") and not row.get("venue_issn") and (not allowed or "venue_issn" in allowed):
        updates.setdefault("venue_issn", candidate["venue_issn"])
    if candidate.get("year") and not row.get("year") and (not allowed or "year" in allowed):
        updates["year"] = candidate["year"]
    if not updates:
        return None
    return updates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--sleep", type=float, default=0.1)
    parser.add_argument(
        "--only-fields",
        default="abstract,doi,venue,venue_issn,openAccessPdf,authors,year",
        help="comma-separated fields eligible for filling",
    )
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    settings = load_env(args.env_file)
    allowed = {value.strip() for value in args.only_fields.split(",") if value.strip()}
    rows = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        rows = rows[: args.limit]
    stats = Counter()
    output = []
    for row in rows:
        repaired = dict(row)
        if not any(not row.get(field) for field in allowed):
            stats["skipped_complete"] += 1
            output.append(repaired)
            continue
        updates = None
        source = None
        errors = []
        try:
            for candidate in openalex_candidates(row, settings, args.timeout):
                normalized = normalize_openalex(candidate)
                if normalized:
                    updates = merge_repair(row, normalized, allowed)
                    if updates:
                        source = "openalex"
                        break
        except Exception as error:
            errors.append(f"openalex:{type(error).__name__}")
        if not updates:
            try:
                for candidate in crossref_candidates(row, args.timeout):
                    normalized = normalize_crossref(candidate)
                    if normalized:
                        updates = merge_repair(row, normalized, allowed)
                        if updates:
                            source = "crossref"
                            break
            except Exception as error:
                errors.append(f"crossref:{type(error).__name__}")
        if not updates:
            try:
                normalized = arxiv_candidate(row, args.timeout)
                if normalized:
                    normalized["source"] = "arxiv"
                    updates = merge_repair(row, normalized, allowed)
                    if updates:
                        source = "arxiv"
            except Exception as error:
                errors.append(f"arxiv:{type(error).__name__}")
        if updates:
            repaired.update(updates)
            repaired["metadata_sources"] = list(dict.fromkeys((repaired.get("metadata_sources") or []) + [source]))
            stats[source] += 1
            if "abstract" in updates:
                stats["abstracts_filled"] += 1
            if "venue" in updates:
                stats["venues_filled"] += 1
        else:
            stats["unchanged"] += 1
        if errors:
            repaired["metadata_errors"] = errors
            stats["records_with_errors"] += 1
        output.append(repaired)
        time.sleep(args.sleep)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(out_path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in output:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, out_path)
    report = {
        "input": str(Path(args.input).resolve()),
        "input_sha256": hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
        "records": len(output),
        "stats": dict(stats),
        "source_counts": dict(Counter(source for row in output for source in row.get("metadata_sources") or [])),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"METADATA_FINISHED records={len(output)} filled={stats['abstracts_filled']} abstracts -> {out_path}")


if __name__ == "__main__":
    main()
