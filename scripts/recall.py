#!/usr/bin/env python3
"""recall.py — 路径 B 主题召回（Semantic Scholar Graph API）。

结果逐条落盘 JSONL（防幻觉：交付物只能引用这里缓存的原始响应）。
无 DOI 顶会（NeurIPS/ICML/ICLR）靠 externalIds 携带的 DBLP 键关联等级。
"""
import argparse, json, os, re, sys, time, hashlib, urllib.request, urllib.parse

S2 = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,authors,year,venue,externalIds,abstract,citationCount,openAccessPdf,tldr,publicationTypes,publicationDate"

def s2_get(url, retries=4):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paper-cat/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503):
                wait = 5 * (i + 1)
                print(f"  S2 {e.code}，退避 {wait}s（第 {i+1} 次）", file=sys.stderr)
                time.sleep(wait); continue
            raise
    return None

def abstract_from_inverted(inv):
    if not inv: return ""
    pos = {}
    for w, idxs in inv.items():
        for i in idxs: pos[i] = w
    return " ".join(pos[i] for i in sorted(pos))

def openalex_search(query, limit, year, cache_dir):
    OA = "https://api.openalex.org/works"
    flt = []
    if year:
        y0 = year.split("-")[0]
        flt.append(f"from_publication_date:{y0}-01-01")
    params = {"search": query, "per-page": min(limit, 50), "select": "id,doi,title,authorships,publication_year,primary_location,abstract_inverted_index,cited_by_count,open_access,best_oa_location,ids"}
    if flt: params["filter"] = ",".join(flt)
    url = OA + "?" + urllib.parse.urlencode(params)
    cache_file = os.path.join(cache_dir, "api", "oa_search", hashlib.md5(url.encode()).hexdigest() + ".json")
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    if os.path.exists(cache_file):
        return json.load(open(cache_file, encoding="utf-8")), True
    req = urllib.request.Request(url, headers={"User-Agent": "paper-cat/0.1 (mailto:paper-cat@localhost)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    json.dump(data, open(cache_file, "w", encoding="utf-8"), ensure_ascii=False)
    return data, False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--year", default="", help="如 2023- 或 2024-2026")
    ap.add_argument("--source", choices=["s2", "openalex", "auto"], default="auto")
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/paper-cat"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    api_cache = os.path.join(a.cache_dir, "api", "s2_search")
    os.makedirs(api_cache, exist_ok=True)

    records, used = [], None
    if a.source in ("s2", "auto"):
        params = {"query": a.query, "fields": FIELDS, "limit": min(a.limit, 100)}
        if a.year: params["year"] = a.year
        url = S2 + "?" + urllib.parse.urlencode(params)
        cache_file = os.path.join(api_cache, hashlib.md5(url.encode()).hexdigest() + ".json")
        data = None
        if os.path.exists(cache_file):
            data = json.load(open(cache_file, encoding="utf-8")); print("（S2 命中缓存）")
        else:
            data = s2_get(url)
        if data:
            used = "s2"
            for p in (data.get("data") or []):
                if not p.get("title"): continue
                records.append({
                    "id_s2": p.get("paperId"), "title": p["title"],
                    "authors": [au.get("name") for au in (p.get("authors") or [])[:6]],
                    "year": p.get("year"), "venue": p.get("venue") or "",
                    "externalIds": p.get("externalIds") or {},
                    "abstract": p.get("abstract") or "",
                    "tldr": (p.get("tldr") or {}).get("text"),
                    "citations": p.get("citationCount"),
                    "openAccessPdf": (p.get("openAccessPdf") or {}).get("url"),
                    "publicationDate": p.get("publicationDate"),
                    "source": "s2_search", "query": a.query})
        elif a.source == "s2":
            sys.exit("S2 多次失败；用 --source auto 启用 OpenAlex 回退")
    if not records and a.source in ("openalex", "auto"):
        data, cached = openalex_search(a.query, a.limit, a.year, a.cache_dir)
        used = "openalex" + ("（缓存）" if cached else "")
        for p in (data.get("results") or []):
            if not p.get("title"): continue
            src = ((p.get("primary_location") or {}).get("source") or {})
            records.append({
                "id_openalex": (p.get("ids") or {}).get("openalex"),
                "doi": (p.get("doi") or "").replace("https://doi.org/", ""),
                "title": p["title"],
                "authors": [au.get("author", {}).get("display_name") for au in (p.get("authorships") or [])[:6]],
                "year": p.get("publication_year"),
                "venue": src.get("display_name") or "",
                "venue_issn": (src.get("issn") or [None])[0],
                "externalIds": ({("DOI" if p.get("doi") else ""): p.get("doi", "").replace("https://doi.org/", "")} if p.get("doi") else {}),
                "abstract": abstract_from_inverted(p.get("abstract_inverted_index")),
                "tldr": None, "citations": p.get("cited_by_count"),
                "openAccessPdf": ((p.get("best_oa_location") or {}).get("pdf_url")),
                "publicationDate": None,
                "source": "openalex", "query": a.query})

    n = 0
    with open(a.out, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); n += 1
    print(f"召回源 {used}；{n} 条 → {a.out}")

if __name__ == "__main__":
    main()
