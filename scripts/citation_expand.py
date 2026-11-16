#!/usr/bin/env python3
"""Expand user seeds by one OpenAlex references/citations hop."""
import argparse
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_ENV_FILE = Path.home() / ".config" / "paper-search-mcp" / ".env"
SELECT = (
    "id,doi,title,authorships,publication_year,primary_location,"
    "abstract_inverted_index,best_oa_location,referenced_works"
)


def load_env(path):
    values = {}
    path = Path(path)
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip().removeprefix("export ").strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("'\"")
    if os.environ.get("OPENALEX_API_KEY"):
        values["OPENALEX_API_KEY"] = os.environ["OPENALEX_API_KEY"]
    return values


def norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def title_match(left, right):
    left, right = norm(left), norm(right)
    return bool(left and right and (left == right or left in right or right in left))


def abstract_from_inverted(inverted):
    if not inverted:
        return ""
    positions = {position: word for word, indexes in inverted.items() for position in indexes}
    return " ".join(positions[index] for index in sorted(positions))


def request_json(url, key, timeout):
    headers = {"User-Agent": "paper-cat-citation/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def normalize_work(work, seed, relationship, query_hash):
    source = (work.get("primary_location") or {}).get("source") or {}
    return {
        "record_id": work.get("id") or work.get("doi") or norm(work.get("title")),
        "id_openalex": work.get("id"),
        "doi": re.sub(r"^https?://doi\.org/", "", str(work.get("doi") or ""), flags=re.I),
        "title": work.get("title") or "",
        "authors": [
            (authorship.get("author") or {}).get("display_name", "")
            for authorship in work.get("authorships") or []
        ],
        "abstract": abstract_from_inverted(work.get("abstract_inverted_index")),
        "year": work.get("publication_year"),
        "venue": source.get("display_name") or "",
        "venue_issn": (source.get("issn") or [None])[0],
        "openAccessPdf": (work.get("best_oa_location") or {}).get("pdf_url"),
        "source": "citation_expansion",
        "query_id": relationship + ":" + seed["record_id"],
        "query": f"{relationship} of {seed.get('title') or seed['record_id']}",
        "query_hash": query_hash,
        "seed_record_id": seed["record_id"],
        "seed_title": seed.get("title"),
        "relationship": relationship,
    }


def resolve_seed(seed, key, timeout):
    doi = str(seed.get("doi") or "").strip()
    if doi:
        try:
            return request_json(f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi)}", key, timeout)
        except Exception:
            pass
    title = str(seed.get("title") or "").strip()
    if not title:
        return None
    payload = request_json(
        "https://api.openalex.org/works?" + urllib.parse.urlencode({
            "search": title,
            "per-page": 5,
            "select": SELECT,
        }),
        key,
        timeout,
    )
    for work in payload.get("results") or []:
        if title_match(title, work.get("title")):
            return work
    return None


def expand(seed, work, direction, max_results, key, timeout):
    work_id = str(work.get("id") or "").split("/")[-1]
    if not work_id:
        return []
    query_hash = hashlib.sha256(
        json.dumps({"seed": seed["record_id"], "direction": direction, "max": max_results}).encode()
    ).hexdigest()
    rows = []
    if direction in {"references", "both"}:
        references = [value.split("/")[-1] for value in work.get("referenced_works") or []]
        for offset in range(0, len(references), 50):
            batch = references[offset: offset + 50]
            if not batch:
                continue
            payload = request_json(
                "https://api.openalex.org/works?" + urllib.parse.urlencode({
                    "filter": "openalex_id:" + "|".join(batch),
                    "per-page": min(50, max_results - len(rows)),
                    "select": SELECT,
                }),
                key,
                timeout,
            )
            rows.extend(normalize_work(item, seed, "references", query_hash) for item in payload.get("results") or [])
            if len(rows) >= max_results:
                return rows[:max_results]
    if direction in {"citations", "both"}:
        payload = request_json(
            "https://api.openalex.org/works?" + urllib.parse.urlencode({
                "filter": f"cites:{work_id}",
                "per-page": min(50, max_results),
                "select": SELECT,
            }),
            key,
            timeout,
        )
        rows.extend(normalize_work(item, seed, "citations", query_hash) for item in payload.get("results") or [])
    return rows[:max_results]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--direction", choices=("references", "citations", "both"), default="both")
    parser.add_argument("--max-per-seed", type=int, default=20)
    parser.add_argument("--limit-seeds", type=int)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--sleep", type=float, default=0.2)
    args = parser.parse_args()
    key = load_env(args.env_file).get("OPENALEX_API_KEY", "")
    seeds = [json.loads(line) for line in Path(args.seeds).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit_seeds:
        seeds = seeds[: args.limit_seeds]
    rows = []
    unresolved = []
    for seed in seeds:
        work = resolve_seed(seed, key, args.timeout)
        if not work:
            unresolved.append(seed["record_id"])
            continue
        rows.extend(expand(seed, work, args.direction, args.max_per_seed, key, args.timeout))
        time.sleep(args.sleep)
    unique = {}
    for row in rows:
        unique.setdefault(row["record_id"], row)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for row in unique.values():
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = output.with_suffix(".report.json")
    report.write_text(
        json.dumps({
            "seeds": len(seeds),
            "resolved": len(seeds) - len(unresolved),
            "unresolved": unresolved,
            "expanded_records": len(rows),
            "unique": len(unique),
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"CITATION_FINISHED seeds={len(seeds)} resolved={len(seeds)-len(unresolved)} unique={len(unique)}")


if __name__ == "__main__":
    main()
