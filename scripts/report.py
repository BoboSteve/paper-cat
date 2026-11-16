#!/usr/bin/env python3
"""Deep-mode report: aggregate the task into results.json, then render the clean HTML view.

results.json is the single source of truth (inputs are hashed); report.html is a view of it.
Optional task/highlights.json, written by the agent after reading, adds take-aways and per-paper
one-line summaries and clusters:

    {"highlights": ["..."], "papers": {"<rank position>": {"summary": "...", "cluster": "..."}}}
"""
import argparse
import datetime
import hashlib
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_html  # noqa: E402
from common import cache_dir, read_json, read_jsonl  # noqa: E402

DECISION_LABELS = {"core": "推荐", "periphery": "也值得一看", "exclude": "排除", "pending": "待核实",
                   "merged": "合并", "not_forwarded": "未进入精读"}


def tail_upper(population, sampled, relevant):
    """One-sided 95% exact hypergeometric upper bound (simple random sampling without replacement)."""
    if not 0 <= relevant <= sampled <= population:
        raise ValueError("Expected 0 <= relevant <= sampled <= population")
    if sampled == 0:
        return population
    denominator = math.comb(population, sampled)
    low, high = relevant, population - sampled + relevant
    while low < high:
        total = (low + high + 1) // 2
        ways = sum(math.comb(total, k) * math.comb(population - total, sampled - k)
                   for k in range(max(0, sampled - population + total), min(relevant, total) + 1))
        if ways / denominator >= .05:
            low = total
        else:
            high = total - 1
    return low


def tail_estimate(stratum):
    n, population, x = stratum["sampled"], stratum["population"], stratum["relevant"]
    if not n:
        return f"未抽样；总体可能相关 0–{population} 条"
    upper = tail_upper(population, n, x)
    return f"总体相关点估计 {round(population * x / n)} 条；单侧95%上界 {upper} 条；未抽样部分上界 {upper - x} 条"


def align_judgments(rows, jlines, selected_k=None, removed=()):
    """Map every ranked row to a decision; rows outside Top K or excluded by policy are not_forwarded."""
    meta = {"criteria_version": "未提供", "criteria": "未提供", "judged_by": "未提供"}
    meta.update(next((j["meta"] for j in jlines if "meta" in j), {}))
    jmap = {}
    for j in jlines:
        if "idx" not in j:
            continue
        idx = j["idx"]
        if type(idx) is not int or not 0 <= idx < len(rows) or idx in jmap:
            raise ValueError(f"Invalid or duplicate judgment idx: {idx}")
        if j.get("decision") not in {"core", "periphery", "exclude", "pending", "merged"}:
            raise ValueError(f"Unknown decision: {j.get('decision')}")
        jmap[idx] = {"relevance": "unknown", "reason": "", "verdict_source": meta["judged_by"],
                     "evidence_basis": "未提供", **j, "criteria_version": meta["criteria_version"]}
    cutoff = len(rows) if selected_k is None else max(0, min(int(selected_k), len(rows)))
    for i, row in enumerate(rows):
        if i in jmap:
            continue
        if row.get("track_excluded"):
            reason, source = row.get("excluded_reason") or "按会议轨道策略未进入精读", "track policy"
        elif i in removed:
            reason, source = "用户在确认 Top K 时移除", "user"
        elif i < cutoff:
            jmap[i] = {"idx": i, "decision": "pending", "relevance": "unknown", "reason": "尚无有效判断",
                       "verdict_source": "未判断", "criteria_version": meta["criteria_version"]}
            continue
        else:
            reason, source = f"排序位于前 {cutoff} 名之外", "rank cutoff"
        jmap[i] = {"idx": i, "decision": "not_forwarded", "relevance": "unknown", "reason": reason,
                   "verdict_source": source, "criteria_version": meta["criteria_version"]}
    return meta, jmap


# --------------------------------------------------------------------------- page model

