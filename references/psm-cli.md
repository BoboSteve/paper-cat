# paper-search-mcp CLI adapter

Read this reference when using the shared `paper-search-mcp` retrieval layer.

## Invocation

The skill pins this upstream commit:

```text
cab1b5cc276a0cd004a0044003926a6440088049
```

For each query, call `search_papers` once in the psm CLI:

```bash
python scripts/psm_search.py --queries <TASK>/queries.jsonl --out-dir <TASK>
```

`psm_search.py` uses `uvx --from <pinned package> paper-search search ...`, writes the raw
CLI response, and leaves the normalized candidates in
`<TASK>/psm_candidates.jsonl`.

If the task also has user seeds, official catalogs, or citation expansion, use
[candidate-merge.md](candidate-merge.md) before Jev ranking. PSM output alone can still be merged,
because repeated papers may appear across queries.

The first run downloads and builds the pinned package into the uv cache; later runs can reuse
the cache, but a fresh machine still needs network access for setup.

## Query plan

Each input line has this shape:

```json
{"query_id":"q1","query":"vision-language-action data efficiency","sources":"openalex,semantic,crossref,arxiv,dblp,openreview","max_results":20}
```

The source order is intentional: psm deduplicates a multi-source response and keeps the first
record it sees. Put the source with the most complete abstract/DOI metadata first. The default
order is:

```text
openalex,semantic,crossref,arxiv,dblp,openreview
```

This is a lossy merge by design. DBLP keys, ISSN, venue tracks and official status may still need
the CS-specific enrichment scripts.

## arXiv is queried directly

When a query lists `arxiv`, `psm_search.py` removes it from the paper-search-mcp call and queries the
arXiv API itself (`arxiv_search`). paper-search-mcp sends its arXiv request together with the other
sources and gives up after a few quick retries, which produced persistent HTTP 429 in practice. The
built-in client follows the arXiv API terms (at most one request every 3 seconds and one connection,
shared by every script and process on the machine through a lock file in the cache directory), honours
`Retry-After`, and backs off 15/30/60/120 s. Its records carry the exact submission date, the arXiv
comment and journal-ref (used later as an unverified venue claim). Use `--arxiv-via-psm` to restore
the old behaviour.

A query whose result is `partial` because of a rate limit or timeout is not cached: the next run of
the stage retries it.

The search layer drops venue names and ISSNs; `venue_resolve.py` restores them after ranking.

## Outputs

- `psm_candidates.jsonl`: normalized Paper records, with query and source metadata;
- `psm_calls.jsonl`: one line per CLI invocation, including source errors;
- `psm_meta.json`: pinned version, source order, query summaries and counts;
- `raw/psm/*.json`: raw CLI responses;
- `raw/psm/*.stderr.log`: CLI diagnostics;
- `summaries/psm/*.json`: resumable per-query state.

A failed or partial source is not converted into a successful empty result.

## Path routing

- Path B: psm is the primary broad discovery layer. Add selected official venue enumeration and
  user/citation seeds when needed.
- Path A: official catalog/DBLP/OpenReview defines the venue universe. psm is optional for missing
  metadata or PDF fallback; do not force it when the official source already provides the field.

The psm OpenReview connector is anonymous broad discovery. It does not authenticate, accept
credentials, or establish main-conference membership. Do not count its results as a main-conference
catalog; use `openreview_enumerate.py --api-version auto` with an exact main venue group when Path A
requires OpenReview. If its summary reports `failed` or `empty`, use DBLP or official proceedings
with an explicit provenance note.

The existing `recall.py` is a legacy fallback for environments without psm and must not be used
silently instead of this adapter.
