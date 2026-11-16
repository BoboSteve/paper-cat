# User-provided references

Read this reference when the user uploads papers, a bibliography, DOI/arXiv links, or asks to search around known references.

## Treat references as a seed set

Accept the formats the current Codex session can read, including PDF, BibTeX, RIS, CSV/JSON exports, and DOI or arXiv lists. Preserve uploaded files and record each item as `source=user_seed`.

Extract only fields supported by the source: title, authors, year, venue, DOI, arXiv ID, abstract, and file path. Do not fabricate missing metadata.

User-provided references are seeds, not automatic final inclusions:

- use them to clarify the topic when the user is still exploring;
- use titles, concepts, and method names to expand the query plan;
- follow their references and citations when useful;
- pass them through the same Jev ranking and final Agent judgment;
- keep out-of-range or boundary seeds visible instead of silently deleting them.

## Clarify how to use them

If the user supplied only a few papers, infer the common problem and method direction, then confirm briefly which parts are representative.

If the user supplied a large bibliography or library export, do not assume every record is in scope. Show a small set of title/venue/method clusters and ask whether the whole set or selected clusters should drive the search.

If the user is unsure, treat the references as an exploratory probe: identify 3-5 directions, give representative examples, and let the user choose or request a broader search.

## Merge and provenance

Normalize user seeds with `scripts/seed_import.py`; it accepts CSV, JSON/JSONL, BibTeX, RIS, and DOI/arXiv text lists. Then merge them with retrieved candidates using [candidate-merge.md](candidate-merge.md). Put the seed file first only when the user wants seed metadata to supply the canonical record; otherwise let the most authoritative source go first. Preserve:

- the uploaded file or source reference;
- `source=user_seed`;
- whether the metadata was read directly or resolved from an external index;
- the seed role: `topic_example`, `method_seed`, `baseline`, `negative_control`, or `unknown`.

Report counts separately for user seeds, expanded candidates, deduplicated additions, forwarded candidates, and final inclusions. A user-provided paper outside the requested time window remains in a separate seed section.

For one-hop citation expansion, run `scripts/citation_expand.py`. It resolves seeds through OpenAlex and follows references/citations; new records keep `source=citation_expansion`, the seed ID, relationship, and query provenance. A resolved seed with no available relationships is a coverage gap, not evidence that no related papers exist.
