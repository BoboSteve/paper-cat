#!/usr/bin/env python3
"""Run paper-search-mcp once per query and persist all results for later merging."""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources  # noqa: E402
from common import load_env  # noqa: E402
from sources import arxiv_all_terms, arxiv_date_clause  # noqa: E402,F401
from progress_dashboard import update as update_progress  # noqa: E402


PSM_COMMIT = "cab1b5cc276a0cd004a0044003926a6440088049"
PSM_SPEC = f"paper-search-mcp @ git+https://github.com/openags/paper-search-mcp.git@{PSM_COMMIT}"
DEFAULT_SOURCES = "openalex,semantic,crossref,arxiv,dblp,openreview"
RETRYABLE = ("429", "rate", "timeout", "timed out", "temporarily")
def arxiv_search(item, timeout=60):
    """arXiv step of one query row: concept/field query override, date window and paging (see sources.py)."""
    query = item.get("arxiv_query") or arxiv_all_terms(item["query"])
    return sources.arxiv_search(query, item.get("date_from"), item.get("date_to"),
                                int(item.get("arxiv_max_results") or item["max_results"]), timeout=timeout)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def safe_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError("query_id must contain only letters, digits, dot, underscore, or hyphen")
    return value


def normalize_query(row, default_sources, default_max, psm_spec):
    query_id = safe_name(row.get("query_id"))
    query = str(row.get("query") or "").strip()
    if not query:
        raise ValueError(f"{query_id}: query is empty")
    sources = row.get("sources", default_sources)
    if isinstance(sources, list):
        sources = ",".join(str(source).strip() for source in sources if str(source).strip())
    sources = ",".join(part.strip() for part in str(sources or "").split(",") if part.strip())
    if not sources:
        raise ValueError(f"{query_id}: sources is empty")
    max_results = int(row.get("max_results", default_max))
    if not 1 <= max_results <= 1000:
        raise ValueError(f"{query_id}: max_results must be 1..1000")
    year = str(row.get("year") or "").strip()
    value = {
        "query_id": query_id,
        "query": query,
        "sources": sources,
        "max_results": max_results,
        "year": year,
        "sort": str(row.get("sort") or "relevance"),
        "psm_spec": psm_spec,
    }
    for key in ("arxiv_query", "arxiv_max_results", "openalex_query", "openalex_max_results", "date_from", "date_to"):
        if row.get(key):
            value[key] = row[key]
    value["query_hash"] = digest(value)
    return value


def command_for(uvx, spec, item, source_timeout):
    command = [
        uvx,
        "--from",
        spec,
        "paper-search",
        "search",
        item["query"],
        "--sources",
        item["sources"],
        "--max-results",
        str(item["max_results"]),
        "--sort",
        item["sort"],
    ]
    if item["year"]:
        command += ["--year", item["year"]]
    if source_timeout:
        command += ["--source-timeout", str(source_timeout)]
    return command