def _badges(row):
    badges = []
    if row.get("ccf_grade"):
        badges.append((f"CCF {row['ccf_grade']}", "grade"))
    elif row.get("ccf_grade_provisional"):
        badges.append((f"CCF {row['ccf_grade_provisional']}?（轨道待核实）", "warn"))
    elif row.get("claimed_ccf_grade"):
        badges.append((f"自称 {row.get('claimed_venue')}（未核实）", "warn"))
    zone = ((row.get("partition") or {}).get("cas2025") or {}).get("major_zone")
    if zone:
        badges.append((f"中科院 {zone} 区", "grade"))
    if row.get("track") not in (None, "main", "unknown", "journal"):
        badges.append((f"{row['track']} 轨道", "warn"))
    return badges


def _meta(row):
    authors = row.get("authors") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in authors.split(";") if a.strip()]
    who = "、".join(authors[:2]) + (" 等" if len(authors) > 2 else "")
    venue = row.get("venue") or ("arXiv 预印本" if row.get("record_type") == "preprint" else "")
    date = str(row.get("published_date") or row.get("year") or "")[:10]
    return " · ".join(x for x in (who, date, str(venue)) if x)


def _url(row):
    doi = str(row.get("doi") or "")
    if row.get("url"):
        return row["url"]
    return f"https://doi.org/{doi}" if doi.startswith("10.") else None


