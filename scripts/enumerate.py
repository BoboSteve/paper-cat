#!/usr/bin/env python3
"""enumerate.py — 路径 A 目录枚举（dblp SPARQL，日更、无 Anubis）。

按卷级 TOC 页精确枚举（主会与 Workshop 天然隔离）：
  https://dblp.org/db/conf/<venue>/<venue><year>
输出 catalog.jsonl + 覆盖统计（目录条目数，供"找全"口径）。
"""
import argparse, json, os, sys, time, urllib.request, urllib.parse

SPARQL = "https://sparql.dblp.org/sparql"

QUERY = """PREFIX dblp: <https://dblp.org/rdf/schema#>
SELECT ?paper ?title ?year ?book ?doi ?note
       (GROUP_CONCAT(DISTINCT ?ee; separator=" ") AS ?ees)
       (GROUP_CONCAT(DISTINCT ?an; separator="; ") AS ?authors)
WHERE {{
  ?paper dblp:listedOnTocPage <{toc}> ;
         dblp:title ?title ; dblp:yearOfPublication ?year ; dblp:publishedInBook ?book .
  OPTIONAL {{ ?paper dblp:doi ?doi }}
  OPTIONAL {{ ?paper dblp:note ?note }}
  OPTIONAL {{ ?paper dblp:electronicEdition ?ee }}
  OPTIONAL {{ ?paper dblp:authoredBy ?a . ?a dblp:creatorName ?an }}
}} GROUP BY ?paper ?title ?year ?book ?doi ?note"""

def run_query(q, cache_dir, tag):
    os.makedirs(os.path.join(cache_dir, "api", "dblp_sparql"), exist_ok=True)
    cf = os.path.join(cache_dir, "api", "dblp_sparql", tag + ".json")
    if os.path.exists(cf):
        return json.load(open(cf, encoding="utf-8"))
    url = SPARQL + "?" + urllib.parse.urlencode({"query": q})
    req = urllib.request.Request(url, headers={"Accept": "application/sparql-results+json",
                                               "User-Agent": "paper-cat/0.1"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    json.dump(data, open(cf, "w", encoding="utf-8"), ensure_ascii=False)
    return data

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", required=True, help="如 conf/iclr")
    ap.add_argument("--year", required=True)
    ap.add_argument("--volume", default="main", help="main=venue+year；或完整卷 id 如 iclr2026w")
    ap.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/paper-cat"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    v = a.venue.split("/")[-1]
    vol = f"{v}{a.year}" if a.volume == "main" else a.volume
    toc = f"https://dblp.org/db/{a.venue}/{vol}"
    data = run_query(QUERY.format(toc=toc), a.cache_dir, f"{a.venue.replace('/','_')}_{vol}")
    rows = []
    for b in data["results"]["bindings"]:
        key = b["paper"]["value"].replace("https://dblp.org/rec/", "")
        rows.append({
            "dblp_key": key, "dblp_volume": vol,
            "title": b["title"]["value"].rstrip("."),
            "year": b["year"]["value"], "book": b.get("book", {}).get("value", ""),
            "doi": b.get("doi", {}).get("value", "").replace("https://doi.org/", ""),
            "note": b.get("note", {}).get("value", ""),
            "ee": b.get("ees", {}).get("value", ""),
            "authors": [x for x in b.get("authors", {}).get("value", "").split("; ") if x],
            "source": f"dblp_sparql:{vol}",
        })
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    informal = sum(1 for r in rows if "informal" in r["note"].lower())
    print(f"枚举 {len(rows)} 条（{toc}）→ {a.out}")
    if informal: print(f"  含 informal 标记 {informal} 条")
    print(f"  覆盖口径：dblp 卷目录条目数 = {len(rows)}（主会/Workshop 按 TOC 页隔离）")

if __name__ == "__main__":
    main()
