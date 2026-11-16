"""Candidate-pool helpers for the deep pipeline: prefilter, key-free ranking, recall check, rerank.

All of them use the plan's concept groups (see concepts.py), so quick and deep mode agree on what
"matches the topic" means.
"""
from __future__ import annotations

import re

import concepts as concept_tools
from common import arxiv_id_of, normalize_doi

PROTECTED_SOURCES = {"user_seed", "xhs_clue", "citation", "citation_expansion", "snowball"}


def plan_concepts(plan):
    return concept_tools.normalize_concepts(plan.get("concepts") or [], plan.get("topic_description") or plan.get("topic", ""))


def _sources(row):
    values = row.get("sources") or [row.get("source")]
    return {str(v) for v in values if v}


def prefilter(rows, concepts, cap):
    """Keep the `cap` best lexical matches plus every user/community/citation seed; report the cut."""
    if len(rows) <= cap:
        return rows, 0
    ranked = concept_tools.rank([dict(r) for r in rows], concepts)
    protected = [r for r in ranked if _sources(r) & PROTECTED_SOURCES]
    others = [r for r in ranked if not _sources(r) & PROTECTED_SOURCES]
    kept = protected + others[: max(0, cap - len(protected))]
    return kept, len(rows) - len(kept)


def lexical_scores(rows, concepts):
    """Ranking without a local model: keyword coverage first, then strength. Marked as such everywhere."""
    ranked = concept_tools.rank([dict(r) for r in rows], concepts)
    for position, row in enumerate(ranked, 1):
        row.update({"status": "ok", "jev_rank": position, "ranker": "lexical",
                    "jev_score": round(0.7 * row["coverage"] + 0.3 * min(row["lexical"], 15) / 15, 4)})
    return ranked


def _keys(row):
    keys = set()
    arxiv = arxiv_id_of(row) or row.get("arxiv_id")
    if arxiv:
        keys.add("arxiv:" + arxiv)
    doi = normalize_doi(row.get("doi"))
    if doi:
        keys.add("doi:" + doi)
    title = re.sub(r"[^a-z0-9]+", "", str(row.get("title") or "").lower())
    if len(title) >= 20:
        keys.add("title:" + title)
    return keys


def known_key(value):
    """'2610.22740', 'arXiv:2610.22740', '10.18653/...' or a title -> comparable key."""
    text = str(value or "").strip()
    match = re.search(r"(\d{4}\.\d{4,5})", text)
    if match:
        return "arxiv:" + match.group(1)
    if normalize_doi(text):
        return "doi:" + normalize_doi(text)
    return "title:" + re.sub(r"[^a-z0-9]+", "", text.lower())


def recall_check(known, *pools):
    """Which papers the user expects were found in each pool (e.g. merged vs. in-window)."""
    if not known:
        return None
    result = {"known": len(known), "found": [], "missing": []}
    index = [set().union(*(_keys(r) for r in pool)) if pool else set() for pool in pools]
    for value in known:
        key = known_key(value)
        hits = [key in keys for keys in index]
        (result["found"] if hits and hits[-1] else result["missing"]).append(
            {"paper": value, "in_pools": hits})
    return result


def apply_rerank(rows, sheet, window):
    """Reorder the first `window` eligible rows by agent priority, interleaving clusters for variety.

    sheet: {"<rank position>": {"priority": 1|2|3, "cluster": "..."}}; unlisted rows keep priority 2.
    Rows beyond the window, and policy-excluded rows, keep their order.
    """
    eligible = [i for i, r in enumerate(rows) if not r.get("track_excluded")]
    head, rest = eligible[:window], eligible[window:]
    for i in head:
        note = sheet.get(str(i)) or {}
        priority = note.get("priority", 2)
        if priority not in (1, 2, 3):
            raise ValueError(f"position {i}: priority must be 1, 2 or 3")
        rows[i]["rerank_priority"], rows[i]["rerank_cluster"] = priority, str(note.get("cluster") or "")[:20]
    ordered = []
    for priority in (1, 2, 3):
        tier = [i for i in head if rows[i]["rerank_priority"] == priority]
        buckets = {}
        for i in tier:
            buckets.setdefault(rows[i]["rerank_cluster"], []).append(i)
        while any(buckets.values()):  # round-robin across clusters keeps the reading list varied
            for cluster in list(buckets):
                if buckets[cluster]:
                    ordered.append(buckets[cluster].pop(0))
    excluded = [i for i, r in enumerate(rows) if r.get("track_excluded")]
    new_rows = [rows[i] for i in ordered + rest + excluded]
    for position, row in enumerate(new_rows):
        row["rank_position"] = position
    return new_rows
