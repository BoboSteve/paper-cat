#!/usr/bin/env python3
"""Merge normalized candidate JSONL files with auditable, conservative identity rules."""
import argparse
import datetime
import difflib
import hashlib
import html
import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path


ARXIV_ID = re.compile(
    r"(?<![0-9])(\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v\d+)?(?=$|[^0-9])",
    re.I,
)
DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
FORMAL_TYPES = {"conference", "journal"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def empty(value):
    return value is None or value == "" or value == [] or value == {}


def text(value):
    return str(value or "").strip()


def as_list(value):
    if isinstance(value, list):
        return value
    return [value] if not empty(value) else []


def norm(value):
    value = unicodedata.normalize("NFKC", html.unescape(text(value))).casefold()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def normalize_doi(value):
    value = DOI_PREFIX.sub("", text(value)).strip().rstrip("/").lower()
    return value if value.startswith("10.") else ""


def record_doi(row):
    external = row.get("externalIds") or {}
    return normalize_doi(row.get("doi") or external.get("DOI"))


def record_arxiv(row):
    external = row.get("externalIds") or {}
    values = [
        row.get("arxiv_id"),
        external.get("ArXiv"),
        external.get("ARXIV"),
        row.get("doi") if str(row.get("doi") or "").lower().startswith("10.48550/arxiv.") else "",
    ]
    if text(row.get("source")).lower() == "arxiv":
        values.append(row.get("paper_id"))
    for key in ("url", "pdf_url", "ee"):
        value = text(row.get(key))
        if "arxiv.org" in value.lower():
            values.append(value)
    for value in values:
        match = ARXIV_ID.search(text(value))
        if match:
            return match.group(1).lower()
    return ""


def dblp_values(row):
    values = [(row.get("externalIds") or {}).get("DBLP"), row.get("dblp_key")]
    if text(row.get("source")).lower() == "dblp":
        values.append(row.get("paper_id"))
    result = []
    for value in values:
        value = text(value)
        if value.count("/") >= 2 and not value.startswith("journals/corr/"):
            result.append(value)
    return result


def acl_values(row):
    external = row.get("externalIds") or {}
    values = [row.get("acl_id"), external.get("ACL"), external.get("ACLAnthology")]
    for key in ("url", "ee"):
        match = re.search(r"aclanthology\.org/([^/?#]+)", text(row.get(key)), re.I)
        if match:
            values.append(match.group(1))
    return [text(value).lower() for value in values if text(value)]


def openreview_values(row):
    external = row.get("externalIds") or {}
    values = [row.get("openreview_id"), row.get("forum"), external.get("OpenReview")]
    for key in ("url", "pdf_url", "ee"):
        match = re.search(r"openreview\.net/(?:forum|pdf)\?id=([^&#]+)", text(row.get(key)), re.I)
        if match:
            values.append(match.group(1))
    return [text(value) for value in values if text(value)]


def identity_keys(row):
    keys = []
    doi = record_doi(row)
    arxiv = record_arxiv(row)
    if doi:
        keys.append(("doi:" + doi).casefold())
    if arxiv:
        keys.append(("arxiv:" + arxiv).casefold())
    keys.extend(("dblp:" + value).casefold() for value in dblp_values(row))
    keys.extend(("acl:" + value).casefold() for value in acl_values(row))
    keys.extend(("openreview:" + value).casefold() for value in openreview_values(row))
    manual = text(row.get("merge_group"))
    if manual:
        keys.append(("manual:" + manual).casefold())
    return list(dict.fromkeys(keys))


def record_year(row):
    for key in ("year", "publicationDate", "publication_date", "published_date"):
        match = re.search(r"(?:19|20|21)\d{2}", text(row.get(key)))
        if match:
            return int(match.group(0))
    return None


def first_author(row):
    authors = row.get("authors")
    if isinstance(authors, str):
        authors = [part.strip() for part in authors.split(";") if part.strip()]
    if not isinstance(authors, list) or not authors:
        return ""
    author = unicodedata.normalize("NFKC", text(authors[0]))
    if "," in author:
        author = author.split(",", 1)[0]
    words = re.findall(r"[\w]+", author, flags=re.UNICODE)
    return norm(words[-1] if words else author)


def formal_venue(row):
    row_type = text(row.get("record_type")).lower()
    if row_type == "preprint":
        return ""
    dblp = dblp_values(row)
    if row_type not in FORMAL_TYPES and not dblp and not text(row.get("book")):
        venue = text(row.get("venue"))
        if not venue or venue.lower().startswith("arxiv"):
            return ""
    for value in (row.get("venue"), row.get("book")):
        value = text(value)
        if value and not value.lower().startswith("arxiv"):
            return norm(value)
    if dblp:
        return norm("/".join(dblp[0].split("/")[:2]))
    return "<unknown>" if row_type in FORMAL_TYPES else ""


def title_compatible(left, right):
    left_year, right_year = record_year(left), record_year(right)
    if left_year and right_year and left_year != right_year:
        return False
    left_author, right_author = first_author(left), first_author(right)
    if left_author and right_author and left_author != right_author:
        return False
    left_venue, right_venue = formal_venue(left), formal_venue(right)
    if left_venue and right_venue:
        return left_venue == right_venue and left_venue != "<unknown>"
    if bool(left_venue) != bool(right_venue):
        return True
    return True


def near_title_similarity(left, right):
    left_norm, right_norm = norm(left), norm(right)
    if not left_norm or not right_norm:
        return 0.0
    shorter, longer = sorted((left_norm, right_norm), key=len)
    if len(shorter) / len(longer) >= 0.5 and shorter in longer:
        return 1.0
    sequence = difflib.SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = set(re.findall(r"[a-z0-9]+", text(left).casefold()))
    right_tokens = set(re.findall(r"[a-z0-9]+", text(right).casefold()))
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    return max(sequence, jaccard)


class UnionFind:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, value):
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left

    def groups(self):
        result = defaultdict(list)
        for index in range(len(self.parent)):
            result[self.find(index)].append(index)
        return list(result.values())


