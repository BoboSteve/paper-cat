# Candidate merge and deduplication

Read this reference when a task combines PSM results, official venue catalogs, user references, or citation expansion before Jev ranking.

## Scope

The Agent chooses which sources belong in the task and the input order. Put the most authoritative or metadata-complete source first when it should supply the canonical title, year, venue, and DOI.

`scripts/merge_candidates.py` is a deterministic backstop, not a required gate for every search. Use it when several normalized JSONL files can contain the same paper. A small or unusual task may instead be merged manually, provided the Agent records the reason and provenance.

The script expects normalized JSONL. The Agent remains responsible for extracting records from PDF, BibTeX, RIS, CSV, or other formats and for deciding whether near-duplicates belong to the same work.

## Invocation

```bash
python scripts/merge_candidates.py \
  --input venue=<TASK>/catalog.jsonl \
  --input psm=<TASK>/psm_candidates.jsonl \
  --input user_seed=<TASK>/seeds.jsonl \
  --out <TASK>/candidates.jsonl \
  --report <TASK>/merge_report.json
```

Each input line must contain at least `title`. Supported identity fields include DOI, arXiv ID, DBLP record key, ACL Anthology ID, OpenReview ID, and `externalIds`.

## Merge policy

Automatic merge evidence, in decreasing strength:

1. DOI;
2. versionless arXiv ID;
3. DBLP, ACL Anthology, or OpenReview publication ID;
4. exact normalized title when year, first author, and formal venue do not conflict.

Same-title records stay separate when their years, first authors, or formal venues conflict. The report lists these as `possible_duplicates` for Agent review.

`possible_duplicates` covers exact normalized-title matches. `near_duplicate_candidates` additionally reports same-first-author records with compatible venues and years, plus high title similarity or a shortened-title relationship. These are review prompts only. Preprint and formal versions often change the title, so assign a shared `merge_group` only after human or Agent review.

After review, the Agent can put the same `merge_group` value on records that should be treated as one work. This is the explicit escape hatch for unusual metadata or intentional version association.

## Outputs

- `candidates.jsonl`: one record per conservative work group, retaining merged query IDs, source labels, and per-record provenance;
- `merge_report.json`: input hashes, group membership, merge evidence, review flags, unmerged same-title groups, and fuzzy near-duplicate candidates.

`title_only_merge` means no shared identifier was found. Treat it as a warning, not proof that the merge is wrong.
