# Alignment, checkpoints, and progress

Read this reference before search, before Jev, and before final full-text judgment.

## Desired-paper profile

The topic brief is not enough. Before search, align on what a useful paper looks like:

- central research question and innovation target;
- mechanisms the user wants to learn;
- papers that would be direct competitors;
- method donors that can transfer across domains;
- failure warnings and negative results that should change the design;
- data, benchmark, or evaluation papers that enable validation;
- positive examples and anti-examples supplied by the user;
- time window and foundational-paper exception;
- K and the realistic full-text budget.

Do not start a large search while these points are generic enough to match almost any paper.

## Paper roles

Every final full-text judgment records one or more roles:

- `direct_competitor`: addresses the same problem and must be compared directly;
- `method_donor`: supplies a mechanism that can be transferred;
- `failure_warning`: reveals a failure mode, negative result, or boundary;
- `data_eval_support`: supplies data, benchmarks, rubrics, or evaluation methods.

These roles are separate from `core`, `periphery`, `exclude`, and `pending`.

## Mandatory user alignments

### Before search

Show the topic brief, desired-paper profile, positive/negative examples, query matrix, sources,
time window, and provisional K. Wait for confirmation.

### Before Jev

Show the Jev prompt and explain that Jev only ranks candidates. Use a small stratified calibration
sample if the objective is new or the last run produced weak papers. Wait for confirmation if the
user wants control over relevance.

### Before final judgment

After Jev, show the proposed Top K with title, source, score, and one-line reason for inclusion.
Allow the user to remove items, restore lower-ranked items, or adjust K before paying the full-text
cost. A topic brief alone is not sufficient alignment.

### During full-text reading

After the first 5–10 papers, report the emerging method clusters, surprising findings, and any need
to revise the rubric. Get user feedback before processing the rest when feasible. Continue periodic
checkpoints every 5–10 papers.

## Full-text rule

Every record forwarded to the final Agent must be read in full text.

- Abstracts are for recall, Jev, metadata, and navigation only.
- Download and extract PDF text before final judgment.
- Record `read_scope=fulltext` and the local text path.
- If full text is unavailable, mark `pending_fulltext`; do not substitute an abstract judgment.
- If the abstract clearly shows very high relevance after repeated retrieval failures, set
  `missing_fulltext_priority=high`, explain why it matters, and generate a separate
  `missing_fulltext.md/.csv` queue for the user. The paper remains pending until a PDF is supplied.
- Continue reading other available papers while high-priority missing files wait for manual retrieval.
- Evidence quotes must come from full text and should include a page or section when available.
- Final decision dimensions include nearest work, mechanism, data/evidence path, co-evolution loop,
  failure modes, compute cost, transfer to the target domain, and minimum validation experiment.

## Progress dashboard

Initialize the dashboard before search:

```bash
python scripts/progress_dashboard.py --task <TASK> \
  --stage alignment --status waiting_user --completed 1 --total 1 \
  --message "Waiting for topic and selection-rule confirmation"
```

Update it while each stage runs:

```bash
python scripts/progress_dashboard.py --task <TASK> \
  --stage search --status running --completed 3 --total 12 \
  --message "Finished 3 of 12 queries"
```

The script writes `progress_state.json`, `stage_summaries.jsonl`, and an auto-refreshing
`dashboard.html`.

When waiting for user-supplied PDFs:

```bash
python scripts/progress_dashboard.py --task <TASK> --stage fulltext \
  --status waiting_user --completed <N> --total <K> \
  --message "Waiting for 3 high-priority PDFs" \
  --summary "Separate manual retrieval queue requires user action"
```

## Summary cadence

Do not wait for major-stage boundaries only.

| Stage | Default cadence |
|---|---|
| Search | every 3–5 queries |
| Merge/metadata | every 25% or when a quality issue appears |
| Jev | every 10–15% or roughly 10 minutes |
| Pre-final alignment | on every K or source-list change |
| Full text | every 5–10 papers |
| Final judgment | every 10 papers |
| Report/regression | after each completed report revision |

## Summary content

Each stage summary states:

```text
completed / total and percentage
throughput and ETA
new high-value papers
current method clusters
failures and pending items
whether selection rules are drifting
decisions needed from the user
```

If the result begins to look homogeneous, keyword-driven, or unrelated to the innovation goal,
pause and recalibrate instead of finishing the batch.
