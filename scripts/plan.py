#!/usr/bin/env python3
"""Validate a topic brief and query matrix, then write a reproducible plan.json."""
import argparse
import datetime
import hashlib
import json
import re
from pathlib import Path
from startlux_runtime import settings as runtime_settings
from concepts import normalize_concepts
from common import load_pack


REQUIRED_BRIEF = ("topic", "topic_description", "time_window", "inclusion", "exclusion")
TIER_K = {"quick": 20, "standard": 50, "deep": 100}


def parse_window(value):
    """{'start','end'} from a dict or text such as '2025-01-01 to 2026-11-15' / '2024-2026'."""
    if isinstance(value, dict):
        return {"start": value.get("start"), "end": value.get("end")}
    dates = re.findall(r"(?:19|20)\d{2}-\d{2}-\d{2}", str(value))
    if len(dates) >= 2:
        return {"start": dates[0], "end": dates[1]}
    years = re.findall(r"(?:19|20)\d{2}", str(value))
    if len(years) >= 2:
        return {"start": f"{years[0]}-01-01", "end": f"{years[1]}-12-31"}
    if len(dates) == 1:
        return {"start": dates[0], "end": datetime.date.today().isoformat()}
    return None


def parse_sources(value):
    if isinstance(value, list):
        values = value
    else:
        values = str(value or "").split(",")
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--path", choices=("A", "B", "hybrid"), required=True)
    parser.add_argument("--tier", choices=("quick", "standard", "deep"), default="standard")
    parser.add_argument("--selected-k", type=int,
                        help="full-text reading budget K; default by tier (quick 20, standard 50, deep 100)")
    parser.add_argument("--jev-rate", type=float, default=3.0, help="observed papers per second")
    parser.add_argument("--track-policy", choices=("main_only", "include_findings", "all"),
                        help="conference tracks forwarded to reading; default main_only (or brief.track_policy)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    brief = json.loads(Path(args.brief).read_text(encoding="utf-8-sig"))
    missing = [key for key in REQUIRED_BRIEF if not brief.get(key)]
    if missing:
        parser.error("brief missing fields: " + ", ".join(missing))
    queries = [
        json.loads(line)
        for line in Path(args.queries).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    warnings = []
    if not queries:
        parser.error("queries file is empty")
    if args.path in {"B", "hybrid"} and len(queries) < 3 and args.tier != "quick":
        warnings.append("fewer than three queries: recall depends on wording; add synonyms for standard/deep runs")
    ids = [str(row.get("query_id") or "").strip() for row in queries]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        parser.error("query_id values must be present and unique")
    total_requested = 0
    sources = set()
    for row in queries:
        if not str(row.get("query") or "").strip():
            parser.error(f"{row.get('query_id')}: query is empty")
        total_requested += int(row.get("max_results") or 20)
        sources.update(parse_sources(row.get("sources") or brief.get("sources")))
    try:
        concepts = normalize_concepts(brief["concepts"], brief["topic"]) if brief.get("concepts") else []
    except ValueError as error:
        parser.error(f"concepts: {error}")
    try:
        pack = load_pack(brief.get("pack") or "general")
    except ValueError as error:
        parser.error(str(error))
    ranker = brief.get("ranker", "auto")
    if ranker not in {"auto", "jev", "lexical"}:
        parser.error("ranker must be auto, jev or lexical")
    prefilter_cap = int(brief.get("prefilter_cap") or 400)
    if not 50 <= prefilter_cap <= 5000:
        parser.error("prefilter_cap must be between 50 and 5000")
    if args.selected_k is None:
        args.selected_k = int(brief.get("selected_k") or TIER_K[args.tier])
    if args.selected_k < 1 or args.jev_rate <= 0:
        parser.error("selected-k and jev-rate must be positive")
    # Subtopics are optional: without them the whole topic is one subtopic.
    regressions = brief.get("regression_subtopics") or brief.get("subtopics") or [brief["topic"]]
    plan = {
        "version": 1,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "topic": brief["topic"],
        "topic_description": brief["topic_description"],
        "desired_paper_profile": brief.get("desired_paper_profile", ""),
        "pack": {key: pack.get(key) for key in ("id", "name", "sources", "grading", "track_rules")},
        "concepts": concepts,
        "known_papers": [str(x) for x in brief.get("known_papers") or []][:200],
        "exclusion_terms": [str(x) for x in brief.get("exclusion_terms") or []][:40],
        "ranker": ranker,
        "prefilter_cap": prefilter_cap,
        "alignment": {
            "positive_examples": brief.get("positive_examples") or [],
            "negative_examples": brief.get("negative_examples") or [],
            "paper_roles": brief.get("paper_roles") or [
                "direct_competitor", "method_donor", "failure_warning", "data_eval_support"
            ],
        },
        "research_questions": brief.get("research_questions") or [],
        "path": args.path,
        "track_policy": args.track_policy or brief.get("track_policy") or "main_only",
        "criteria_version": brief.get("criteria_version") or "v1",
        "warnings": warnings,
        "tier": args.tier,
        "run_date": datetime.date.today().isoformat(),
        "time_window": brief["time_window"],
        "time_window_range": parse_window(brief["time_window"]),
        "clarification": brief.get("clarification", "topic brief supplied by the user"),
        "inclusion": brief["inclusion"],
        "exclusion": brief["exclusion"],
        "transfer_rule": brief.get(
            "transfer_rule",
            "General methods are eligible when their components can be transferred to the target topic.",
        ),
        "catalog": {
            "source": brief.get("catalog_source") or "OpenReview/DBLP/official proceedings",
            "sources": parse_sources(brief.get("catalog_sources") or []),
        },
        "sources": sorted(sources),
        "queries": {
            "count": len(queries),
            "requested_results": total_requested,
            "sha256": hashlib.sha256(Path(args.queries).read_bytes()).hexdigest(),
        },
        "jev": {
            "model": runtime_settings().get("model_name", "StartLux-Decision-v1.1-4B-Instant-MLX-8bit"),
            "selected_k": args.selected_k,
            "workers": 1,
            "estimated_seconds_at_k": round(args.selected_k / args.jev_rate, 1),
        },
        "fulltext": {
            "required_for": ["core", "boundary", "conflicting_evidence"],
            "queries": brief.get("fulltext_queries") or [
                "method", "experiment", "result", "ablation", "limitation",
            ],
        },
        "regression": {
            "scope": "target_topic_only",
            "subtopics": regressions,
        },
        "stop_conditions": brief.get("stop_conditions") or [
            "all configured sources finish or their failures are recorded",
            "Top-K final judgments have evidence quotes",
            "full-text checks for core and boundary items are complete or marked pending",
            "known coverage gaps are listed in the final report",
        ],
        "known_gaps": brief.get("known_gaps") or [],
        "funnel": {
            "steps": [
                ["识别（召回）", 0],
                ["目录/来源去重", 0],
                ["判定（相关性）", 0],
                ["纳入核心", 0],
                ["纳入外围", 0],
                ["待核实", 0],
                ["排序未转发", 0],
                ["排除", 0],
            ]
        },
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for warning in warnings:
        print("PLAN_WARNING " + warning)
    print(
        f"PLAN_FINISHED path={args.path} queries={len(queries)} "
        f"requested={total_requested} K={args.selected_k} -> {output}"
    )


if __name__ == "__main__":
    main()