def split_authors(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if not isinstance(value, str):
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


def record_year(value):
    match = re.match(r"(\d{4})", str(value or ""))
    return int(match.group(1)) if match else None


def convert_paper(paper, item, summary):
    raw = dict(paper)
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    doi = str(raw.get("doi") or "").strip().lower()
    paper_id = str(raw.get("paper_id") or "").strip()
    identity = doi or paper_id or re.sub(r"\W+", "", title.casefold())
    return {
        **raw,
        "record_id": identity,
        "title": title,
        "authors": split_authors(raw.get("authors")),
        "psm_authors": raw.get("authors"),
        "abstract": str(raw.get("abstract") or "").strip(),
        "year": record_year(raw.get("published_date")),
        "query_id": item["query_id"],
        "query": item["query"],
        "query_hash": item["query_hash"],
        "psm_source_order": item["sources"].split(","),
        "psm_source_results": summary.get("source_results") or {},
        "psm_errors": summary.get("errors") or {},
        "psm_raw_path": f"raw/psm/{item['query_id']}.json",
    }


def existing_summary(path, item):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if value.get("query_hash") != item["query_hash"] or value.get("status") not in {"ok", "partial"}:
        return None
    # A partial result caused by rate limits or timeouts is retried instead of being cached forever.
    errors = " ".join(str(v) for v in (value.get("errors") or {}).values()).lower()
    if value.get("status") == "partial" and any(word in errors for word in RETRYABLE):
        return None
    return value


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--uvx", default="uvx")
    parser.add_argument("--psm-spec", default=PSM_SPEC)
    parser.add_argument("--default-sources", default=DEFAULT_SOURCES)
    parser.add_argument("--default-max-results", type=int, default=20)
    parser.add_argument("--source-timeout", type=float, default=60)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--progress-task", help="task directory for dashboard updates")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--arxiv-via-psm", action="store_true",
                        help="let paper-search-mcp query arXiv itself (default: built-in throttled arXiv client)")
    args = parser.parse_args()
    if args.timeout <= 0 or args.source_timeout <= 0:
        parser.error("timeouts must be positive")
    uvx = shutil.which(args.uvx) if os.path.sep not in args.uvx else args.uvx
    if not uvx:
        parser.error(f"uvx executable not found: {args.uvx}")

    with open(args.queries, encoding="utf-8") as source:
        raw_queries = [json.loads(line) for line in source if line.strip()]
    queries = [normalize_query(row, args.default_sources, args.default_max_results, args.psm_spec)
               for row in raw_queries]
    if len({item["query_id"] for item in queries}) != len(queries):
        parser.error("duplicate query_id")
    if args.limit:
        queries = queries[: args.limit]

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw" / "psm"
    summary_dir = out_dir / "summaries" / "psm"
    raw_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)
    calls_path = out_dir / "psm_calls.jsonl"
    candidates = []
    summaries = []
    failed = False
    failed_count = 0

    if args.progress_task:
        update_progress(
            args.progress_task, stage="search", status="running", completed=0,
            total=len(queries), errors=0, message=f"Starting {len(queries)} queries",
        )

    for query_index, item in enumerate(queries, 1):
        summary_path = summary_dir / f"{item['query_id']}.json"
        prior = existing_summary(summary_path, item)
        if prior:
            summaries.append(prior)
            candidates.extend(prior.get("candidates") or [])
            print(f"PSM_CACHED {item['query_id']} papers={prior.get('paper_count', 0)}", flush=True)
            if args.progress_task:
                update_progress(
                    args.progress_task, stage="search", status="running", completed=query_index,
                    total=len(queries), errors=failed_count,
                    message=f"Cached {item['query_id']} ({prior.get('paper_count', 0)} papers)",
                    summary=(
                        f"Processed {query_index}/{len(queries)} queries; "
                        f"{len(candidates)} candidates; {failed_count} failed queries"
                    ) if query_index % 5 == 0 or query_index == len(queries) else None,
                )
            continue

        wanted = item["sources"].split(",")  # not "sources": that name is the module
        direct_arxiv = "arxiv" in wanted and not args.arxiv_via_psm
        direct_openalex = "openalex" in wanted and bool(item.get("openalex_query"))
        skip = ({"arxiv"} if direct_arxiv else set()) | ({"openalex"} if direct_openalex else set())
        psm_sources = [source for source in wanted if source not in skip]
        command = command_for(uvx, args.psm_spec, {**item, "sources": ",".join(psm_sources)},
                              args.source_timeout) if psm_sources else []
        started_at = now()
        started = time.monotonic()
        stdout = stderr = ""
        returncode = None
        error = ""
        try:
            response = {"papers": [], "errors": {}, "source_results": {}, "sources_used": []}
            if command:
                proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=args.timeout, check=False)
                returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
                response = json.loads(stdout)
                if returncode != 0 or not isinstance(response, dict) or not isinstance(response.get("papers"), list):
                    raise ValueError("paper-search returned unsuccessful or malformed JSON")
                response["errors"] = dict(response.get("errors") or {})
                response["source_results"] = dict(response.get("source_results") or {})
                response["sources_used"] = list(response.get("sources_used") or [])
            if direct_arxiv:
                try:
                    arxiv_papers = arxiv_search(item, timeout=args.source_timeout)
                    response["papers"] = list(response["papers"]) + arxiv_papers
                    response["source_results"]["arxiv"] = len(arxiv_papers)
                    response["sources_used"].append("arxiv")
                    (raw_dir / f"{item['query_id']}.arxiv.json").write_text(
                        json.dumps(arxiv_papers, ensure_ascii=False, indent=1), encoding="utf-8")
                except Exception as exc:
                    response["errors"]["arxiv"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                    response["source_results"]["arxiv"] = 0
            if direct_openalex:
                try:
                    found = sources.openalex_search(item["openalex_query"], item.get("date_from"), item.get("date_to"),
                                                    int(item.get("openalex_max_results") or item["max_results"]),
                                                    load_env(), timeout=args.source_timeout)
                    response["papers"] = list(response["papers"]) + found
                    response["source_results"]["openalex"] = len(found)
                    response["sources_used"].append("openalex")
                except Exception as exc:
                    response["errors"]["openalex"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                    response["source_results"]["openalex"] = 0
            converted = [
                converted
                for paper in response["papers"]
                for converted in [convert_paper(paper, item, response)]
                if converted is not None
            ]
            malformed = len(response["papers"]) - len(converted)
            status = "partial" if response.get("errors") or malformed else "ok"
        except Exception as exc:
            failed = True
            failed_count += 1
            response = {}
            converted = []
            malformed = 0
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"

        raw_path = raw_dir / f"{item['query_id']}.json"
        if stdout:
            raw_path.write_text(stdout, encoding="utf-8")
        stderr_path = raw_dir / f"{item['query_id']}.stderr.log"
        if stderr:
            stderr_path.write_text(stderr, encoding="utf-8")
        summary = {
            **item,
            "status": status,
            "started_at": started_at,
            "finished_at": now(),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "returncode": returncode,
            "raw_path": str(raw_path.relative_to(out_dir)) if stdout else None,
            "stderr_path": str(stderr_path.relative_to(out_dir)) if stderr else None,
            "source_results": response.get("source_results") or {},
            "sources_used": response.get("sources_used") or [],
            "errors": response.get("errors") or {},
            # answered without error but found nothing: reported, never shown as a plain success
            "empty_sources": sorted(name for name, count in (response.get("source_results") or {}).items()
                                    if not count and name not in (response.get("errors") or {})),
            "raw_total": response.get("raw_total"),
            "paper_count": len(converted),
            "malformed_papers": malformed,
            "error": error,
            "candidates": converted,
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        call = {
            "query_id": item["query_id"],
            "query_hash": item["query_hash"],
            "command": command,
            "status": status,
            "started_at": summary["started_at"],
            "finished_at": summary["finished_at"],
            "elapsed_ms": summary["elapsed_ms"],
            "returncode": returncode,
            "source_results": summary["source_results"],
            "errors": summary["errors"],
            "error": error,
        }
        with calls_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(call, ensure_ascii=False) + "\n")
        summaries.append(summary)
        candidates.extend(converted)
        print(f"PSM_QUERY {item['query_id']} status={status} papers={len(converted)}", flush=True)
        if args.progress_task:
            update_progress(
                args.progress_task, stage="search", status="running", completed=query_index,
                total=len(queries), errors=failed_count,
                message=f"{item['query_id']}: {status}, {len(converted)} papers",
                summary=(
                    f"Processed {query_index}/{len(queries)} queries; "
                    f"{len(candidates)} candidates; {failed_count} failed queries"
                ) if query_index % 5 == 0 or query_index == len(queries) else None,
            )

    write_jsonl(out_dir / "psm_candidates.jsonl", candidates)
    spec_commit = re.search(r"@([0-9a-f]{40})", args.psm_spec)
    meta = {
        "psm_spec": args.psm_spec,
        "psm_commit": spec_commit.group(1) if spec_commit else None,
        "default_sources": args.default_sources,
        "queries": summaries,
        "paper_count": len(candidates),
        "created_at": now(),
    }
    (out_dir / "psm_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    coverage = {
        "query_statuses": dict(Counter(item.get("status") for item in summaries)),
        "source_results": {},
        "source_errors": {},
        "stderr_events": {},
    }
    source_results = Counter()
    source_errors = Counter()
    stderr_events = Counter()
    for summary in summaries:
        for source, count in (summary.get("source_results") or {}).items():
            source_results[source] += int(count or 0)
        for source in (summary.get("errors") or {}):
            source_errors[source] += 1
        stderr_path = summary.get("stderr_path")
        if stderr_path:
            text = (out_dir / stderr_path).read_text(encoding="utf-8", errors="replace")
            for source in summary["sources"].split(","):
                if source in text.lower():
                    if "429" in text:
                        stderr_events[source + ":rate_limited"] += 1
                    elif "error" in text.lower():
                        stderr_events[source + ":error"] += 1
    coverage["source_results"] = dict(source_results)
    coverage["source_errors"] = dict(source_errors)
    coverage["empty_sources"] = dict(Counter(name for summary in summaries for name in summary.get("empty_sources") or []))
    coverage["stderr_events"] = dict(stderr_events)
    (out_dir / "psm_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"PSM_FINISHED papers={len(candidates)} -> {out_dir / 'psm_candidates.jsonl'}")
    if args.progress_task:
        update_progress(
            args.progress_task, stage="search",
            status="degraded" if failed else "done", completed=len(queries),
            total=len(queries), errors=failed_count,
            message=f"Search complete: {len(candidates)} candidates",
            summary=f"Search complete: {len(candidates)} candidates, {failed_count} failed queries",
        )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