def build_page(task, plan, rows, jmap, manifest, highlights, regression, ladder_items):
    pdf = {item["idx"]: item.get("file") for item in manifest if item.get("status") == "ok"}
    extra = (highlights or {}).get("papers") or {}

    def card(i):
        row, j = rows[i], jmap[i]
        note = extra.get(str(i)) or {}
        roles = j.get("paper_roles") or []
        return {"title": row["title"], "url": _url(row), "pdf": pdf.get(i), "meta": _meta(row),
                "badges": _badges(row), "roles": roles,
                "cluster": note.get("cluster") or j.get("cluster") or report_html.ROLE_LABELS.get(roles[0] if roles else "", ""),
                "summary": note.get("summary") or j.get("one_line") or j.get("reason"),
                "why": j.get("reason") if (note.get("summary") or j.get("one_line")) else None,
                "evidence": {"quote": j.get("evidence_quote"), "page": j.get("evidence_page"),
                             "verified": j.get("evidence_verified")} if j.get("evidence_quote") else None,
                "community": row.get("community")}

    order = sorted(jmap, key=lambda i: (rows[i].get("rank_position", i)))
    core = [card(i) for i in order if jmap[i]["decision"] == "core"]
    periphery = [card(i) for i in order if jmap[i]["decision"] == "periphery"]
    decisions = Counter(j["decision"] for j in jmap.values())
    judged = sum(decisions[d] for d in ("core", "periphery", "exclude", "pending", "merged"))
    psm = read_json(task / "psm_meta.json", {}) or {}
    raw = sum(len(q.get("candidates") or []) for q in psm.get("queries") or []) or len(rows)
    window = plan.get("time_window_range") or {}
    queries = [json.loads(line).get("query") for line in (task / "queries.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()] if (task / "queries.jsonl").exists() else []
    gaps = list(plan.get("known_gaps") or [])
    checks = [f"{c['name']}：{c['status']}" for c in (regression or {}).get("checks") or []]
    all_rows = [[rows[i].get("rank_position", i) + 1, rows[i]["title"], rows[i].get("year") or "",
                 rows[i].get("venue") or "", DECISION_LABELS.get(jmap[i]["decision"], jmap[i]["decision"]),
                 jmap[i].get("reason") or ""] for i in order]
    return {
        "mode": "deep", "title": plan.get("topic", "论文检索"),
        "subtitle": f"{window.get('start', '')} 至 {window.get('end', '')} · 生成于 {datetime.date.today().isoformat()}",
        "funnel": [("篇检索结果", raw), ("篇候选", len(rows)), ("篇通读全文", judged),
                   ("篇推荐", decisions["core"]), ("篇也值得一看", decisions["periphery"])],
        "notice": "每篇推荐都由 AI 通读了全文；证据句已在原文中逐字核对并标注页码。等级与分区来自公开名录快照，正式引用前请再核对。",
        "highlights": (highlights or {}).get("highlights") or [],
        "papers": core, "secondary": periphery,
        "upgrade": [{"title": t["title"], "benefit": t["benefit"], "how": t["how"]} for t in ladder_items[:3]],
        "details": [
            {"title": "检索式与数据源", "items": [f"时间范围：{window.get('start')} 至 {window.get('end')}"] +
             [f"检索式：{q}" for q in queries] + [f"判断依据：{plan.get('inclusion')}；排除：{plan.get('exclusion')}"]},
            {"title": f"全部候选与决策（{len(rows)} 篇，回答“为什么没有 X”）", "searchable": True,
             "table": {"headers": ["排名", "标题", "年份", "发表", "结果", "原因"], "rows": all_rows}},
            {"title": "完整性检查与已知缺口", "items": checks + gaps or ["无"]},
        ],
        "footer": f"论文喵 PaperCat · 判断方：{(jmap and next(iter(jmap.values())).get('verdict_source')) or ''} · "
                  "完整数据见同目录 results.json",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task", required=True)
    parser.add_argument("--cache-dir")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    task, out = Path(args.task).resolve(), Path(args.out).resolve()
    rows = read_jsonl(task / "ranked.jsonl")
    plan = read_json(task / "plan.json", {}) or {}
    selected_k = int((plan.get("jev") or {}).get("selected_k") or len(rows))
    jlines = read_jsonl(task / "judgments.jsonl")
    overrides = read_json(task / "topk_overrides.json", {}) or {}
    meta, jmap = align_judgments(rows, jlines, selected_k, {int(i) for i in overrides.get("remove") or []})
    manifest = read_json(task / "pdfs" / "manifest.json", []) or []
    for item in manifest:
        if item.get("file"):
            path = Path(item["file"])
            path = path if path.is_absolute() else task / path
            item["file"] = os.path.relpath(path, out.parent).replace(os.sep, "/")
    registry = read_json(cache_dir(args.cache_dir) / "venue_registry.json", {}) or {}
    hashes = {name: hashlib.sha256((task / name).read_bytes()).hexdigest()
              for name in ("ranked.jsonl", "judgments.jsonl", "plan.json") if (task / name).exists()}
    results = {
        "plan": plan, "rows": rows, "judgments": [jmap.get(i) for i in range(len(rows))], "judgment_meta": meta,
        "manifest": manifest, "decisions": dict(Counter(j["decision"] for j in jmap.values())),
        "preflight": read_json(task / "preflight.json", {}), "psm_coverage": read_json(task / "psm_coverage.json", {}),
        "scope_report": read_json(task / "scope_report.json", {}), "fulltext": read_json(task / "fulltext_manifest.json", {}),
        "registry_snapshot": registry.get("snapshot"), "input_sha256": hashes,
        "tail_sampling": [dict(s, estimate=tail_estimate(s)) for s in (plan.get("tail_sampling") or {}).get("strata", [])],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.parent / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    from doctor import ladder
    page = build_page(task, plan, rows, jmap, manifest, read_json(task / "highlights.json", {}),
                      read_json(task / "regression_report.json", {}), [t for t in ladder() if t["id"] != "local_model"])
    report_html.write(page, out)
    decisions = results["decisions"]
    print(f"OK → {out}（{out.stat().st_size // 1024} KB，推荐 {decisions.get('core', 0)} / "
          f"也值得一看 {decisions.get('periphery', 0)} / 待核实 {decisions.get('pending', 0)}）")


if __name__ == "__main__":
    main()
