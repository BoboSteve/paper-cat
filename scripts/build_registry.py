#!/usr/bin/env python3
"""build_registry.py — 三源抓取生成 venue_registry.json（等级映射底座）。

源：ccf-deadlines conference/*.yml（会议等级+dblp键，MIT）
    CCFrank4dblp data/ccfRankUrl.js（期刊+会议 DBLP 路径→A/B/C，MIT）
    ShowJCR CSV（CCF2026 第三校验；FQBJCR2025 中科院终版；XR2026 新锐，GPL——仅运行时使用不随 skill 分发）
输出：venue_registry.json（含快照日期；等级判定 = 载体键+卷/版块，不按前缀）
"""
import argparse, csv, io, json, os, re, subprocess, sys, urllib.request, urllib.parse, datetime

RAW = "https://raw.githubusercontent.com"
def fetch(url, dest, binary=False):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    url = urllib.parse.quote(url, safe=":/%?&=#")
    req = urllib.request.Request(url, headers={"User-Agent": "paper-cat/0.1"})
    tmp = dest + ".part"  # never leave a truncated file that later runs would trust
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        f.write(r.read())
    os.replace(tmp, dest)
    return dest

def fetch_ccfrank(cache):
    """返回 journals+会议 的 dblp 路径 → rank 映射。"""
    url = f"{RAW}/WenyanLiu/CCFrank4dblp/master/data/ccfRankUrl.js"
    p = fetch(url, os.path.join(cache, "ccfrank_ccfRankUrl.js"))
    with open(p, encoding="utf-8") as f:
        text = f.read()
    mapping, by_carrier = {}, {}
    for path, rank in re.findall(r'"(/(?:conf|journals)/[^"]+)":\s*"([ABC])"', text):
        key = path.strip("/")
        parts = key.split("/")
        carrier = "/".join(parts[:2])
        mapping[carrier if len(parts) == 3 and parts[1] == parts[2] else key] = rank
        by_carrier.setdefault(carrier, set()).add(rank)
    for carrier, ranks in by_carrier.items():
        if len(ranks) == 1:
            mapping.setdefault(carrier, next(iter(ranks)))
    return mapping

def fetch_ccf2026(cache):
    """ShowJCR CCF2026 CSV → 列表[{name, rank, kind, dblp_key}]。"""
    url = f"{RAW}/hitfyd/ShowJCR/HEAD/中科院分区表及JCR原始数据文件/CCF2026-UTF8.csv"
    p = fetch(url, os.path.join(cache, "ShowJCR_CCF2026.csv"))
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    out = []
    for r in rows:
        dblp = None
        u = r.get("网址", "")
        m = re.search(r"dblp\.(?:org|uni-trier\.de)/db/((?:conf|journals)/[A-Za-z0-9.+-]+)/?", u)
        if m: dblp = m.group(1)
        out.append({"name": r.get("刊物名称", ""), "full": r.get("Journal", ""),
                    "rank": r.get("CCF推荐类型", "").replace("类", ""),
                    "kind": "conference" if "会议" in r.get("CCF推荐类别（国际学术刊物/会议）", "") else "journal",
                    "dblp_key": dblp})
    return out

def fetch_partitions(cache):
    """FQBJCR2025（中科院终版）+ XR2026（新锐）→ issn → 分区。"""
    parts = {}
    specs = [
        ("FQBJCR2025-UTF8.csv", "cas2025", f"{RAW}/hitfyd/ShowJCR/HEAD/中科院分区表及JCR原始数据文件/FQBJCR2025-UTF8.csv"),
        ("XR2026-UTF8.csv", "xr2026", f"{RAW}/hitfyd/ShowJCR/HEAD/中科院分区表及JCR原始数据文件/XR2026-UTF8.csv"),
    ]
    for fname, key, url in specs:
        p = fetch(url, os.path.join(cache, f"ShowJCR_{fname}"))
        rd = csv.DictReader(open(p, encoding="utf-8"))
        n = 0
        for r in rd:
            issns = []
            for col in ("ISSN/EISSN", "ISSN", "EISSN"):
                v = (r.get(col) or "").strip()
                if v: issns += [x.strip() for x in v.split("/") if x.strip()]
            if not issns: continue
            entry = {}
            if key == "cas2025":
                entry = {"major": r.get("大类", ""), "major_zone": (r.get("大类分区") or "").split(" ")[0],
                         "top": r.get("Top", "") == "是", "minor1": r.get("小类1", "").strip(),
                         "minor1_zone": (r.get("小类1分区") or "").split(" ")[0]}
            else:
                entry = {"major": r.get("大类中文名", ""), "major_zone": (r.get("大类新锐分区") or "").replace("区", "").strip(),
                         "top": (r.get("Top") or "").strip() not in ("", "—", "-"), "warning": bool((r.get("预警标记") or "").strip())}
            for issn in set(issns):
                parts.setdefault(issn, {})[key] = entry
            n += 1
        print(f"  {key}: {n} journals")
    return parts

