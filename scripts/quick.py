#!/usr/bin/env python3
"""Two-minute literature snapshot: zero configuration, abstract level, and honest about both.

    python scripts/quick.py search --task DIR --topic "主题" --concepts '[["harness","scaffold"],["self-evolving","evolution"]]'
                                   [--since 2026-05-15] [--until 2026-11-15]
    # the agent reads DIR/quick/shortlist.md and writes DIR/quick/picks.json (format: see SKILL.md)
    python scripts/quick.py render --task DIR

Sources: arXiv and OpenAlex, both without accounts. No local model: candidates are pre-ranked by an
explainable keyword-coverage score and the agent reads the shortlisted abstracts. Picks may only
refer to shortlisted papers, so nothing can be added from memory.
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import concepts as concept_tools  # noqa: E402
import report_html  # noqa: E402
import sources  # noqa: E402
from common import arxiv_id_of, load_env, normalize_doi, read_json, window_status, write_json, write_jsonl  # noqa: E402

DEFAULT_DAYS = 183
MAX_PICKS = 10
LIMITS = {"summary": 160, "why": 160, "cluster": 16, "highlight": 120}


class UserError(Exception):
    """A problem the user or agent can fix; printed without a traceback."""


# --------------------------------------------------------------------------- search

def parse_window(since, until):
    today = datetime.date.today()
    try:
        end = datetime.date.fromisoformat(until) if until else today
        start = datetime.date.fromisoformat(since) if since else end - datetime.timedelta(days=DEFAULT_DAYS)
    except ValueError as error:
        raise UserError(f"日期格式应为 YYYY-MM-DD：{error}") from None
    if start > end:
        raise UserError("开始日期晚于结束日期")
    return start, min(end, today)


def load_concepts(value, topic):
    if value and value.startswith("@"):
        value = Path(value[1:]).read_text(encoding="utf-8-sig")
    try:
        parsed = json.loads(value) if value and value.strip().startswith("[") else value
    except json.JSONDecodeError as error:
        raise UserError(f"--concepts 不是合法 JSON：{error}") from None
    try:
        return concept_tools.normalize_concepts(parsed, topic)
    except ValueError as error:
        raise UserError(str(error)) from None


def _identity(record):
    arxiv = arxiv_id_of(record) or record.get("arxiv_id")
    if arxiv:
        return "arxiv:" + arxiv
    doi = normalize_doi(record.get("doi"))
    if doi:
        return "doi:" + doi
    return "title:" + re.sub(r"[^a-z0-9]+", "", str(record.get("title") or "").lower())[:120]


def dedupe(records):
    """Merge duplicates across sources; arXiv dates (day precision) and the longest abstract win."""
    merged, by_title = {}, {}
    for record in records:
        key = _identity(record)
        title_key = re.sub(r"[^a-z0-9]+", "", str(record.get("title") or "").lower())
        key = key if key in merged else by_title.get(title_key, key)  # same work, different identifiers
        if key not in merged:
            merged[key] = {**record, "sources": [record["source"]]}
            if len(title_key) >= 20:
                by_title.setdefault(title_key, key)
            continue
        kept = merged[key]
        kept["sources"] = sorted(set(kept["sources"]) | {record["source"]})
        if len(record.get("abstract") or "") > len(kept.get("abstract") or ""):
            kept["abstract"] = record["abstract"]
        for field in ("venue", "venue_source_type", "venue_issn", "venue_evidence", "pdf_url", "published_doi"):
            if record.get(field) and not kept.get(field):
                kept[field] = record[field]
        if record["source"] == "arxiv":
            kept["published_date"], kept["date_precision"] = record["published_date"], "day"
    return list(merged.values())


def narrowest_group(concepts, records):
    """Index of the group to relax: never the first (the core topic, by convention); among the others,
    the one least present in the titles/abstracts of the given records, the last-listed on ties."""
    scorer = concept_tools.Scorer([[term] for group in concepts for term in group])
    hits = [0] * len(concepts)
    for record in records:
        matched = set(scorer.score(record)["matched_terms"])
        for index, group in enumerate(concepts):
            hits[index] += bool(matched & set(group))
    return min(range(1, len(concepts)), key=lambda i: (hits[i], -i))


def run_sources(concepts, start, end, per_source, settings):
    """Query arXiv and OpenAlex. A source is ok (records found), empty (answered, nothing found) or failed.

    arXiv requires every concept group in the title or abstract, so with 3+ groups it can return nothing
    while OpenAlex (which also searches full text) finds plenty. In that case the narrowest group (least
    present in the OpenAlex results) is dropped once for arXiv only, and the relaxation is reported.
    """
    found, report = [], {}

    def attempt(name, job):
        try:
            records = job()
            report[name] = {"status": "ok" if records else "empty", "records": len(records)}
            return records
        except Exception as error:  # one source failing must not end the run
            report[name] = {"status": "failed", "error": f"{type(error).__name__}: {str(error)[:120]}"}
            return []

    window = (start.isoformat(), end.isoformat())
    found += attempt("openalex", lambda: sources.openalex_search(
        concept_tools.openalex_query(concepts), *window, per_source, settings))
    arxiv = attempt("arxiv", lambda: sources.arxiv_search(concept_tools.arxiv_query(concepts), *window, per_source))
    if not arxiv and report["arxiv"]["status"] == "empty" and len(concepts) >= 3:
        dropped = narrowest_group(concepts, found) if found else len(concepts) - 1
        relaxed = [group for i, group in enumerate(concepts) if i != dropped]
        arxiv = attempt("arxiv", lambda: sources.arxiv_search(concept_tools.arxiv_query(relaxed), *window, per_source))
        report["arxiv"]["relaxed"] = {"dropped_group": concepts[dropped], "query": concept_tools.arxiv_query(relaxed)}
    found += arxiv
    return found, report


def source_line(name, value):
    if value["status"] == "failed":
        return f"{name}：失败（{value['error']}）"
    text = f"{name}：{value['records']} 篇" if value["status"] == "ok" else f"{name}：零命中"
    relaxed = value.get("relaxed")
    if relaxed:
        text += f"（全部概念同时出现时为 0，已去掉概念组「{' / '.join(relaxed['dropped_group'])}」后重试）"
    return text


def shortlist_markdown(topic, shortlist):
    lines = [f"# 初筛名单：{topic}", "",
             "读每篇的标题和摘要，选出最多 10 篇写进 picks.json；只能用下面的 id，不能添加名单外的论文。", ""]
    for item in shortlist:
        lines += [f"## {item['id']} · {item['title']}",
                  f"- {item['date']} · {item['venue'] or '预印本'} · 匹配：{', '.join(item['matched_terms']) or '—'}",
                  "", item["abstract"] or "（无摘要：只看标题，谨慎推荐）", ""]
    return "\n".join(lines)


def cmd_search(args):
    task = Path(args.task).expanduser().resolve()
    out = task / "quick"
    topic = args.topic.strip()
    if not topic:
        raise UserError("请给出主题（--topic）")
    if not 10 <= args.per_source <= 500 or not 5 <= args.shortlist <= 80:
        raise UserError("--per-source 应在 10–500，--shortlist 应在 5–80")
    concepts = load_concepts(args.concepts, topic)
    start, end = parse_window(args.since, args.until)
    raw, source_report = run_sources(concepts, start, end, args.per_source, load_env())
    if not raw:
        failed = [source_line(k, v) for k, v in source_report.items()]
        raise UserError("两个数据源都没有返回结果。" + ("；".join(failed) if failed else "") +
                        " 建议：检查网络，或减少概念组/换更常见的说法后重试。")
    records = []
    for record in dedupe(raw):
        status = window_status(record, start, end)[0]
        if status in {"in_window", "boundary", "unknown"}:
            record["scope_status"] = status
            records.append(record)
    ranked = concept_tools.rank(records, concepts)
    pool = [r for r in ranked if r["coverage"] > 0]
    shortlist = []
    for number, record in enumerate(pool[: args.shortlist], 1):
        authors = [a.strip() for a in re.split(r";", str(record.get("authors") or "")) if a.strip()]
        shortlist.append({
            "id": f"p{number:02d}", "title": record["title"], "date": record.get("published_date") or "",
            "authors": authors[:3], "venue": record.get("venue") or "", "url": record.get("url"),
            "pdf": record.get("pdf_url"), "abstract": (record.get("abstract") or "")[:1800],
            "coverage": record["coverage"], "matched_terms": record["matched_terms"],
            "sources": record["sources"], "identity": _identity(record),
        })
    full = sum(r["coverage"] >= 1 for r in ranked)
    warnings, suggestions = [], []
    if full == 0:
        warnings.append("没有一篇论文的标题/摘要同时包含全部概念：初筛名单只是部分匹配，结果可能偏题")
        suggestions += ["去掉最窄的一个概念组", "给概念组补充同义词", "把时间范围放宽到 12 个月"]
    elif full < 10:
        warnings.append(f"同时命中全部概念的论文只有 {full} 篇")
        suggestions += ["把时间范围放宽到 12 个月", "去掉最窄的一个概念组", "给概念组补充同义词"]
    for name, value in source_report.items():
        if value["status"] != "ok" or value.get("relaxed"):
            warnings.append(source_line(name, value))
    plan = {"topic": topic, "concepts": concepts, "since": start.isoformat(), "until": end.isoformat(),
            "queries": {"arxiv": concept_tools.arxiv_query(concepts), "openalex": concept_tools.openalex_query(concepts)},
            "created_at": datetime.datetime.now().isoformat(timespec="seconds")}
    report = {"sources": source_report, "raw": len(raw), "unique_in_window": len(records),
              "full_coverage": full, "shortlist": len(shortlist), "warnings": warnings, "suggestions": suggestions}
    write_json(out / "plan.json", plan)
    write_jsonl(out / "candidates.jsonl", ranked)
    write_json(out / "shortlist.json", shortlist)
    (out / "shortlist.md").write_text(shortlist_markdown(topic, shortlist), encoding="utf-8")
    write_json(out / "search_report.json", report)
    print(f"QUICK_SEARCH raw={len(raw)} unique_in_window={len(records)} full_coverage={full} shortlist={len(shortlist)}")
    for name, value in source_report.items():
        print("  " + source_line(name, value))
    for warning in warnings:
        print("  WARNING " + warning)
    if suggestions:
        print("  OPTIONS " + " / ".join(suggestions + ["就用现有结果"]))
    print(f"NEXT 读 {out / 'shortlist.md'}，写 {out / 'picks.json'}，再运行 render")


# --------------------------------------------------------------------------- render

def _text(value, field, limit, errors, where, required=True):
    if not isinstance(value, str) or not value.strip():
        if required:
            errors.append(f"{where}: {field} 不能为空")
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        errors.append(f"{where}: {field} 超过 {limit} 字（现在 {len(value)} 字），请精简")
    return value


def validate_picks(payload, shortlist):
    """Return (clean, errors). Picks may only reference shortlist ids; lengths are bounded."""
    errors, known = [], {item["id"]: item for item in shortlist}
    if not isinstance(payload, dict):
        return None, ["picks.json 应是一个对象：{\"highlights\": [...], \"picks\": [...], \"also\": [...]}"]
    picks = payload.get("picks")
    if not isinstance(picks, list) or not 1 <= len(picks) <= MAX_PICKS:
        errors.append(f"picks 应包含 1–{MAX_PICKS} 篇")
        picks = picks if isinstance(picks, list) else []
    seen, clean_picks = set(), []
    for number, pick in enumerate(picks[:MAX_PICKS], 1):
        where = f"第 {number} 篇推荐（id {pick.get('id') if isinstance(pick, dict) else '?'}）"
        if not isinstance(pick, dict):
            errors.append(f"{where}: 应是对象")
            continue
        pid = pick.get("id")
        if pid not in known:
            errors.append(f"{where}: id {pid!r} 不在初筛名单中（不能添加名单外的论文）")
            continue
        if pid in seen:
            errors.append(f"{where}: id {pid} 重复")
            continue
        seen.add(pid)
        clean_picks.append({"id": pid,
                            "summary": _text(pick.get("summary"), "summary", LIMITS["summary"], errors, where),
                            "why": _text(pick.get("why"), "why", LIMITS["why"], errors, where),
                            "cluster": _text(pick.get("cluster"), "cluster", LIMITS["cluster"], errors, where)})
    if len({p["cluster"] for p in clean_picks}) > 6:
        errors.append("cluster 太多（最多 6 类），请合并相近的类别")
    also = []
    for pid in payload.get("also") or []:
        if pid in known and pid not in seen and pid not in also:
            also.append(pid)
        elif pid not in known:
            errors.append(f"also: id {pid!r} 不在初筛名单中")
    highlights = payload.get("highlights") or []
    if not isinstance(highlights, list) or len(highlights) > 5:
        errors.append("highlights 应是最多 5 条的列表")
        highlights = []
    highlights = [_text(h, "highlight", LIMITS["highlight"], errors, f"第 {i} 条要点")
                  for i, h in enumerate(highlights, 1)]
    return {"picks": clean_picks, "also": also[:10], "highlights": [h for h in highlights if h]}, errors


def _meta(item):
    authors = item["authors"][:2]
    who = "、".join(authors) + (" 等" if len(item["authors"]) > 2 else "")
    return " · ".join(x for x in (who, item["date"], item["venue"] or "arXiv 预印本") if x)


def build_page(plan, report, shortlist, clean, ladder):
    known = {item["id"]: item for item in shortlist}
    papers = [{"title": known[p["id"]]["title"], "url": known[p["id"]]["url"], "pdf": known[p["id"]]["pdf"],
               "meta": _meta(known[p["id"]]), "badges": [], "roles": [], "cluster": p["cluster"],
               "summary": p["summary"], "why": p["why"], "evidence": None, "community": None}
              for p in clean["picks"]]
    secondary = [{"title": known[pid]["title"], "url": known[pid]["url"], "meta": _meta(known[pid])}
                 for pid in clean["also"]]
    upgrade = [{"title": "深度检索（全文核实）", "benefit": "下载并通读全文，结论附原文证据和页码，标注会议/期刊等级与分区",
                "how": "对 Agent 说：对这个主题做深度检索"}] + [
        {"title": t["title"], "benefit": t["benefit"], "how": t["how"]} for t in ladder[:3]]
    picked = {p["id"] for p in clean["picks"]}
    rows = [[item["id"], item["title"], item["date"], ", ".join(item["matched_terms"]),
             "推荐" if item["id"] in picked else ("也值得一看" if item["id"] in clean["also"] else "")]
            for item in shortlist]
    source_lines = [source_line(name, v) for name, v in report["sources"].items()]
    return {
        "mode": "quick", "title": plan["topic"],
        "subtitle": f"{plan['since']} 至 {plan['until']} · 生成于 {datetime.date.today().isoformat()}",
        "funnel": [("篇检索结果", report["raw"]), ("篇在时间范围内", report["unique_in_window"]),
                   ("篇进入初筛", report["shortlist"]), ("篇推荐", len(papers))],
        "notice": (f"这是基于标题和摘要的速览：AI 读了 {report['shortlist']} 篇摘要后挑出 {len(papers)} 篇，"
                   "没有读全文，也没有核对会议或期刊等级。需要可引用的结论，请用深度检索。"),
        "highlights": clean["highlights"], "papers": papers, "secondary": secondary, "upgrade": upgrade,
        "details": [
            {"title": "检索式与数据源", "items": [f"时间范围：{plan['since']} 至 {plan['until']}",
                                             f"arXiv：{plan['queries']['arxiv']}",
                                             f"OpenAlex：{plan['queries']['openalex']}"] + source_lines},
            {"title": f"初筛名单（{len(shortlist)} 篇，按关键词覆盖度排序）", "searchable": True,
             "table": {"headers": ["编号", "标题", "日期", "命中关键词", "结果"], "rows": rows}},
        ],
        "footer": "论文喵 PaperCat · 数据：arXiv、OpenAlex · 速览不等于结论，引用前请阅读原文",
    }


def cmd_render(args):
    task = Path(args.task).expanduser().resolve()
    out = task / "quick"
    shortlist = read_json(out / "shortlist.json")
    plan, report = read_json(out / "plan.json"), read_json(out / "search_report.json")
    if not shortlist or not plan:
        raise UserError("还没有初筛名单：先运行 quick.py search")
    picks_path = Path(args.picks) if args.picks else out / "picks.json"
    try:
        payload = json.loads(picks_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise UserError(f"找不到 {picks_path}：请先根据 shortlist.md 写 picks.json") from None
    except json.JSONDecodeError as error:
        raise UserError(f"picks.json 不是合法 JSON：{error}") from None
    clean, errors = validate_picks(payload, shortlist)
    if errors:
        raise UserError("picks.json 需要修改：\n  " + "\n  ".join(errors))
    from doctor import ladder
    page = build_page(plan, report, shortlist, clean, ladder())
    target = Path(args.out) if args.out else out / "report.html"
    report_html.write(page, target)
    print(f"QUICK_REPORT picks={len(clean['picks'])} also={len(clean['also'])} -> {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("search", help="search arXiv + OpenAlex and build the shortlist")
    s.add_argument("--task", required=True)
    s.add_argument("--topic", required=True)
    s.add_argument("--concepts", help='JSON list of synonym groups, or @file; defaults to the topic words')
    s.add_argument("--since", help=f"YYYY-MM-DD; default {DEFAULT_DAYS} days before --until")
    s.add_argument("--until", help="YYYY-MM-DD; default today")
    s.add_argument("--per-source", type=int, default=150)
    s.add_argument("--shortlist", type=int, default=40)
    r = sub.add_parser("render", help="validate the agent's picks and write the report")
    r.add_argument("--task", required=True)
    r.add_argument("--picks")
    r.add_argument("--out")
    args = parser.parse_args()
    try:
        {"search": cmd_search, "render": cmd_render}[args.command](args)
    except UserError as error:
        print(f"STOPPED: {error}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
