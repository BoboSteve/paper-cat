#!/usr/bin/env python3
"""One entry point for the whole workflow, in five resumable stages with a checkpoint card after each.

    python scripts/run.py init     --task DIR --brief brief.json [--queries q.jsonl] [--seeds refs.bib ...] [--clues notes.json ...]
    python scripts/run.py search   --task DIR      # multi-source search, seeds, citations, merge, abstracts, date window
    python scripts/run.py rank     --task DIR      # Jev scoring (model started/stopped automatically), venues, grades, Top K
    python scripts/run.py fetch    --task DIR      # download Top K, extract page-marked full text, build the reading sheet
    python scripts/run.py finalize --task DIR --judged-by "Claude Code (model)"   # import judgments, report, checks
    python scripts/run.py status   --task DIR

Each stage writes checkpoints/NN_*.md: what happened, what needs the user's confirmation, and the
next command. The agent stops at every card; the user answers in chat. Stages are safe to re-run.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from common import cache_dir, load_env, read_json, read_jsonl, write_json, write_jsonl  # noqa: E402

STAGES = ("init", "search", "rank", "rerank", "fetch", "finalize")
TIER_LABEL = {"quick": "小（默认读 20 篇）", "standard": "中（默认读 50 篇）", "deep": "大（默认读 100 篇）"}


def _caps(query):
    """Per-source result caps as the search will really apply them."""
    caps = [f"{name} 最多 {query[key]} 条" for name, key in (("arXiv", "arxiv_max_results"), ("OpenAlex", "openalex_max_results"))
            if query.get(key)]
    return "，".join(caps + [f"其余来源各最多 {query.get('max_results')} 条"])
DEFAULT_SOURCES = ("openalex", "semantic", "arxiv", "dblp", "crossref")


class StageError(RuntimeError):
    pass


# --------------------------------------------------------------------------- plumbing

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def script(name, *args, task, allow_fail=False, quiet=False):
    command = [sys.executable, "-X", "utf8", "-B", str(SCRIPTS / name), *map(str, args)]
    env = {**os.environ, "PYTHONUTF8": "1"}
    result = subprocess.run(command, cwd=str(task), env=env, text=True, encoding="utf-8", errors="replace",
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    output = result.stdout.strip()
    if output and not quiet:
        print("\n".join("  " + line for line in output.splitlines()[-12:]))
    log = task / "logs" / "run.log"
    log.parent.mkdir(exist_ok=True)
    with log.open("a", encoding="utf-8") as stream:
        stream.write(f"\n### {now()} {name} {' '.join(map(str, args))}\n{output}\n(exit {result.returncode})\n")
    if result.returncode != 0 and not allow_fail:
        raise StageError(f"{name} failed (exit {result.returncode}); see {log}")
    return result.returncode, output


def state(task):
    return read_json(task / "run_state.json", {"stages": {}}) or {"stages": {}}


def mark(task, stage, **info):
    value = state(task)
    value["stages"][stage] = {"finished_at": now(), **info}
    write_json(task / "run_state.json", value)


def require(task, stage):
    if stage not in state(task)["stages"]:
        raise StageError(f"run `{stage}` first (python scripts/run.py {stage} --task {task})")


def card(task, number, name, lines):
    path = task / "checkpoints" / f"{number:02d}_{name}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"\nCHECKPOINT -> {path}")
    return path


def py_cmd(stage, task, extra=""):
    return f"python scripts/run.py {stage} --task \"{task}\"{extra}"


# --------------------------------------------------------------------------- init

def write_queries(task, brief, explicit, sources):
    target = task / "queries.jsonl"
    if explicit:
        shutil.copyfile(explicit, target)
        return target
    from plan import parse_window
    window = parse_window(brief.get("time_window", ""))
    queries = list(brief.get("queries") or [])
    if brief.get("concepts"):
        import concepts as concept_tools
        groups = concept_tools.normalize_concepts(brief["concepts"], brief.get("topic", ""))
        queries.insert(0, {"query_id": "concepts", "query": " ".join(group[0] for group in groups),
                           "arxiv_query": concept_tools.arxiv_query(groups), "arxiv_max_results": 300,
                           "openalex_query": concept_tools.openalex_query(groups), "openalex_max_results": 200})
    if not queries:
        raise StageError("no queries: add \"concepts\" (recommended) or a \"queries\" list to the brief")
    with target.open("w", encoding="utf-8", newline="\n") as stream:
        for number, item in enumerate(queries, 1):
            row = item if isinstance(item, dict) else {"query": str(item)}
            row = {"query_id": row.get("query_id") or f"q{number}", "sources": row.get("sources") or ",".join(sources),
                   "max_results": int(row.get("max_results") or brief.get("max_results_per_query") or 25),
                   **({"date_from": window["start"], "date_to": window["end"]} if window else {}), **row}
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return target


def stage_init(args, task):
    task.mkdir(parents=True, exist_ok=True)
    raw = Path(args.brief).read_bytes()
    try:
        brief = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:  # written by a shell on Chinese Windows; stored as UTF-8 from here on
        brief = json.loads(raw.decode("gbk"))
        print("  note: brief file was not UTF-8 (read as GBK); saved a UTF-8 copy in the task folder")
    write_json(task / "brief.json", brief)
    for kind, files in (("seeds", args.seeds), ("clues", args.clues)):
        for file in files or []:
            (task / kind).mkdir(exist_ok=True)
            shutil.copyfile(file, task / kind / Path(file).name)
    print("[1/3] preflight")
    script("preflight.py", "--require", "tools", "--allow-degraded", "--out", task / "preflight.json", task=task, allow_fail=True)
    preflight = read_json(task / "preflight.json", {}) or {}
    components = preflight.get("components") or {}
    from common import load_pack
    if args.pack:
        brief["pack"] = args.pack
        write_json(task / "brief.json", brief)
    try:
        pack = load_pack(brief.get("pack") or "general")
    except ValueError as error:
        raise StageError(str(error)) from None
    usable = [s for s in pack["sources"] if (components.get(s) or {}).get("status", "ok") != "blocked"
              and not (s == "semantic" and (components.get("semantic") or {}).get("configured") is False)]
    write_queries(task, brief, args.queries, usable)
    print("[2/3] plan")
    plan_args = ["--brief", task / "brief.json", "--queries", task / "queries.jsonl", "--path", args.path,
                 "--tier", args.tier, "--out", task / "plan.json"]
    if args.k:
        plan_args += ["--selected-k", args.k]
    if args.track_policy:
        plan_args += ["--track-policy", args.track_policy]
    script("plan.py", *plan_args, task=task)
    plan = read_json(task / "plan.json")
    print("[3/3] dashboard")
    script("progress_dashboard.py", "--task", task, "--stage", "alignment", "--status", "waiting_user",
           "--completed", 1, "--total", 1, "--message", "Waiting for plan confirmation", task=task, quiet=True)
    queries = read_jsonl(task / "queries.jsonl")
    source_lines = source_section(components, args.path)
    mark(task, "init", queries=len(queries))
    card(task, 1, "plan", [
        "# 检查点 1：检索计划（请确认）", "",
        f"**主题**：{plan['topic']}", "", f"**主题描述**：{plan['topic_description']}", "",
        f"**目标论文画像**：{plan.get('desired_paper_profile') or '（未填写，建议补充）'}", "",
        f"**时间窗**：{plan['time_window']}  → 解析为 {plan.get('time_window_range')}", "",
        "**纳入标准**：", *[f"- {item}" for item in _as_list(plan["inclusion"])], "",
        "**排除标准**：", *[f"- {item}" for item in _as_list(plan["exclusion"])], "",
        f"**路径**：{plan['path']}　**检索规模**：{TIER_LABEL.get(plan['tier'], plan['tier'])}　**全文阅读量 K**：{plan['jev']['selected_k']}　"
        f"**会议轨道**：{plan['track_policy']}（main_only 只收主会；include_findings 同时收 Findings）", "",
        "**检索式**（由概念组自动生成：组内同义词 OR、组间 AND）：", *[f"- `{q['query_id']}` {q['query']}（{q['sources']}；{_caps(q)}）" for q in queries], "",
        *source_lines,
        *(["**提醒**：", *[f"- {w}" for w in plan.get("warnings") or []], ""] if plan.get("warnings") else []),
        f"种子文献：{len(list((task / 'seeds').glob('*'))) if (task / 'seeds').exists() else 0} 个文件；"
        f"小红书线索：{len(list((task / 'clues').glob('*'))) if (task / 'clues').exists() else 0} 个文件", "",
        "---", "需要用户确认：主题与画像是否准确、检索式是否要增删、K 与轨道策略是否合适。",
        f"确认后运行：`{py_cmd('search', task)}`",
    ])


def source_section(components, path):
    """Checkpoint text: what works now, and optional sources the user could add (never required)."""
    from common import source_status
    rows = source_status()
    working, broken, missing = [], [], []
    for row in rows:
        check = components.get(row["id"]) or {}
        if row["configured"] and check.get("status") == "blocked":
            broken.append(row)  # configured but the service rejected it (expired key, wrong password ...)
        elif row["configured"]:
            working.append(row["name"])
        elif not (row["id"] == "openreview" and path == "B"):
            missing.append(row)  # OpenReview only matters for venue-complete (Path A) runs
    from common import runtime_settings
    runtime = runtime_settings()
    ranker = ("本地排序模型在 rank 阶段自动启动、用完关闭" if runtime.get("service") and runtime.get("model_name")
              else "未配置本地排序模型：rank 阶段按关键词覆盖度排序，再由 Agent 读摘要二次排序")
    always = ["arXiv"] + ([] if "OpenAlex" in working else ["OpenAlex（免 key）"]) + ["Crossref"]
    lines = ["**数据源**：" + "、".join(always + working) + f" 可用；{ranker}。", ""]
    for row in broken:
        lines.append(f"- ⚠ {row['name']} 已配置但连接失败（{(components.get(row['id']) or {}).get('http_status') or '见 preflight.json'}），"
                     f"本次会跳过它；可运行 `python scripts/{row['command']}` 重新录入并测试。")
    if missing:
        lines += ["", "**可选：再配置以下来源效果更好（不配置也能完整运行，不影响本次确认）**", "",
                  "| 来源 | 配置后的好处 | 怎么配置 |", "|---|---|---|"]
        for row in missing:
            how = f"`python scripts/{row['command']}`（{row['get']}）" if row["command"] else row["get"]
            lines.append(f"| {row['name']} | {row['benefit']} | {how} |")
        lines += ["", "密钥和密码请在你自己的终端里运行上面的命令输入（不回显），不要发到对话里。"]
    from doctor import load_state
    if "xiaohongshu" not in (load_state().get("dismissed") or []):
        running = any(r["id"] == "xiaohongshu" and r["configured"] for r in rows)
        lines += ["", "**要不要顺便看看小红书？** 发帖的人往往是对论文比较有把握才分享，评论里也常有复现反馈。"
                  "可以用来发现论文，并在最后给推荐论文附上“社区口碑”（只作参考，不作证据）。",
                  "- 选项：A 看（" + ("小红书 MCP 已在运行，直接检索" if running else
                                  "需要你先运行 `python scripts/xhs_setup.py --setup` 扫码登录，约 3 分钟；或者直接把帖子文字贴给 Agent") + "）"
                  "　B 这次不看　C 以后都不要再问（`python scripts/doctor.py --dismiss xiaohongshu`）"]
    return lines + [""]


def _as_list(value):
    return value if isinstance(value, list) else [value]


# --------------------------------------------------------------------------- search

def stage_search(args, task):
    require(task, "init")
    plan = read_json(task / "plan.json")
    print("[1/5] topic search")
    code, _ = script("psm_search.py", "--queries", task / "queries.jsonl", "--out-dir", task,
                     "--progress-task", task, task=task, allow_fail=True)
    inputs = []
    seeds = sorted((task / "seeds").glob("*")) if (task / "seeds").exists() else []
    clues = sorted((task / "clues").glob("*")) if (task / "clues").exists() else []
    if seeds:
        print("[2/5] user seeds + citation expansion")
        script("seed_import.py", *sum((["--input", p] for p in seeds), []), "--out", task / "seeds.jsonl", task=task)
        inputs.append(("user_seed", task / "seeds.jsonl"))
        if plan.get("citation_expand", True):
            c, _ = script("citation_expand.py", "--seeds", task / "seeds.jsonl", "--direction", "both",
                          "--max-per-seed", 20, "--out", task / "citation_candidates.jsonl", task=task, allow_fail=True)
            if c == 0 and (task / "citation_candidates.jsonl").exists():
                inputs.append(("citation", task / "citation_candidates.jsonl"))
    if clues:
        print("[2/5] Xiaohongshu clues -> paper seeds")
        script("clue_import.py", *sum((["--input", p] for p in clues), []), "--out-clues", task / "xhs_clues.jsonl",
               "--out-seeds", task / "xhs_seeds.jsonl", "--resolve", task=task)
        if (task / "xhs_seeds.jsonl").exists() and (task / "xhs_seeds.jsonl").stat().st_size:
            inputs.append(("xhs_clue", task / "xhs_seeds.jsonl"))
    if (task / "psm_candidates.jsonl").exists():
        inputs.insert(0, ("psm", task / "psm_candidates.jsonl"))
    empty = [label for label, path in inputs if not any(line.strip() for line in path.read_text(encoding="utf-8").splitlines())]
    for label in empty:
        print(f"  note: {label} produced no records (see logs/run.log); continuing without it")
    inputs = [(label, path) for label, path in inputs if label not in empty]
    if not inputs:
        raise StageError("no candidates from any source; see checkpoints and logs/run.log")
    print("[3/5] merge")
    script("merge_candidates.py", *sum((["--input", f"{label}={path}"] for label, path in inputs), []),
           "--out", task / "candidates.jsonl", "--report", task / "merge_report.json", task=task)
    print("[4/5] abstracts")
    script("metadata_repair.py", "--input", task / "candidates.jsonl", "--out", task / "candidates_metadata.jsonl",
           "--report", task / "metadata_report.json", "--only-fields", "abstract", task=task, allow_fail=True)
    source = task / ("candidates_metadata.jsonl" if (task / "candidates_metadata.jsonl").exists() else "candidates.jsonl")
    window = plan.get("time_window_range")
    print("[5/5] date window")
    if window and window.get("start") and window.get("end"):
        script("scope_filter.py", "--input", source, "--out", task / "candidates_scoped.jsonl", "--start", window["start"],
               "--end", window["end"], "--drop-out", "--report", task / "scope_report.json", task=task)
    else:
        shutil.copyfile(source, task / "candidates_scoped.jsonl")
        print("  time window not parseable: no date filtering (stated in the card)")
    rows = read_jsonl(task / "candidates_scoped.jsonl")
    psm_meta = read_json(task / "psm_meta.json", {}) or {}
    coverage = read_json(task / "psm_coverage.json", {}) or {}
    scope = read_json(task / "scope_report.json", {}) or {}
    merge = (read_json(task / "merge_report.json", {}) or {}).get("counts") or {}
    missing_abstract = sum(not str(r.get("abstract") or "").strip() for r in rows)
    from jev_rank import profile_from_plan, prompt_for, topic_from_plan
    prompt = prompt_for(topic_from_plan(str(task / "plan.json")), profile_from_plan(str(task / "plan.json")))
    boundary = [r for r in rows if r.get("scope_status") == "boundary"]
    from pool import recall_check
    recall = recall_check(plan.get("known_papers"), read_jsonl(task / "candidates.jsonl"), rows)
    recall_lines = []
    if recall:
        recall_lines = [f"- 召回自检：你给的 {recall['known']} 篇已知论文中找到 {len(recall['found'])} 篇",
                        *[f"  - 未找到：{m['paper']}" + ("（检索到了，但不在时间窗内）" if m["in_pools"][0] else "")
                          for m in recall["missing"][:10]]]
        write_json(task / "recall_check.json", recall)
    mark(task, "search", candidates=len(rows), search_exit=code)
    card(task, 2, "candidates", [
        "# 检查点 2：候选池与 Jev 排序提示（请确认）", "",
        f"- 检索：{len(psm_meta.get('queries') or [])} 组查询，各来源命中 {coverage.get('source_results', {})}",
        f"- 来源错误：{coverage.get('source_errors') or '无'}" + ("（partial 的查询下次运行会自动重试）" if coverage.get("source_errors") else ""),
        *([f"- 零命中（来源正常回应但没有结果，常见原因是概念组太多太窄）：{coverage['empty_sources']}"]
          if coverage.get("empty_sources") else []),
        f"- 合并：输入 {merge.get('input_rows', '?')} 条 → 候选 {merge.get('candidates', len(rows))} 条；疑似近重复 {merge.get('near_duplicates', 0)} 组（见 merge_report.json）",
        f"- 时间窗：{scope.get('counts', {})}；时间窗外已移除 {scope.get('dropped', {}).get('out_of_window', 0)} 条",
        f"- 边界记录（只知年/月、跨越时间窗边界，保留待你决定）：{len(boundary)} 条",
        *[f"  - {r.get('year')} {r['title'][:90]}" for r in boundary[:8]],
        f"- 缺摘要：{missing_abstract} 条（排序时只能看标题）", *recall_lines, "",
        "## Jev 排序提示（只用于排序，不做纳入/排除决定）", "", "```text", prompt, "```", "",
        "---", "需要用户确认：候选规模是否合理、提示是否表达了你想要的论文；如结果偏题，先改 brief/检索式再重跑 search。",
        f"确认后运行：`{py_cmd('rank', task)}`",
    ])


# --------------------------------------------------------------------------- rank

def stage_rank(args, task):
    require(task, "search")
    plan = read_json(task / "plan.json")
    k = int(plan["jev"]["selected_k"])
    import pool
    groups = pool.plan_concepts(plan)
    rows = read_jsonl(task / "candidates_scoped.jsonl")
    kept, dropped = pool.prefilter(rows, groups, int(plan.get("prefilter_cap") or 400))
    write_jsonl(task / "rank_input.jsonl", kept)
    if dropped:
        print(f"  prefilter: kept {len(kept)} best keyword matches, set aside {dropped} (listed in the card)")
    print("[1/4] relevance ranking")
    ranker = run_ranker(args, task, plan, kept, groups)
    print("[2/4] venue resolution")
    script("venue_resolve.py", "--input", task / "jev_scored.jsonl", "--out", task / "jev_venue.jsonl",
           "--report", task / "venue_report.json", task=task)
    print("[3/4] grades, partitions, track policy")
    registry_dir = cache_dir(args.cache_dir)
    if not (registry_dir / "venue_registry.json").exists():
        print("  first use: downloading the public grade tables (CCF lists, CAS partitions), about 1-3 minutes")
        code, _ = script("build_registry.py", "--cache-dir", registry_dir, task=task, allow_fail=True, quiet=True)
        if code != 0:
            print("  could not build the grade tables (offline or git missing): continuing without grades")
    script("rank.py", "--candidates", task / "jev_venue.jsonl", "--cache-dir", registry_dir,
           "--plan", task / "plan.json", "--out", task / "ranked.jsonl", "--allow-missing-registry", task=task)
    rows = read_jsonl(task / "ranked.jsonl")
    from concepts import exclusion_hints
    for row in rows:
        row["exclusion_hints"] = exclusion_hints(row, plan.get("exclusion_terms"))
    write_jsonl(task / "ranked.jsonl", rows)
    eligible = [(i, r) for i, r in enumerate(rows) if not r.get("track_excluded")]
    print("[4/4] calibration sample and rerank sheet")
    if len(eligible) > 2 * k:
        script("calibrate_k.py", "sample", "--ranked", task / "ranked.jsonl", "--k", k,
               "--out-dir", task / "calibration", task=task, quiet=True)
    write_rerank_sheet(task, rows, eligible[: 2 * k])
    mark(task, "rank", ranked=len(rows), k=k, ranker=ranker, prefilter_dropped=dropped)
    topk_card(task, plan, rows, k, ranker, dropped)


def run_ranker(args, task, plan, rows, groups):
    """Local model when available (or required); otherwise keyword ranking, stated in the card."""
    choice = plan.get("ranker", "auto")
    if choice != "lexical" and not args.no_service:
        configured = bool((read_json(SCRIPTS.parent / "runtime.local.json", {}) or {}).get("service"))
        if configured or choice == "jev":
            started = False
            try:
                code, _ = script("jev_service.py", "status", task=task, allow_fail=True, quiet=True)
                if code != 0:
                    code, _ = script("jev_service.py", "start", task=task, allow_fail=choice != "jev")
                    started = code == 0
                if code == 0:
                    script("jev_rank.py", "--candidates", task / "rank_input.jsonl", "--plan", task / "plan.json",
                           "--out", task / "jev_scored.jsonl", "--progress-task", task, task=task)
                    return "jev"
            finally:
                if started and not args.keep_service:
                    script("jev_service.py", "stop", task=task, allow_fail=True, quiet=True)
                    print("  model service stopped (GPU memory released)")
    elif args.no_service and choice != "lexical":
        script("jev_rank.py", "--candidates", task / "rank_input.jsonl", "--plan", task / "plan.json",
               "--out", task / "jev_scored.jsonl", "--progress-task", task, task=task)
        return "jev"
    import pool
    write_jsonl(task / "jev_scored.jsonl", pool.lexical_scores(rows, groups))
    print("  no local model: ranked by keyword coverage (the agent can rerank the head, see the card)")
    return "lexical"


def write_rerank_sheet(task, rows, head):
    lines = ["# 二次排序（可选）", "",
             "读下面每篇的标题和摘要，在 rerank.json 里给出优先级（1 必读 / 2 可读 / 3 可不读）和方法类别；",
             "然后运行 `python scripts/run.py rerank --task <TASK>`。同一优先级内按类别轮流排列，避免同类方法扎堆。", ""]
    for i, row in head:
        lines += [f"## {i} · {row['title']}", str(row.get("abstract") or "（无摘要）")[:900], ""]
    (task / "rerank_sheet.md").write_text("\n".join(lines), encoding="utf-8")


def topk_card(task, plan, rows, k, ranker, dropped):
    eligible = [(i, r) for i, r in enumerate(rows) if not r.get("track_excluded")]
    top, after = eligible[:k], eligible[k:k + 10]
    excluded = [r for r in rows if r.get("track_excluded")]
    scores = [r.get("jev_score") or 0 for r in rows if r.get("status") == "ok"]
    base, _, reranked = ranker.partition("+")
    ranker_line = ("本地模型打分（只用于排序）" if base == "jev" else "按关键词覆盖度排序（未使用本地模型）")
    ranker_line += "，并经 Agent 读摘要二次排序、按方法类别分散" if reranked else (
        "；建议让 Agent 读摘要做一次二次排序" if base != "jev" else "")
    header = "| 位置 | 分数 | 标题 | 年份 | 发表载体 | 等级/分区 | 提示 |"
    rule = "|---|---|---|---|---|---|---|"
    card(task, 3, "topk", [
        f"# 检查点 3：前 {len(top)} 篇全文阅读名单（请确认）", "",
        f"排序方式：{ranker_line}。共 {len(scores)} 篇；≥0.9 {sum(s >= .9 for s in scores)}，"
        f"0.5–0.9 {sum(.5 <= s < .9 for s in scores)}，<0.5 {sum(s < .5 for s in scores)}。",
        *([f"候选太多：先按关键词保留了最相关的部分，另有 {dropped} 篇未参与排序（rank_input.jsonl 之外）。"] if dropped else []),
        *(["**本次没有等级名录**（首次下载失败：离线或无法访问 GitHub），“等级/分区”一栏为空，不代表论文没有等级；"
           "联网后运行 `python scripts/build_registry.py`，再重新运行 rank 即可补上。"] if any(r.get("grade_tables_missing") for r in rows) else []), "",
        header, rule, *[_row_line(i, r) for i, r in top], "",
        f"## 紧随其后的 {len(after)} 篇（可补回）", "", header, rule, *[_row_line(i, r) for i, r in after], "",
        *([f"## 按轨道策略 {plan.get('track_policy')} 未进入名单的 {len(excluded)} 篇", "",
           *[f"- {r.get('venue')}: {r['title'][:90]}" for r in excluded[:15]], ""] if excluded else []),
        "## 你可以这样调整", "",
        "- 删掉不想读的、补回想读的、改 K：直接告诉 Agent（会写入 `topk_overrides.json`）；",
        "- 让 Agent 读摘要做二次排序并按方法类别分散（`rerank_sheet.md` → `rerank.json` → `run.py rerank`）；",
        "- “提示”一栏是摘要里出现了你的排除词，只提醒读全文时确认，不会自动排除。", "",
        f"确认后运行：`{py_cmd('fetch', task)}`",
    ])


def stage_rerank(args, task):
    require(task, "rank")
    if state(task)["stages"].get("fetch"):
        raise StageError("全文阶段已经开始：二次排序会改变位置编号，请在 fetch 之前进行")
    sheet_path = task / "rerank.json"
    try:
        sheet = json.loads(sheet_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise StageError(f"缺少 {sheet_path}：格式为 {{\"位置\": {{\"priority\": 1, \"cluster\": \"类别\"}}}}") from None
    except json.JSONDecodeError as error:
        raise StageError(f"rerank.json 不是合法 JSON：{error}") from None
    import pool
    plan = read_json(task / "plan.json")
    k = int(plan["jev"]["selected_k"])
    rows = read_jsonl(task / "ranked.jsonl")
    try:
        rows = pool.apply_rerank(rows, sheet, 2 * k)
    except ValueError as error:
        raise StageError(str(error)) from None
    write_jsonl(task / "ranked.jsonl", rows)
    (task / "topk_overrides.json").unlink(missing_ok=True)  # positions changed; old edits no longer apply
    mark(task, "rerank", reordered=min(2 * k, len(rows)))
    topk_card(task, plan, rows, k, state(task)["stages"]["rank"].get("ranker", "jev") + "+agent", 0)


def _row_line(index, row):
    grade = row.get("ccf_grade") and f"CCF {row['ccf_grade']}" or (
        row.get("ccf_grade_provisional") and f"CCF {row['ccf_grade_provisional']}（轨道待核实）") or (
        row.get("claimed_ccf_grade") and f"声称 CCF {row['claimed_ccf_grade']}（未核实）") or ""
    zone = ((row.get("partition") or {}).get("cas2025") or {}).get("major_zone")
    grade = "；".join(x for x in (grade, zone and f"中科院 {zone} 区") if x) or "—"
    venue = row.get("venue") or row.get("claimed_venue") or ("arXiv 预印本" if row.get("record_type") == "preprint" else "—")
    title = str(row.get("title") or "").replace("|", "/")[:80]
    hints = "⚠ 摘要提到 " + "、".join(row["exclusion_hints"]) if row.get("exclusion_hints") else ""
    return (f"| {index} | {row.get('jev_score') or 0:.2f} | {title} | {row.get('year') or ''} | {str(venue)[:40]} "
            f"| {grade} | {hints} |")


def selected_positions(task, rows, k):
    overrides = read_json(task / "topk_overrides.json", {}) or {}
    k = int(overrides.get("k") or k)
    eligible = [i for i, r in enumerate(rows) if not r.get("track_excluded")]
    chosen = [i for i in eligible[:k] if i not in set(overrides.get("remove") or [])]
    chosen += [int(i) for i in overrides.get("add") or [] if 0 <= int(i) < len(rows) and int(i) not in chosen]
    chosen += [i for i, r in enumerate(rows) if r.get("status") == "flagged" and i not in chosen]
    return chosen


# --------------------------------------------------------------------------- fetch

def stage_fetch(args, task):
    require(task, "rank")
    plan = read_json(task / "plan.json")
    rows = read_jsonl(task / "ranked.jsonl")
    chosen = selected_positions(task, rows, plan["jev"]["selected_k"])
    idx = ",".join(map(str, chosen))
    print(f"[1/3] download {len(chosen)} PDFs")
    script("download.py", "--ranked", task / "ranked.jsonl", "--idx", idx, "--out-dir", task / "pdfs",
           "--skip-existing", task=task)
    print("[2/3] full text (page-marked)")
    script("fulltext_verify.py", "--task", task, "--progress-task", task, task=task, allow_fail=True)
    print("[3/3] reading sheet and batches")
    script("judgment_sheet.py", "--ranked", task / "ranked.jsonl", "--plan", task / "plan.json", "--idx", idx,
           "--out-csv", task / "judgment_sheet.csv", "--out-md", task / "judgment_sheet.md", task=task)
    script("reading.py", "plan", "--task", task, task=task)
    reading_plan = read_json(task / "reading" / "plan.json", {}) or {}
    manifest = {r["idx"]: r for r in (read_json(task / "fulltext_manifest.json", {}) or {}).get("records", [])}
    downloads = {r["idx"]: r for r in read_json(task / "pdfs" / "manifest.json", [])}
    ready = [i for i in chosen if (manifest.get(i) or {}).get("status") == "ok"]
    missing = [i for i in chosen if i not in ready]
    mark(task, "fetch", selected=len(chosen), readable=len(ready))
    batches = reading_plan.get("batches") or []
    pages = sum(b["pages"] for b in batches)
    card(task, 4, "reading", [
        f"# 检查点 4：全文通读（{len(ready)}/{len(chosen)} 篇可读，共约 {pages} 页）", "",
        f"已分成 {len(batches)} 批（`reading/assignment_B*.md`），阅读说明在 `reading/INSTRUCTIONS.md`，"
        "协议见 `references/reading-protocol.md`。",
        "- 每批读完写 `reading/batch_<批次>.json`；证据句必须从原文逐字复制，会自动核对并定位页码；",
        "- 支持子 Agent 的工具可以每批交给一个子 Agent 并行读；不支持就逐批顺序读；",
        "- 中断（额度用完、断网）不会丢进度：`python scripts/reading.py status --task <TASK>` 列出还没完成的批次，只重读那些；",
        "- 先读完第一批，向用户汇报方法聚类和意外发现，再继续。", "",
        "## 批次", "",
        *[f"- {b['id']}：{len(b['idx'])} 篇，约 {b['pages']} 页" for b in batches], "",
        *(["## 缺全文（保持待核实）", "",
           *[f"- 位置 {i}：{rows[i]['title'][:90]} — {(downloads.get(i) or {}).get('note') or (manifest.get(i) or {}).get('status', '未下载')}"
             for i in missing], "",
           "给用户两个选项：",
           f"- A 自己下载 PDF，保存为 `{task / 'pdfs' / 'manual'}/<位置>.pdf`（如 `{missing[0]}.pdf`），再运行一次 fetch；",
           "- B 用名单后面的论文替补：Agent 写 `topk_overrides.json`（如 "
           f"`{{\"remove\": [{missing[0]}], \"add\": [<检查点 3 的位置>]}}`）再运行一次 fetch；",
           "- 都不选也可以：缺全文的论文保持“待核实”，不会进入核心结论。", ""] if missing else []),
        "---", "全部批次完成后运行（会自动合并、核对引句、生成报告）：",
        "`" + py_cmd("finalize", task, ' --judged-by "<harness> (<model>)"') + "`",
    ])


# --------------------------------------------------------------------------- finalize

def stage_finalize(args, task):
    require(task, "fetch")
    judged_by = args.judged_by or os.environ.get("PAPERCAT_JUDGE") or os.environ.get("CS_PAPER_SEARCH_JUDGE")
    if not judged_by:
        raise StageError("--judged-by is required: name the harness and model that read the papers")
    if (task / "reading" / "plan.json").exists():
        code, output = script("reading.py", "status", "--task", task, task=task, allow_fail=True, quiet=True)
        if "ALL_DONE" not in output:
            raise StageError("还有阅读批次没有完成：\n" + output + "\n完成后再运行 finalize（不会重复读已完成的批次）")
        script("reading.py", "merge", "--task", task, task=task)
    print("[1/4] manual retrieval queue")
    script("missing_fulltext_report.py", "--sheet", task / "judgment_sheet.csv", "--out-md", task / "missing_fulltext.md",
           "--out-csv", task / "missing_fulltext.csv", task=task, allow_fail=True, quiet=True)
    print("[2/4] import judgments (quotes checked against full text)")
    script("judgment_import.py", "--input", task / "judgment_sheet.csv", "--ranked", task / "ranked.jsonl",
           "--plan", task / "plan.json", "--judged-by", judged_by, "--quote-check", args.quote_check,
           "--out", task / "judgments.jsonl", "--progress-task", task, task=task)
    print("[3/4] report")
    script("report.py", "--task", task, "--cache-dir", cache_dir(args.cache_dir), "--out", task / "report.html", task=task)
    print("[4/4] checks")
    script("regression_check.py", "--task", task, task=task, allow_fail=True)
    report = read_json(task / "regression_report.json", {}) or {}
    judgments = [row for row in read_jsonl(task / "judgments.jsonl") if "idx" in row]
    rows = read_jsonl(task / "ranked.jsonl")
    counts = Counter(row["decision"] for row in judgments)
    core = [row for row in judgments if row["decision"] == "core"]
    mark(task, "finalize", decisions=dict(counts), regression=report.get("status"))
    card(task, 5, "summary", [
        "# 检查点 5：结果", "",
        f"- 判断：{dict(counts)}；引句全部逐字核对：{all(r.get('evidence_verified') for r in judgments if r['decision'] != 'pending')}",
        f"- 完整性检查：{report.get('status')}（" + "，".join(f"{c['name']}={c['status']}" for c in report.get("checks", [])
                                                if c["status"] != "pass") + "）",
        f"- 报告：`{task / 'report.html'}`", "",
        "## 核心论文", "",
        *[f"- **{rows[r['idx']]['title']}**（{rows[r['idx']].get('venue') or rows[r['idx']].get('claimed_venue') or '预印本'}）"
          f" — {', '.join(r['paper_roles'])}\n  > {r['evidence_quote']}（第 {r.get('evidence_page')} 页）" for r in core],
        "" if core else "（没有论文满足核心标准：这是一个有效结论，不用凑数）",
    ])


def stage_status(args, task):
    value = state(task)
    for stage in STAGES:
        info = value["stages"].get(stage)
        print(f"{stage:<9} {'done ' + info['finished_at'] if info else '-'}")
    progress = read_json(task / "progress_state.json", {}) or {}
    if progress.get("status") and progress.get("status") != "done":
        print(f"running: {progress.get('stage')} {progress.get('completed', 0)}/{progress.get('total', '?')} "
              f"({progress.get('percent', 0)}%) {progress.get('message') or ''}".rstrip())
    cards = sorted((task / "checkpoints").glob("*.md")) if (task / "checkpoints").exists() else []
    if cards:
        print(f"latest checkpoint: {cards[-1]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="stage", required=True)
    for name in (*STAGES, "status"):
        p = sub.add_parser(name)
        p.add_argument("--task", required=True, help="task folder (created by init)")
        p.add_argument("--cache-dir")
        if name == "init":
            p.add_argument("--brief", required=True)
            p.add_argument("--queries")
            p.add_argument("--seeds", nargs="*", help="user references: PDF list, BibTeX, RIS, CSV/JSON, DOI/arXiv list")
            p.add_argument("--clues", nargs="*", help="Xiaohongshu notes saved as JSON/JSONL/text")
            p.add_argument("--path", choices=("A", "B", "hybrid"), default="B")
            p.add_argument("--tier", choices=("quick", "standard", "deep"), default="quick")
            p.add_argument("--k", type=int)
            p.add_argument("--track-policy", choices=("main_only", "include_findings", "all"))
            p.add_argument("--pack", help="discipline pack: general (default) or cs; see packs/")
        if name == "rank":
            p.add_argument("--no-service", action="store_true", help="do not start/stop the model; use a running one")
            p.add_argument("--keep-service", action="store_true", help="leave the model running afterwards")
        if name == "finalize":
            p.add_argument("--judged-by")
            p.add_argument("--quote-check", choices=("strict", "warn"), default="strict")
    args = parser.parse_args()
    task = Path(args.task).expanduser().resolve()
    load_env()  # fail early on unreadable env files; values are never printed
    try:
        {"init": stage_init, "search": stage_search, "rank": stage_rank, "rerank": stage_rerank, "fetch": stage_fetch,
         "finalize": stage_finalize, "status": stage_status}[args.stage](args, task)
    except StageError as error:
        print(f"\nSTOPPED: {error}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
