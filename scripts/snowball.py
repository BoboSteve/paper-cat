#!/usr/bin/env python3
"""Backward snowballing from the papers already read: what do the best papers cite that we missed?

    python scripts/snowball.py find --task DIR          # writes snowball_candidates.jsonl + checkpoints/06_snowball.md
    python scripts/snowball.py add  --task DIR --pick 1,3,4

`find` reads the reference sections of the full texts judged core/periphery, extracts arXiv IDs and
DOIs, resolves them (arXiv / OpenAlex), drops what the candidate pool already has or what falls
outside the time window, and ranks the rest by how many read papers cite them. This does not depend
on citation databases, which lag weeks behind new preprints.

`add` appends the chosen papers to the END of ranked.jsonl (existing positions, downloads and
judgments stay valid) and to topk_overrides.json; then run `run.py fetch` again.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clue_import import from_openalex, openalex_work, resolve_arxiv  # noqa: E402
from common import load_env, normalize_doi, read_json, read_jsonl, split_pages, window_status, write_json, write_jsonl  # noqa: E402
from pool import _keys  # noqa: E402

REF_HEADING = re.compile(r"^\s*(?:\d+\s*\.?\s*)?(references|bibliography|参考文献)\s*$", re.I | re.M)
ARXIV = re.compile(r"(?:arxiv[:\s/]*|abs/)(\d{4}\.\d{4,5})", re.I)
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;)]+)")


def reference_text(pages):
    """Text after the last 'References' heading (the bibliography), or '' if none is found."""
    text = "\n".join(pages)
    matches = list(REF_HEADING.finditer(text))
    return text[matches[-1].end():] if matches else ""


def _date_label(record):
    """'2026-08-24', or '2026（只知年份，可能在范围外）' for year/month-only dates straddling the window."""
    value = str(record.get("published_date") or "")[:10]
    precision = record.get("date_precision")
    shown = {"year": value[:4], "month": value[:7]}.get(precision, value)
    if record.get("window_status") == "boundary":
        shown += "（只知" + {"year": "年份", "month": "月份"}.get(precision, "大致日期") + "，可能在范围外）"
    elif record.get("window_status") == "unknown":
        shown = (shown or "—") + "（日期不明）"
    return shown or "—"


def cmd_find(args, task):
    plan = read_json(task / "plan.json", {}) or {}
    wanted = set(args.decisions.split(","))
    judgments = [j for j in read_jsonl(task / "judgments.jsonl") if "idx" in j and j.get("decision") in wanted]
    if not judgments:
        raise SystemExit("no judged papers with decisions " + args.decisions + ": finish reading first")
    ranked = read_jsonl(task / "ranked.jsonl")
    known = set().union(*(_keys(r) for r in read_jsonl(task / "candidates.jsonl"))) | set().union(*(_keys(r) for r in ranked))
    cited_by = defaultdict(set)
    for judgment in judgments:
        text = reference_text(split_pages(Path(judgment["fulltext_path"]).read_text(encoding="utf-8", errors="replace")))
        for match in ARXIV.finditer(text):
            cited_by["arxiv:" + match.group(1)].add(judgment["idx"])
        for match in DOI.finditer(text):
            doi = normalize_doi(match.group(1).rstrip("."))
            if doi and not doi.startswith("10.48550/"):
                cited_by["doi:" + doi].add(judgment["idx"])
    fresh = {key: idxs for key, idxs in cited_by.items() if key not in known}
    try:
        arxiv_meta = resolve_arxiv([k[6:] for k in fresh if k.startswith("arxiv:")])
    except Exception as error:  # rate limit or offline: continue with DOI references, say what is missing
        arxiv_meta = {}
        print(f"  note: arXiv lookup unavailable ({type(error).__name__}); arXiv references skipped this time, "
              "run snowball.py find again later to include them")
    settings, records = load_env(), []
    dois = sorted((k for k in fresh if k.startswith("doi:")), key=lambda k: -len(fresh[k]))[: args.max_doi_lookups]
    for key in [k for k in fresh if k.startswith("arxiv:")] + dois:
        meta = arxiv_meta.get(key[6:]) if key.startswith("arxiv:") else None
        if key.startswith("doi:"):
            try:
                works = openalex_work(settings, doi=key[4:])
                meta = from_openalex(works[0]) if works and works[0].get("title") else None
            except Exception:
                meta = None
        if not meta:
            continue
        record = {**meta, "source": "snowball", "sources": ["snowball"], "record_id": key,
                  "cited_by": sorted(fresh[key]), "cited_by_titles": [ranked[i]["title"][:80] for i in sorted(fresh[key])]}
        if set(_keys(record)) & known:
            continue
        window = plan.get("time_window_range") or {}
        if window.get("start"):
            status, _, precision, _ = window_status(record, datetime.date.fromisoformat(window["start"]),
                                                    datetime.date.fromisoformat(window["end"]))
            if status == "out_of_window":
                continue
            record["window_status"], record["date_precision"] = status, precision
        records.append(record)
    from pool import plan_concepts
    import concepts as concept_tools
    scorer = concept_tools.Scorer(plan_concepts(plan))
    for record in records:
        record.update(scorer.score(record))
    # Cited by more read papers first; among equals, closer to the topic first.
    records.sort(key=lambda r: (-len(r["cited_by"]), -r["coverage"], -r["lexical"]))
    records = records[: args.max]
    write_jsonl(task / "snowball_candidates.jsonl", records)
    lines = ["# 检查点 6：滚雪球补检（可选）", "",
             f"从 {len(judgments)} 篇已读论文的参考文献中，找到 {len(records)} 篇候选池里没有、且不在时间范围外的论文：", "",
             "| 编号 | 被几篇引用 | 标题 | 日期 |", "|---|---|---|---|"]
    lines += [f"| {n} | {len(r['cited_by'])} | {r['title'][:90].replace('|', '/')} | {_date_label(r)} |"
              for n, r in enumerate(records, 1)]
    lines += ["", "想补读哪几篇？告诉 Agent 编号，它会运行 `snowball.py add --pick 编号` 再重新运行 fetch。"
              "（只在参考文献里出现，不代表一定相关；仍会经过全文判断。）"]
    (task / "checkpoints").mkdir(exist_ok=True)
    (task / "checkpoints" / "06_snowball.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"SNOWBALL references_seen={len(cited_by)} new={len(fresh)} candidates={len(records)} "
          f"-> {task / 'checkpoints' / '06_snowball.md'}")


def cmd_add(args, task):
    candidates = read_jsonl(task / "snowball_candidates.jsonl")
    try:
        picks = sorted({int(x) for x in args.pick.split(",") if x.strip()})
    except ValueError:
        raise SystemExit("--pick takes card numbers such as 1,3,4") from None
    if not picks or picks[0] < 1 or picks[-1] > len(candidates):
        raise SystemExit(f"--pick numbers must be between 1 and {len(candidates)}")
    chosen = [candidates[n - 1] for n in picks]
    ranked = read_jsonl(task / "ranked.jsonl")
    existing = set().union(*(_keys(r) for r in ranked))
    chosen = [c for c in chosen if not set(_keys(c)) & existing]
    if not chosen:
        raise SystemExit("all picked papers are already in ranked.jsonl")
    work = task / "snowball"
    work.mkdir(exist_ok=True)
    write_jsonl(work / "picked.jsonl", chosen)
    here = Path(__file__).resolve().parent
    for command in (["venue_resolve.py", "--input", work / "picked.jsonl", "--out", work / "picked_venue.jsonl"],
                    ["rank.py", "--candidates", work / "picked_venue.jsonl", "--plan", task / "plan.json",
                     "--out", work / "picked_ranked.jsonl"]):
        subprocess.run([sys.executable, "-X", "utf8", str(here / command[0]), *map(str, command[1:])],
                       check=True, stdout=subprocess.DEVNULL)
    added = read_jsonl(work / "picked_ranked.jsonl")
    start = len(ranked)
    for offset, row in enumerate(added):
        row.update({"rank_position": start + offset, "status": "snowball", "track_excluded": False})
    write_jsonl(task / "ranked.jsonl", ranked + added)
    overrides = read_json(task / "topk_overrides.json", {}) or {}
    overrides["add"] = sorted(set(overrides.get("add") or []) | set(range(start, start + len(added))))
    write_json(task / "topk_overrides.json", overrides)
    print(f"SNOWBALL_ADDED {len(added)} papers at positions {start}..{start + len(added) - 1}; "
          "now run `run.py fetch` (existing downloads and judgments are kept)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("find")
    f.add_argument("--task", required=True)
    f.add_argument("--decisions", default="core,periphery")
    f.add_argument("--max", type=int, default=30)
    f.add_argument("--max-doi-lookups", type=int, default=40)
    a = sub.add_parser("add")
    a.add_argument("--task", required=True)
    a.add_argument("--pick", required=True)
    args = parser.parse_args()
    task = Path(args.task).expanduser().resolve()
    # Each step needs the previous one; say which, instead of a traceback.
    needed = {"find": ("judgments.jsonl", "run.py finalize"), "add": ("snowball_candidates.jsonl", "snowball.py find")}
    name, step = needed[args.command]
    if not (task / name).exists():
        raise SystemExit(f"STOPPED: 还没有 {name}：先运行 `python scripts/{step} --task \"{task}\"`")
    {"find": cmd_find, "add": cmd_add}[args.command](args, task)


if __name__ == "__main__":
    main()
