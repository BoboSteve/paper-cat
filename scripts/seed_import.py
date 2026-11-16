#!/usr/bin/env python3
"""Normalize user references from CSV, JSON/JSONL, BibTeX, RIS, or identifier lists."""
import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path


TITLE_KEYS = ("title", "name", "paper_title")
AUTHOR_KEYS = ("authors", "author", "creators")
YEAR_KEYS = ("year", "date", "published", "publication_year")
DOI_KEYS = ("doi", "DOI")
URL_KEYS = ("url", "link", "paper_url")
ABSTRACT_KEYS = ("abstract", "summary")


def norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def first_value(mapping, keys):
    lowered = {str(key).casefold(): value for key, value in mapping.items()}
    for key in keys:
        value = lowered.get(key.casefold())
        if value not in (None, ""):
            return value
    return ""


def split_authors(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"\s*(?:;|\||\band\b)\s*", str(value or "")) if item.strip()]


def identifier_from_url(url):
    url = str(url or "")
    doi = re.search(r"(?:doi\.org/|doi:\s*)(10\.\d{4,9}/\S+)", url, re.I)
    # arxiv.org/abs/ID, arXiv:ID, or a bare new-style ID on its own
    arxiv = (re.search(r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", url, re.I)
             or re.search(r"arxiv\s*[:：]\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", url, re.I)
             or re.fullmatch(r"\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)\s*", url))
    return (doi.group(1).rstrip(" .") if doi else "", re.sub(r"v\d+$", "", arxiv.group(1)) if arxiv else "")


def normalize_row(raw, seed_role, source_ref):
    title = str(first_value(raw, TITLE_KEYS) or "").strip()
    url = str(first_value(raw, URL_KEYS) or "").strip()
    doi = str(first_value(raw, DOI_KEYS) or "").strip().lower()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi).rstrip("/")
    url_doi, arxiv = identifier_from_url(url)
    arxiv = arxiv or identifier_from_url(str(raw.get("arxiv_id") or raw.get("eprint") or ""))[1]
    doi = doi or url_doi
    year_match = re.search(r"(?:19|20|21)\d{2}", str(first_value(raw, YEAR_KEYS) or ""))
    identity = doi or arxiv or norm(title)
    return {
        "record_id": f"user_seed:{identity}",
        "title": title,
        "authors": split_authors(first_value(raw, AUTHOR_KEYS)),
        "year": int(year_match.group(0)) if year_match else None,
        "doi": doi,
        "arxiv_id": arxiv,
        "abstract": str(first_value(raw, ABSTRACT_KEYS) or "").strip(),
        "url": url,
        "source": "user_seed",
        "source_ref": source_ref,
        "seed_role": seed_role,
        "metadata_source": "user_supplied",
    }


def load_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def load_json(path):
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        for key in ("papers", "items", "data", "references"):
            if isinstance(value.get(key), list):
                return value[key]
        return [value]
    if isinstance(value, list):
        return value
    raise ValueError(f"{path}: JSON root must be an object, list, or supported wrapper")


def load_bib(path):
    source = path.read_text(encoding="utf-8")
    entries = []
    for match in re.finditer(r"@\w+\s*\{[^,]+,\s*(.*?)\n\}", source, re.S):
        body = match.group(1)
        fields = {}
        position = 0
        while True:
            field = re.search(r"(\w+)\s*=\s*", body[position:])
            if not field:
                break
            name = field.group(1).casefold()
            value_start = position + field.end()
            if value_start >= len(body):
                break
            opener = body[value_start]
            if opener == "{":
                depth = 0
                cursor = value_start
                while cursor < len(body):
                    if body[cursor] == "{":
                        depth += 1
                    elif body[cursor] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    cursor += 1
                value = body[value_start + 1:cursor]
                position = cursor + 1
            elif opener == '"':
                cursor = value_start + 1
                while cursor < len(body):
                    if body[cursor] == '"' and body[cursor - 1] != "\\":
                        break
                    cursor += 1
                value = body[value_start + 1:cursor]
                position = cursor + 1
            else:
                end = body.find(",", value_start)
                end = len(body) if end < 0 else end
                value = body[value_start:end]
                position = end + 1
            fields[name] = re.sub(r"\s+", " ", value).strip()
        entries.append({
            "title": fields.get("title", ""),
            "authors": fields.get("author", ""),
            "year": fields.get("year", ""),
            "doi": fields.get("doi", ""),
            "url": fields.get("url", ""),
            "abstract": fields.get("abstract", ""),
        })
    return entries


