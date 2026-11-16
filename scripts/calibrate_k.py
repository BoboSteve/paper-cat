#!/usr/bin/env python3
"""Help choose K (the full-text reading budget) from a small blind, rank-stratified sample.

    python scripts/calibrate_k.py sample  --ranked ranked.jsonl --k 50 --out-dir calibration/
    # the agent (or user) labels calibration_sheet.csv: must_read / useful / drop / pending
    python scripts/calibrate_k.py analyze --out-dir calibration/

The sheet hides Jev scores and ranks and is shuffled, so labels are not anchored by the model.
The analysis reports the useful-rate per rank band and suggests where reading stops paying off.
It is a cutoff aid, not a recall estimate.
"""
import argparse
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import read_jsonl, write_json  # noqa: E402

LABELS = {"must_read", "useful", "drop", "pending"}


def bands_for(n, k):
    edges = [(1, max(1, k // 4)), (k // 4 + 1, k), (k + 1, 2 * k), (2 * k + 1, n)]
    return [(name, lo, min(hi, n)) for name, (lo, hi) in zip(("top_quarter", "rest_of_k", "k_to_2k", "tail"), edges)
            if lo <= min(hi, n)]


def sample(rows, k, per_band=5, seed=20261115):
    eligible = [(position, row) for position, row in enumerate(rows) if not row.get("track_excluded")]
    rng = random.Random(seed)
    picked = []
    bands = bands_for(len(eligible), k)
    # Bands that are missing or smaller than their quota pass the shortfall on to later bands,
    # so the sample keeps about 4 * per_band records while recording the real strata.
    carry = per_band * (4 - len(bands))
    for name, lo, hi in bands:
        pool = eligible[lo - 1:hi]
        want = per_band + carry
        take = min(len(pool), want)
        carry = want - take
        for position, row in rng.sample(pool, take):
            picked.append({"band": name, "rank_position": position, "row": row})
    rng.shuffle(picked)
    return picked, bands, seed


def write_sample(picked, bands, seed, out_dir, k):
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "calibration_sheet.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["sample_id", "title", "year", "venue", "abstract", "label", "reason"])
        writer.writeheader()
        for number, item in enumerate(picked, 1):
            row = item["row"]
            writer.writerow({"sample_id": number, "title": row.get("title"), "year": row.get("year"),
                             "venue": row.get("venue") or row.get("claimed_venue") or "",
                             "abstract": row.get("abstract") or "(no abstract: label pending unless the title is decisive)",
                             "label": "", "reason": ""})
    write_json(out_dir / "calibration_key.json", {
        "seed": seed, "k": k, "bands": [{"name": n, "from": lo, "to": hi} for n, lo, hi in bands],
        "samples": [{"sample_id": number, "band": item["band"], "rank_position": item["rank_position"],
                     "record_id": item["row"].get("canonical_record_id") or item["row"].get("record_id")}
                    for number, item in enumerate(picked, 1)],
    })


def analyze(out_dir):
    key = json.loads((out_dir / "calibration_key.json").read_text(encoding="utf-8"))
    with (out_dir / "calibration_sheet.csv").open(encoding="utf-8-sig", newline="") as stream:
        labels = {int(row["sample_id"]): row["label"].strip() for row in csv.DictReader(stream)}
    bad = {sid: label for sid, label in labels.items() if label and label not in LABELS}
    if bad:
        raise SystemExit(f"unknown labels {bad}; use {sorted(LABELS)}")
    per_band = {}
    for item in key["samples"]:
        label = labels.get(item["sample_id"], "")
        counts = per_band.setdefault(item["band"], Counter())
        counts[label or "unlabelled"] += 1
    summary = []
    for band in key["bands"]:
        counts = per_band.get(band["name"], Counter())
        judged = counts["must_read"] + counts["useful"] + counts["drop"]
        rate = (counts["must_read"] + counts["useful"]) / judged if judged else None
        summary.append({**band, "counts": dict(counts), "useful_rate": None if rate is None else round(rate, 2)})
    k = key["k"]
    hint = "keep K"
    by_name = {row["name"]: row for row in summary}
    beyond = (by_name.get("k_to_2k") or {}).get("useful_rate")
    inside = (by_name.get("rest_of_k") or {}).get("useful_rate")
    if beyond is not None and beyond >= 0.4:
        hint = f"useful papers continue past K: consider K≈{2 * k}"
    elif inside is not None and inside < 0.2:
        hint = f"few useful papers in ranks {k // 4 + 1}-{k}: consider K≈{max(5, k // 2)}"
    result = {"bands": summary, "suggestion": hint}
    write_json(out_dir / "calibration_result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--ranked", required=True)
    s.add_argument("--k", type=int, required=True)
    s.add_argument("--per-band", type=int, default=5)
    s.add_argument("--seed", type=int, default=20261115)
    s.add_argument("--out-dir", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    if args.command == "sample":
        rows = read_jsonl(args.ranked)
        picked, bands, seed = sample(rows, args.k, args.per_band, args.seed)
        write_sample(picked, bands, seed, Path(args.out_dir), args.k)
        print(f"CALIBRATION_SAMPLE records={len(picked)} bands={[b[0] for b in bands]} -> {args.out_dir}")
    else:
        result = analyze(Path(args.out_dir))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