def load_inputs(items):
    rows = []
    inputs = []
    for label, path in items:
        source = Path(path)
        source_rows = []
        for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as error:
                raise ValueError(f"{label}:{line_number}: invalid JSON: {error}") from error
            if not isinstance(row, dict) or not text(row.get("title")):
                raise ValueError(f"{label}:{line_number}: record must be an object with a title")
            source_rows.append(row)
        if not source_rows:
            raise ValueError(f"{label}: input contains no records")
        input_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        inputs.append({"label": label, "path": str(source), "rows": len(source_rows), "sha256": input_hash})
        for index, row in enumerate(source_rows):
            rows.append({
                **row,
                "_input_label": label,
                "_input_index": len(rows),
                "_source_index": index,
                "_row_sha256": digest(row),
            })
    return rows, inputs


def member_evidence(row):
    item = {
        "input": row["_input_label"],
        "input_row": row["_source_index"],
        "record_sha256": row["_row_sha256"],
        "source": text(row.get("source")),
        "title": text(row.get("title")),
        "doi": record_doi(row),
        "arxiv_id": record_arxiv(row),
        "dblp_key": (dblp_values(row) or [""])[0],
        "acl_id": (acl_values(row) or [""])[0],
        "openreview_id": (openreview_values(row) or [""])[0],
        "query_id": text(row.get("query_id")),
        "query_hash": text(row.get("query_hash")),
        "raw_path": text(row.get("psm_raw_path")),
    }
    return {key: value for key, value in item.items() if not empty(value)}


def group_flags(members):
    flags = []
    if len({norm(row.get("title")) for row in members}) > 1:
        flags.append("title_conflict")
    formal_dois = {record_doi(row) for row in members}
    formal_dois = {value for value in formal_dois if value and not value.startswith("10.48550/arxiv.")}
    if len(formal_dois) > 1:
        flags.append("multiple_formal_dois")
    if len({record_arxiv(row) for row in members if record_arxiv(row)}) > 1:
        flags.append("multiple_arxiv_ids")
    if len({record_year(row) for row in members if record_year(row)}) > 1:
        flags.append("multiple_years")
    venues = {formal_venue(row) for row in members if formal_venue(row)}
    if len(venues) > 1:
        flags.append("multiple_venues")
    return flags


def canonical_record_id(members):
    values = defaultdict(list)
    for row in members:
        if record_doi(row):
            values["doi"].append(record_doi(row))
        if record_arxiv(row):
            values["arxiv"].append(record_arxiv(row))
        for value in dblp_values(row):
            values["dblp"].append(value)
        for value in acl_values(row):
            values["acl"].append(value)
        for value in openreview_values(row):
            values["openreview"].append(value)
    for kind in ("doi", "arxiv", "dblp", "acl", "openreview"):
        if values[kind]:
            return f"{kind}:{values[kind][0]}"
    basis = "\0".join([
        norm(members[0].get("title")),
        text(record_year(members[0])),
        first_author(members[0]),
        formal_venue(members[0]),
    ])
    return "candidate-" + hashlib.sha1(basis.encode()).hexdigest()[:16]


