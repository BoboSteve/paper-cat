#!/usr/bin/env python3
"""rank.py — 载体身份识别 + 等级/分区标注（只查表，模型不得填写）。

判定链：externalIds.DBLP（S2）> venue 名匹配（词元包含评分，长名优先）
等级三源交叉：ccf-deadlines / CCFrank / ShowJCR CCF2026；不一致 → 待核实
分区（仅期刊）：ISSN → 中科院2025终版 + 新锐2026（体系显式，键归一化）
"""
import argparse, json, os, re, sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARXIV_ISSN = "2331-8422"
# 手工别名：norm(venue) → dblp 键（None = 已知无法映射，标待核实）
MANUAL_ALIAS = {
    "neurips": "conf/nips", "nips": "conf/nips",
    "advancesinneuralinformationprocessingsystems": "conf/nips",
    "neuralinformationprocessingsystems": "conf/nips",
    "icml": "conf/icml", "internationalconferenceonmachinelearning": "conf/icml",
    "proceedingsofmachinelearningresearch": None, "machinelearningresearch": None, "pmlr": None,
    "iclr": "conf/iclr", "internationalconferenceonlearningrepresentations": "conf/iclr",
    "tmlr": "journals/tmlr", "transactionsonmachinelearningresearch": "journals/tmlr",
    "arxivcornelluniversity": None, "arxiv": None, "arxivorg": None,
    "openreview": None,
}

def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def tokens(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower())) - {"of", "the", "for", "on", "in"}

def ccf2026_ranks(entries):
    ranks = {}
    for entry in entries:
        if entry.get("dblp_key"):
            ranks.setdefault(entry["dblp_key"], set()).add(entry["rank"])
    return ranks


def build_name_index(registry):
    """[(norm, tokens, key, kind)]：来自 ccf-deadlines 标题与 ShowJCR CCF2026 名称。"""
    entries = []
    for key, v in registry["conferences"].items():
        for name in [v.get("title"), key.split("/")[-1]]:
            if name and len(norm(name)) >= 3:
                entries.append((norm(name), tokens(name), key, "conference"))
    ranks = ccf2026_ranks(registry["ccf2026_entries"])
    for e in registry["ccf2026_entries"]:
        if e.get("dblp_key") and len(ranks[e["dblp_key"]]) == 1:
            for name in [e.get("name"), e.get("full")]:
                if name and len(norm(name)) >= 3:
                    entries.append((norm(name), tokens(name), e["dblp_key"], e["kind"]))
    return entries

def match_venue(venue_str, index):
    """精确 norm 命中 > 词元子集评分（≥0.8 且词元数≥2，长键优先）。"""
    if not venue_str: return None, None
    n = norm(venue_str)
    if n in MANUAL_ALIAS:
        k = MANUAL_ALIAS[n]; return k, ("conference" if k and k.startswith("conf/") else ("journal" if k else None))
    exact = [(e[2], e[3]) for e in index if e[0] == n]
    if exact: return exact[0]
    vt = tokens(venue_str)
    if not vt: return None, None
    best = None
    for _, kt, key, kind in index:
        if len(kt) < 2: continue
        # 键词元全部出现在 venue 中，且覆盖 venue 词元的 80% 以上：
        # 防止 "Artificial Intelligence" 吃掉 "Artificial Intelligence Review"
        if kt <= vt and len(kt) / len(vt) >= 0.8:
            cand = (len(kt), key, kind)
            if best is None or cand[0] > best[0]: best = cand
    if best: return best[1], best[2]
    # "ACM SIGSAC Conference on Computer and Communications Security (CCS)": a bracketed acronym that
    # names exactly one listed conference. Name matches are always flagged for review downstream.
    if re.search(r"workshop|findings|companion|adjunct|extended abstract|doctoral|demo|poster|symposium on", venue_str, re.I):
        return None, None  # satellite volumes must not borrow the main conference's acronym
    for acronym in re.findall(r"\(([A-Za-z][A-Za-z0-9&+-]{1,15})\)", venue_str):
        keys = {e[2] for e in index if e[0] == norm(acronym) and e[3] == "conference"}
        if len(keys) == 1:
            return keys.pop(), "conference"
    return None, None

def volume_track(volume, rules):
    if not volume: return "unknown"
    suf = rules["track_rules"]["volume_suffix"]; kw = rules["track_rules"]["volume_contains"]
    m = re.search(r"(\d{4})([a-z]*)$", volume)
    if m and m.group(2): return suf.get(m.group(2), "unknown")
    low = volume.lower()
    for k, v in kw.items():
        if k in low: return v
    return "main"

# Track evidence that is stronger than the DBLP volume-name heuristic and must not be overwritten.
STRONG_TRACK_SOURCES = {"acl_anthology_id", "official_catalog", "openreview", "dblp_volume"}
TRACK_POLICIES = {
    "main_only": {"main"},
    "include_findings": {"main", "findings"},
    "all": None,  # every track is forwarded; non-main tracks still never inherit a CCF grade
}


