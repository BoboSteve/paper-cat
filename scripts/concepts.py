"""Concept groups: one description of a topic, used to build every query and to score candidates.

A topic is a list of groups. Terms inside a group are synonyms (OR); every group should be present
(AND). Example for harness-level self-improvement:

    [["harness", "scaffold"], ["self-evolving", "self-improving", "self-improvement", "evolution"],
     ["agent", "agents"]]

Writing groups is the agent's job during clarification (the user confirms them). Everything else is
mechanical: arXiv field queries, OpenAlex boolean search, and an explainable lexical score.
"""
from __future__ import annotations

import datetime
import math
import re

MAX_GROUPS = 6
MAX_TERMS = 12
STOPWORDS = {"a", "an", "and", "the", "of", "for", "in", "on", "to", "with", "via", "by", "from", "using",
             "towards", "toward", "based", "new", "study", "approach", "method", "methods"}


def _clean_term(term):
    """Keep letters, digits, CJK, spaces and hyphens: no quotes, colons or brackets reach a query."""
    term = re.sub(r"[^\w\s\-一-鿿]", " ", str(term or ""))
    return re.sub(r"\s+", " ", term).strip().lower()[:60]


def normalize_concepts(value, topic=""):
    """Accept [[...], ...], ["a | b", ...] or "a | b; c | d"; fall back to the topic's content words."""
    if isinstance(value, str):
        value = [part for part in value.split(";")]
    groups = []
    for group in value or []:
        terms = group if isinstance(group, (list, tuple)) else str(group).split("|")
        terms = list(dict.fromkeys(t for t in (_clean_term(x) for x in terms) if t))[:MAX_TERMS]
        if terms:
            groups.append(terms)
    if not groups:
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]+", topic) if w.lower() not in STOPWORDS]
        groups = [[w.lower()] for w in dict.fromkeys(words)][:4]
    if not groups:
        raise ValueError("no usable concept terms: give English keywords or concept groups")
    return groups[:MAX_GROUPS]


def _quoted(term):
    return f'"{term}"' if " " in term else term


def arxiv_query(concepts):
    """(ti:a OR abs:a OR ti:b OR abs:b) AND (...): field search with synonyms recalls far better than all-terms."""
    parts = []
    for group in concepts:
        fields = " OR ".join(f"{field}:{_quoted(term)}" for term in group for field in ("ti", "abs"))
        parts.append(f"({fields})")
    return " AND ".join(parts)


def openalex_query(concepts):
    """OpenAlex boolean search over title and abstract: ("a" OR "b") AND ("c" OR "d")."""
    return " AND ".join("(" + " OR ".join(f'"{term}"' for term in group) + ")" for group in concepts)


def _pattern(term):
    """Word-bounded, tolerant of hyphen/space variants and a plural 's' (self-evolving ~ self evolving)."""
    body = r"[\s\-]?".join(re.escape(piece) for piece in re.split(r"[\s\-]+", term))
    return re.compile(rf"(?<![\w]){body}(?:s|es)?(?![\w])", re.I)


class Scorer:
    """Explainable lexical relevance: which groups matched, where, and how often."""

    def __init__(self, concepts):
        self.concepts = concepts
        self.patterns = [[(term, _pattern(term)) for term in group] for group in concepts]

    def score(self, record):
        title = str(record.get("title") or "")
        abstract = str(record.get("abstract") or "")
        matched, points, hits = 0, 0.0, []
        for group in self.patterns:
            best = 0.0
            for term, pattern in group:
                in_title = bool(pattern.search(title))
                count = len(pattern.findall(abstract))
                value = (3.0 if in_title else 0.0) + min(count, 3)
                if value > best:
                    best = value
                if value:
                    hits.append(term)
            if best:
                matched += 1
                points += best
        coverage = matched / len(self.patterns)
        return {"coverage": round(coverage, 3), "lexical": round(points, 2), "matched_terms": sorted(set(hits))}


def rank(records, concepts, today=None):
    """Sort by group coverage, then lexical points, then recency; annotate each record in place."""
    scorer = Scorer(concepts)
    today = today or datetime.date.today()
    for record in records:
        record.update(scorer.score(record))
        record["recency_days"] = _age_days(record.get("published_date"), today)
    return sorted(records, key=lambda r: (-r["coverage"], -r["lexical"], r["recency_days"]))


def _age_days(value, today):
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not match:
        return math.inf
    try:
        return max(0, (today - datetime.date(*map(int, match.groups()))).days)
    except ValueError:
        return math.inf


def exclusion_hints(record, terms):
    """Word-bounded hits of user exclusion terms in title/abstract: a reading prompt, never a decision."""
    text = f"{record.get('title') or ''} {record.get('abstract') or ''}"
    return sorted({term for term in (_clean_term(t) for t in terms or []) if term and _pattern(term).search(text)})