def merge_members(members):
    merged = {}
    for row in members:
        for key, value in row.items():
            if key.startswith("_") or key in {"provenance", "merge_flags", "merge_evidence"}:
                continue
            if empty(merged.get(key)) and not empty(value):
                merged[key] = value
    external = {}
    for row in members:
        for key, value in (row.get("externalIds") or {}).items():
            if not empty(value):
                external.setdefault(key, value)
    if external:
        merged["externalIds"] = external
    abstracts = [text(row.get("abstract")) for row in members if text(row.get("abstract"))]
    if abstracts:
        merged["abstract"] = max(abstracts, key=len)
    flags = []
    for row in members:
        values = row.get("flags") or []
        values = [values] if isinstance(values, str) else values
        flags.extend(value for value in values if value not in flags)
    merge_flags = group_flags(members)
    if flags or merge_flags:
        merged["flags"] = list(dict.fromkeys(flags + merge_flags))

    identities = [set(identity_keys(row)) for row in members]
    counts = defaultdict(int)
    for keys in identities:
        for key in keys:
            counts[key] += 1
    shared = sorted(key for key, count in counts.items() if count > 1)
    title_match = len({norm(row.get("title")) for row in members}) == 1
    if len(members) > 1 and not shared and title_match:
        merge_flags.append("title_only_merge")
        merged["flags"] = list(dict.fromkeys(merged.get("flags", []) + merge_flags))

    merged["record_id"] = canonical_record_id(members)
    merged["source_inputs"] = list(dict.fromkeys(row["_input_label"] for row in members))
    merged["sources"] = list(dict.fromkeys(text(row.get("source")) for row in members if text(row.get("source"))))
    merged["query_ids"] = list(dict.fromkeys(
        [text(row.get("query_id")) for row in members if text(row.get("query_id"))]
        + [text(item) for row in members for item in as_list(row.get("query_ids")) if text(item)]
    ))
    merged["query_hashes"] = list(dict.fromkeys(
        [text(row.get("query_hash")) for row in members if text(row.get("query_hash"))]
        + [text(item) for row in members for item in as_list(row.get("query_hashes")) if text(item)]
    ))
    merged["merged_from"] = len(members)
    merged["provenance"] = [member_evidence(row) for row in members]
    merged["merge_evidence"] = {
        "shared_identities": shared,
        "title_match": title_match,
        "identities": {kind: values for kind, values in sorted(
            ((kind, sorted({key.split(":", 1)[1] for keys in identities for key in keys
                            if key.startswith(kind + ":")}))
             for kind in ("doi", "arxiv", "dblp", "acl", "openreview", "manual")),
            key=lambda item: item[0],
        ) if values},
    }
    return merged