def clone_ccfddl(cache):
    """稀疏克隆 ccf-deadlines 的 conference/ 目录，解析每个 yml。"""
    repo = os.path.join(cache, "ccf-deadlines")
    try:
        if not os.path.exists(os.path.join(repo, ".git")):
            subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                            "https://github.com/ccfddl/ccf-deadlines", repo], check=True, capture_output=True)
        else:
            subprocess.run(["git", "-C", repo, "pull", "--ff-only"], capture_output=True)
        subprocess.run(["git", "-C", repo, "sparse-checkout", "set", "conference"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        # git is optional: CCFrank4dblp already maps conference DBLP keys to CCF grades.
        print(f"  ccf-deadlines unavailable ({type(exc).__name__}); conference grades come from CCFrank4dblp only")
        if not os.path.isdir(os.path.join(repo, "conference")):
            return {}
    confs = {}
    for root, _, files in os.walk(os.path.join(repo, "conference")):
        for fn in files:
            if not fn.endswith(".yml"): continue
            text = open(os.path.join(root, fn), encoding="utf-8").read()
            if not text.lstrip().startswith("- title:"): continue
            dblp = re.search(r"^\s+dblp:\s*(\S+)", text, re.M)
            if not dblp: continue
            rank = dict(re.findall(r"^\s+(ccf|core|thcpl):\s*([A-Z*]+|N)", text, re.M))
            title = re.search(r"^\s*- title:\s*(.+)$", text, re.M)
            confs[f"conf/{dblp.group(1)}"] = {
                "title": title.group(1).strip() if title else "",
                "ccf": rank.get("ccf", ""), "core": rank.get("core", ""), "thcpl": rank.get("thcpl", "")}
    return confs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default=None)
    a = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import cache_dir
    a.cache_dir = str(cache_dir(a.cache_dir))
    cache = os.path.join(a.cache_dir, "sources")
    os.makedirs(cache, exist_ok=True)
    today = datetime.date.today().isoformat()
    reg_path = os.path.join(a.cache_dir, "venue_registry.json")

    print("[1/4] CCFrank4dblp（期刊+会议等级）...")
    ccfrank = fetch_ccfrank(cache)
    print(f"  {len(ccfrank)} 条 DBLP 路径→等级")
    print("[2/4] ShowJCR CCF2026（第三校验源）...")
    ccf2026 = fetch_ccf2026(cache)
    print(f"  {len(ccf2026)} 条（含 dblp 键 {sum(1 for x in ccf2026 if x['dblp_key'])} 条）")
    print("[3/4] ShowJCR 分区（中科院2025终版 + 新锐2026）...")
    parts = fetch_partitions(cache)
    print(f"  {len(parts)} 个 ISSN")
    print("[4/4] ccf-deadlines（会议等级+录用数据源）...")
    confs = clone_ccfddl(cache)
    print(f"  {len(confs)} 个会议")

    # 交叉核对：ccf-deadlines vs CCFrank（会议部分）
    mismatch, compared = [], 0
    for k, v in confs.items():
        fr = ccfrank.get(k)
        if fr and v["ccf"]: compared += 1
        if fr and v["ccf"] and fr != v["ccf"]:
            mismatch.append({"key": k, "ccfddl": v["ccf"], "ccfrank": fr})
    print(f"  双源实际比较 {compared} 条；等级不一致: {len(mismatch)} 条 → 标记待核实清单")

    registry = {
        "snapshot": today,
        "sources": {
            "ccf-deadlines": {"fetched": today, "license": "MIT", "note": "2026-03-05 同步第七版（公示稿；正式版 03-31，需 diff 复核）"},
            "ccfrank4dblp": {"fetched": today, "license": "MIT", "updated": "2026-04-06"},
            "showjcr": {"fetched": today, "license": "GPL-3.0（仅运行时使用，不随 skill 分发）",
                        "note": "中科院2025终版（已停更）/新锐2026/CCF2026"}},
        "conferences": confs,
        "journals_ccf": {k: v for k, v in ccfrank.items() if k.startswith("journals/") and k.count("/") == 1},
        "ccfrank_conferences": {k: v for k, v in ccfrank.items() if k.startswith("conf/") and k.count("/") == 1},
        "crosscheck_count": compared,
        "conflicts_rank": mismatch,
        "partitions_by_issn": parts,
        "ccf2026_entries": ccf2026,
    }
    tmp = reg_path + ".tmp"
    json.dump(registry, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, reg_path)
    print(f"OK → {reg_path}（快照 {today}）")

if __name__ == "__main__":
    main()