def track_policy_from(plan_path, explicit):
    if explicit:
        return explicit
    if plan_path and os.path.exists(plan_path):
        plan = json.load(open(plan_path, encoding="utf-8"))
        return plan.get("track_policy") or "main_only"
    return "main_only"


def lookup_grade(dblp_key, confs, journals, registry, ccf2026):
    cand = []
    v = confs.get(dblp_key)
    if v and v.get("ccf"): cand.append((v["ccf"], "ccf-deadlines"))
    j = journals.get(dblp_key) or registry.get("ccfrank_conferences", {}).get(dblp_key)
    if j: cand.append((j, "ccfrank"))
    for rank in sorted(ccf2026.get(dblp_key) or ()):
        cand.append((rank, "showjcr-ccf2026"))
    return cand


def annotate(rec, ctx, policy):
    index, rules, confs, journals, registry, ccf2026, parts = ctx
    dblp_key, volume, carrier = None, None, None
    ext = rec.get("externalIds") or {}
    doi = rec.get("doi") or ext.get("DOI") or ""
    is_arxiv_doi = doi.lower().startswith("10.48550/")
    matched_kind = None
    dpath = ext.get("DBLP") or ""
    if rec.get("venue_key"):
        carrier = rec["venue_key"]
    elif dpath:
        seg = dpath.split("/")
        carrier = "/".join(seg[:2])
        volume = rec.get("dblp_volume")
    if not carrier:
        key, kind = match_venue(rec.get("venue") or "", index)
        if key: dblp_key, matched_kind = key, kind
    name_matched = bool(dblp_key) and not dpath and not rec.get("venue_key")
    if carrier == "journals/corr":
        carrier, volume = None, None
    alias_to, ambiguous_carrier = None, False
    if carrier and carrier in rules["carrier_alias"]:
        alias_to = rules["carrier_alias"][carrier]["venue"]
        ambiguous_carrier = bool(alias_to and "|" in alias_to)
        if alias_to and "|" not in alias_to:
            dblp_key = alias_to; matched_kind = "conference"
    issn = (rec.get("venue_issn") or "").replace("-", "").lower()
    # 载体判定优先级：正式键（ACL ID / DBLP / 目录）> 载体别名 > venue 名匹配 > arXiv 启发式
    if carrier and (carrier.startswith("conf/") or (alias_to and not ambiguous_carrier)):
        rec_type = "conference"; dblp_key = dblp_key or carrier
    elif carrier and carrier.startswith("journals/"):
        rec_type = "journal"; dblp_key = dblp_key or carrier
    elif matched_kind:
        rec_type = matched_kind
    elif rec.get("venue_source_type") in ("journal", "conference"):
        rec_type = rec["venue_source_type"]  # formal venue outside the CCF list: no grade, partition by ISSN
    elif (is_arxiv_doi or issn == ARXIV_ISSN or str(rec.get("venue") or "").lower().startswith("arxiv")
          or "arxiv.org" in str(rec.get("url") or "").lower() or "arxiv.org" in str(rec.get("pdf_url") or "").lower()):
        rec_type = "preprint"
    else:
        rec_type = "unknown"

    grade, grade_src, flags, provisional = None, [], [], None
    if name_matched and dblp_key:
        flags.append("等级经 venue 名匹配（非 DBLP 键），建议复核：venue 字符串可能是论文集母刊名")
    if rec_type in ("conference", "journal") and dblp_key:
        cand = lookup_grade(dblp_key, confs, journals, registry, ccf2026)
        ranks = {r for r, _ in cand}
        if len(ranks) == 1:
            grade = cand[0][0]; grade_src = [s for _, s in cand]
        elif len(ranks) > 1:
            flags.append("等级三源不一致: " + json.dumps(cand, ensure_ascii=False) + " → 待核实")
        if dblp_key == "conf/ijcai":
            flags.append("等级变动：IJCAI 第七版由 A 降 B（2024 前论文发表时 A，现行 B）")
    partition = None
    if rec_type == "journal" and issn and issn != ARXIV_ISSN:
        partition = parts.get(issn)

    strong_track = rec.get("track") if rec.get("track_source") in STRONG_TRACK_SOURCES else None
    if strong_track:
        track, track_source = strong_track, rec["track_source"]
    elif rec_type == "conference":
        track, track_source = volume_track(volume, rules), ("dblp_volume_name" if volume else "none")
    else:
        track, track_source = ("journal" if rec_type == "journal" else "unknown"), "record_type"

    main_venue_grade = grade
    if rec_type == "conference" and track not in ("main", "unknown"):
        flags.append(f"轨道={track}：CCF 第七版只计主会，不继承 {dblp_key} 的等级")
        grade, grade_src = None, []
    if rec_type == "conference" and track == "unknown":
        if grade:
            provisional = grade
            flags.append(f"会议等级 {grade} 来自载体，但缺少轨道证据 → 待核实（可能是 Findings/Workshop）")
        grade, grade_src = None, []
    if ambiguous_carrier:
        flags.append("载体对应多个会议，缺少卷/期映射 → 等级待核实")
        grade, grade_src, provisional = None, [], None

    claimed_grade = None
    if rec_type in ("preprint", "unknown") and rec.get("claimed_venue"):
        key, kind = match_venue(re.sub(r"\b(19|20)\d{2}\b", "", rec["claimed_venue"]), index)
        if key:
            ranks = {r for r, _ in lookup_grade(key, confs, journals, registry, ccf2026)}
            claimed_grade = next(iter(ranks)) if len(ranks) == 1 else None
        flags.append(f"arXiv 备注声称发表于 {rec['claimed_venue']}（未经目录核实）")

    allowed = TRACK_POLICIES[policy]
    excluded = bool(rec_type == "conference" and allowed is not None and track not in allowed and track != "unknown")
    return {**rec, "dblp_key": dblp_key, "dblp_volume": volume, "record_type": rec_type,
            "track": track, "track_source": track_source, "ccf_grade": grade, "ccf_grade_sources": grade_src,
            "ccf_grade_provisional": provisional,
            "main_venue_grade": main_venue_grade if track not in ("main", "unknown", "journal") else None,
            "claimed_ccf_grade": claimed_grade, "partition": partition, "flags": flags, "issn": issn or None,
            "track_excluded": excluded,
            "excluded_reason": f"轨道 {track} 不在筛选策略 {policy} 内" if excluded else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--cache-dir", help="defaults to $PAPERCAT_CACHE, runtime.local.json, or ~/.cache/paper-cat")
    ap.add_argument("--plan", help="plan.json; supplies track_policy")
    ap.add_argument("--track-policy", choices=sorted(TRACK_POLICIES),
                    help="main_only (default) | include_findings | all")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-missing-registry", action="store_true",
                    help="continue without grade tables instead of stopping (grades stay empty)")
    a = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import cache_dir
    registry_path = os.path.join(str(cache_dir(a.cache_dir)), "venue_registry.json")
    if os.path.exists(registry_path):
        registry = json.load(open(registry_path, encoding="utf-8"))
    elif a.allow_missing_registry:
        # No grade tables available (offline first run): annotate venues and tracks, but never invent grades.
        registry = {"snapshot": None, "conferences": {}, "journals_ccf": {}, "ccfrank_conferences": {},
                    "ccf2026_entries": [], "partitions_by_issn": {}}
        print("  未找到等级名录：本次不标注 CCF 等级与期刊分区（可稍后运行 build_registry.py 再重排）")
    else:
        ap.error(f"venue registry not found: {registry_path}; run build_registry.py or pass --allow-missing-registry")
    rules = json.load(open(os.path.join(SKILL_DIR, "references", "carrier_rules.json"), encoding="utf-8"))
    ctx = (build_name_index(registry), rules, registry["conferences"], registry["journals_ccf"], registry,
           ccf2026_ranks(registry["ccf2026_entries"]),
           {k.replace("-", "").lower(): v for k, v in registry["partitions_by_issn"].items()})
    policy = track_policy_from(a.plan, a.track_policy)
    if policy not in TRACK_POLICIES:
        ap.error(f"unknown track_policy {policy!r}")

    rows = [annotate(json.loads(line), ctx, policy) for line in open(a.candidates, encoding="utf-8") if line.strip()]
    pack = (json.load(open(a.plan, encoding="utf-8")).get("pack") if a.plan and os.path.exists(a.plan) else None) or {}
    if pack and "ccf" not in (pack.get("grading") or []):
        # Discipline packs without CCF: keep journal partitions, drop CS-only grades and track rules.
        for r in rows:
            r.update({"ccf_grade": None, "ccf_grade_sources": [], "ccf_grade_provisional": None,
                      "claimed_ccf_grade": None, "main_venue_grade": None})
    if pack and not pack.get("track_rules", True):
        for r in rows:
            r.update({"track_excluded": False, "excluded_reason": None})
    # Eligible records keep their Jev order and come first, so Top K never spends reading budget on excluded tracks.
    rows.sort(key=lambda r: (bool(r["track_excluded"]), r.get("jev_rank") or 10**9))
    stats = {"graded": 0, "provisional": 0, "preprint": 0, "claimed": 0, "unresolved": 0,
             "journal_partitioned": 0, "track_excluded": 0}
    tables_missing = not registry.get("snapshot")
    for position, r in enumerate(rows):
        r["rank_position"] = position
        r["grade_tables_missing"] = tables_missing
        stats["track_excluded"] += r["track_excluded"]
        stats["journal_partitioned"] += bool(r["partition"])
        if r["ccf_grade"]: stats["graded"] += 1
        elif r["ccf_grade_provisional"]: stats["provisional"] += 1
        elif r["record_type"] == "preprint":
            stats["preprint"] += 1; stats["claimed"] += bool(r["claimed_ccf_grade"])
        else: stats["unresolved"] += 1
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"标注完成 → {a.out}（轨道策略 {policy}）")
    print("  等级命中 {graded}｜等级待核实轨道 {provisional}｜预印本 {preprint}（其中声称已发表 {claimed}）｜"
          "未解析 {unresolved}｜期刊分区 {journal_partitioned}｜按轨道策略排除 {track_excluded}".format(**stats))


if __name__ == "__main__":
    main()