def merge(items, near_threshold=0.86):
    rows, inputs = load_inputs(items)
    union = UnionFind(len(rows))
    key_owner = {}
    for index, row in enumerate(rows):
        for key in identity_keys(row):
            if key in key_owner:
                union.union(key_owner[key], index)
            else:
                key_owner[key] = index

    title_rows = defaultdict(list)
    for index, row in enumerate(rows):
        title = norm(row.get("title"))
        if title:
            title_rows[title].append(index)
    for indexes in title_rows.values():
        for offset, left_index in enumerate(indexes):
            for right_index in indexes[offset + 1:]:
                left_root, right_root = union.find(left_index), union.find(right_index)
                if left_root == right_root:
                    continue
                left = [rows[index] for index in range(len(rows)) if union.find(index) == left_root]
                right = [rows[index] for index in range(len(rows)) if union.find(index) == right_root]
                if all(title_compatible(a, b) for a in left for b in right):
                    union.union(left_root, right_root)

    candidates = []
    report_groups = []
    for indexes in sorted(union.groups(), key=min):
        members = [rows[index] for index in indexes]
        merged = merge_members(members)
        candidates.append(merged)
        report_groups.append({
            "record_id": merged["record_id"],
            "title": merged.get("title"),
            "merged_from": len(members),
            "input_labels": merged["source_inputs"],
            "sources": merged["sources"],
            "query_ids": merged["query_ids"],
            "years": sorted({record_year(row) for row in members if record_year(row)}),
            "first_authors": sorted({first_author(row) for row in members if first_author(row)}),
            "formal_venues": sorted({formal_venue(row) for row in members if formal_venue(row)}),
            "merge_flags": merged.get("flags", []),
            "records": merged["provenance"],
        })

    title_groups = defaultdict(list)
    for group in report_groups:
        title_groups[norm(group["title"])].append(group)
    possible_duplicates = []
    for title, groups in title_groups.items():
        if title and len(groups) > 1:
            possible_duplicates.append({
                "normalized_title": title,
                "groups": [{
                    "record_id": group["record_id"],
                    "years": group["years"],
                    "first_authors": group["first_authors"],
                    "formal_venues": group["formal_venues"],
                } for group in groups],
            })
    near_duplicate_candidates = []
    by_author = defaultdict(list)
    for group in report_groups:
        for author in group["first_authors"]:
            by_author[author].append(group)
    for author, groups in by_author.items():
        for offset, left in enumerate(groups):
            for right in groups[offset + 1:]:
                left_years = [value for value in left["years"] if value]
                right_years = [value for value in right["years"] if value]
                if left_years and right_years and abs(min(left_years) - min(right_years)) > 5:
                    continue
                left_venue = set(left["formal_venues"])
                right_venue = set(right["formal_venues"])
                if left_venue and right_venue and not (left_venue & right_venue):
                    continue
                score = near_title_similarity(left["title"], right["title"])
                if score < near_threshold:
                    continue
                near_duplicate_candidates.append({
                    "first_author": author,
                    "title_similarity": round(score, 4),
                    "left": {
                        "record_id": left["record_id"],
                        "title": left["title"],
                        "years": left["years"],
                        "formal_venues": left["formal_venues"],
                    },
                    "right": {
                        "record_id": right["record_id"],
                        "title": right["title"],
                        "years": right["years"],
                        "formal_venues": right["formal_venues"],
                    },
                    "action": "review_then_assign_merge_group_if_same_work",
                })
    report = {
        "created_at": now(),
        "inputs": inputs,
        "counts": {
            "input_rows": len(rows),
            "candidates": len(candidates),
            "merged_rows": len(rows) - len(candidates),
            "multi_record_groups": sum(item["merged_from"] > 1 for item in report_groups),
            "groups_with_review_flags": sum(bool(item["merge_flags"]) for item in report_groups),
            "possible_duplicate_groups": len(possible_duplicates),
            "near_duplicate_candidates": len(near_duplicate_candidates),
        },
        "groups": report_groups,
        "possible_duplicates": possible_duplicates,
        "near_duplicate_candidates": near_duplicate_candidates,
    }
    return candidates, report


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def parse_input(value):
    if "=" not in value:
        raise ValueError("--input must use LABEL=PATH")
    label, path = value.split("=", 1)
    label = label.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", label):
        raise ValueError("input label must contain only letters, digits, dot, underscore, or hyphen")
    if not path.strip():
        raise ValueError("input path is empty")
    return label, path.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, help="LABEL=PATH; repeat in desired priority order")
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--near-threshold", type=float, default=0.86)
    args = parser.parse_args()
    try:
        if not 0 < args.near_threshold <= 1:
            raise ValueError("--near-threshold must be in (0, 1]")
        items = [parse_input(value) for value in args.input]
        labels = [label for label, _ in items]
        if len(labels) != len(set(labels)):
            raise ValueError("input labels must be unique")
        output, report_path = Path(args.out), Path(args.report)
        if output.resolve() == report_path.resolve():
            raise ValueError("--out and --report must be different files")
        inputs = {Path(path).resolve() for _, path in items}
        if output.resolve() in inputs or report_path.resolve() in inputs:
            raise ValueError("outputs must not overwrite an input file")
        candidates, report = merge(items, args.near_threshold)
        write_jsonl(output, candidates)
        write_json(report_path, report)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    counts = report["counts"]
    print(
        f"MERGE_FINISHED input={counts['input_rows']} candidates={counts['candidates']} "
        f"merged={counts['merged_rows']} possible_duplicates={counts['possible_duplicate_groups']} "
        f"near_duplicates={counts['near_duplicate_candidates']}"
    )


if __name__ == "__main__":
    main()