def load_ris(path):
    entries = []
    current = {}
    authors = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if len(raw) < 2 or raw[2:4] != "  ":
            continue
        tag, value = raw[:2], raw[5:].strip()
        if tag == "TY":
            current = {}
            authors = []
        elif tag in {"T1", "TI", "ST"}:
            current.setdefault("title", value)
        elif tag in {"AU", "A1"}:
            authors.append(value)
        elif tag == "PY":
            current["year"] = value
        elif tag == "DO":
            current["doi"] = value
        elif tag == "UR":
            current.setdefault("url", value)
        elif tag in {"AB", "N2"}:
            current.setdefault("abstract", value)
        elif tag == "ER":
            current["authors"] = authors
            entries.append(current)
    return entries


def load_identifier_list(path):
    entries = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        doi, arxiv = identifier_from_url(value)
        entries.append({
            "title": "",
            "doi": doi or (value if value.startswith("10.") else ""),
            "url": value if value.startswith("http") else (f"https://arxiv.org/abs/{arxiv}" if arxiv else ""),
            "arxiv_id": arxiv,
        })
    return entries


def resolve_missing_titles(rows):
    """Identifier-only seeds (arXiv ID / DOI lists) get real metadata from arXiv or OpenAlex.

    Nothing is invented: seeds that cannot be resolved are reported and left out.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from clue_import import from_openalex, openalex_work, resolve_arxiv
    from common import load_env
    pending = [row for row in rows if not row["title"]]
    if not pending:
        return rows, []
    found = resolve_arxiv([row["arxiv_id"] for row in pending if row["arxiv_id"]])
    settings, unresolved = load_env(), []
    for row in pending:
        meta = found.get(row["arxiv_id"]) if row["arxiv_id"] else None
        if not meta and row["doi"]:
            try:
                works = openalex_work(settings, doi=row["doi"])
                meta = from_openalex(works[0]) if works and works[0].get("title") else None
            except Exception:
                meta = None
        if not meta:
            unresolved.append(row["arxiv_id"] or row["doi"] or row["url"])
            continue
        row.update({key: meta[key] for key in ("title", "abstract", "authors", "published_date", "pdf_url")
                    if meta.get(key)})
        row["doi"] = row["doi"] or meta.get("doi", "")
        row["year"] = row["year"] or int(str(meta.get("published_date") or "0")[:4] or 0) or None
        row["metadata_source"] = "resolved:arxiv" if row["arxiv_id"] else "resolved:openalex"
    return [row for row in rows if row["title"]], unresolved


def load_entries(path):
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return load_csv(path)
    if suffix in {".json", ".jsonl"}:
        return load_json(path)
    if suffix == ".bib":
        return load_bib(path)
    if suffix == ".ris":
        return load_ris(path)
    if suffix in {".txt", ".list"}:
        return load_identifier_list(path)
    raise ValueError(f"{path}: unsupported seed format; use CSV, JSON/JSONL, BibTeX, RIS, or a text identifier list")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, help="repeatable user reference file")
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed-role", default="unknown")
    parser.add_argument("--no-resolve", action="store_true",
                        help="do not look up titles for identifier-only seeds (arXiv/OpenAlex)")
    args = parser.parse_args()
    rows = []
    inputs = []
    for value in args.input:
        path = Path(value)
        entries = load_entries(path)
        inputs.append({
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "entries": len(entries),
        })
        for entry in entries:
            row = normalize_row(entry, args.seed_role, str(path.resolve()))
            if not row["title"] and not row["doi"] and not row["arxiv_id"]:
                continue
            rows.append(row)
    unique = {}
    for row in rows:
        unique.setdefault(row["record_id"], row)
    unresolved = []
    if not args.no_resolve:
        resolved, unresolved = resolve_missing_titles(list(unique.values()))
        unique = {row["record_id"]: row for row in resolved}
    output = sorted(unique.values(), key=lambda row: (row.get("year") or 0, row["title"]), reverse=True)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as stream:
        for row in output:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = Path(args.out).with_suffix(".report.json")
    report.write_text(
        json.dumps({"inputs": inputs, "records": len(rows), "unique": len(output), "unresolved": unresolved}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"SEEDS_FINISHED records={len(rows)} unique={len(output)} unresolved={len(unresolved)} -> {out_path}")


if __name__ == "__main__":
    main()
