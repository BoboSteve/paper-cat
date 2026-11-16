"""Key-free search clients used by quick mode and by the deep pipeline's arXiv step.

Both clients go through common.http_get, which spaces requests per host for every process on the
machine (arXiv: one request per 3.2 s) and backs off on HTTP 429 using Retry-After.
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

from common import arxiv_id_of, http_get, http_json, normalize_doi

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
STOPWORDS = {"a", "an", "and", "the", "of", "for", "in", "on", "to", "with", "via", "by", "from", "using"}


def arxiv_all_terms(text):
    """Legacy fallback: natural-language query -> all terms required."""
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", text) if w.lower() not in STOPWORDS]
    return " AND ".join(f"all:{w}" for w in words[:8]) or f'all:"{text}"'


def arxiv_date_clause(date_from, date_to):
    if not date_from:
        return ""
    start = date_from.replace("-", "")[:8]
    end = (date_to or "2099-12-31").replace("-", "")[:8]
    return f" AND submittedDate:[{start}0000 TO {end}2359]"


def _arxiv_entry(entry):
    match = re.search(r"abs/(.+?)(v\d+)?$", entry.findtext(ATOM + "id") or "")
    if not match:
        return None
    arxiv_id = match.group(1)
    links = {link.get("title") or link.get("rel"): link.get("href") for link in entry.findall(ATOM + "link")}
    return {
        "paper_id": f"arxiv:{arxiv_id}",
        "title": re.sub(r"\s+", " ", entry.findtext(ATOM + "title") or "").strip(),
        "authors": "; ".join((a.findtext(ATOM + "name") or "").strip() for a in entry.findall(ATOM + "author")),
        "abstract": re.sub(r"\s+", " ", entry.findtext(ATOM + "summary") or "").strip(),
        "doi": f"10.48550/arxiv.{arxiv_id}",
        "published_date": (entry.findtext(ATOM + "published") or "")[:10],
        "updated_date": (entry.findtext(ATOM + "updated") or "")[:10],
        "date_precision": "day",
        "pdf_url": links.get("pdf") or f"https://arxiv.org/pdf/{arxiv_id}",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "source": "arxiv",
        "categories": "; ".join(c.get("term") for c in entry.findall(ATOM + "category")),
        "arxiv_comment": (entry.findtext(ARXIV_NS + "comment") or "").strip(),
        "arxiv_journal_ref": (entry.findtext(ARXIV_NS + "journal_ref") or "").strip(),
        "published_doi": (entry.findtext(ARXIV_NS + "doi") or "").strip(),
    }


def arxiv_search(search_query, date_from=None, date_to=None, max_results=100, page_size=100, timeout=60):
    """Run an arXiv API query (field syntax) restricted to submission dates, fetching pages until done."""
    query = search_query + arxiv_date_clause(date_from, date_to)
    entries = []
    for start in range(0, max(1, int(max_results)), page_size):
        size = min(page_size, int(max_results) - start)
        params = {"search_query": query, "start": start, "max_results": size,
                  "sortBy": "relevance", "sortOrder": "descending"}
        xml = http_get("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params),
                       timeout=timeout, retries=4, backoff=(15, 30, 60, 120), max_wait=180)
        page = ET.fromstring(xml).findall(ATOM + "entry")
        entries.extend(page)
        if len(page) < size:
            break
    return [paper for paper in (_arxiv_entry(entry) for entry in entries) if paper and paper["title"]]


OPENALEX_SELECT = ("id,doi,title,publication_date,authorships,abstract_inverted_index,primary_location,"
                   "locations,best_oa_location,type,ids")


def _openalex_record(work):
    inverted = work.get("abstract_inverted_index") or {}
    words = {position: word for word, positions in inverted.items() for position in positions}
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    formal = next((loc.get("source") for loc in [primary] + list(work.get("locations") or [])
                   if (loc.get("source") or {}).get("type") in {"journal", "conference"}), None) or {}
    record = {
        "paper_id": work.get("id"),
        "title": re.sub(r"\s+", " ", work.get("title") or "").strip(),
        "authors": "; ".join((a.get("author") or {}).get("display_name", "") for a in work.get("authorships") or []),
        "abstract": " ".join(words[i] for i in sorted(words)),
        "doi": normalize_doi(work.get("doi")),
        "published_date": work.get("publication_date") or "",
        "url": work.get("doi") or primary.get("landing_page_url") or work.get("id"),
        "pdf_url": (work.get("best_oa_location") or {}).get("pdf_url") or primary.get("pdf_url"),
        "source": "openalex",
        "openalex_type": work.get("type"),
        "repository": source.get("display_name") if source.get("type") == "repository" else None,
    }
    if formal.get("display_name"):
        record.update({"venue": formal["display_name"], "venue_source_type": formal.get("type"),
                       "venue_issn": formal.get("issn_l") or (formal.get("issn") or [None])[0],
                       "venue_evidence": "openalex_location"})
    arxiv = arxiv_id_of({"url": primary.get("landing_page_url"), "pdf_url": primary.get("pdf_url"),
                         "doi": record["doi"]})
    if arxiv:
        record["arxiv_id"] = arxiv
    return record


def openalex_search(search, date_from=None, date_to=None, max_results=100, settings=None, timeout=30):
    """OpenAlex boolean search over titles and abstracts; works without a key (shared public quota)."""
    settings = settings or {}
    key = settings.get("OPENALEX_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    filters = []
    if date_from:
        filters.append(f"from_publication_date:{date_from}")
    if date_to:
        filters.append(f"to_publication_date:{date_to}")
    # Page numbers, not cursor=: since 2026 OpenAlex refuses cursor paging without an API key.
    per_page = min(200, max(1, max_results))
    records, page = [], 1
    while len(records) < max_results and page * per_page <= 10000:
        params = {"search": search, "per-page": per_page, "page": page, "select": OPENALEX_SELECT}
        if filters:
            params["filter"] = ",".join(filters)
        try:
            payload = http_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params),
                                headers=headers, timeout=timeout, retries=2)
        except Exception:
            if not records:
                raise
            break  # keep the pages already fetched instead of failing the whole source
        results = payload.get("results") or []
        records += [r for r in (_openalex_record(work) for work in results) if r["title"]]
        if len(results) < per_page:
            break
        page += 1
    return records[:max_results]
