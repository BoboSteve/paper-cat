# OpenReview main-conference enumeration (v1/v2)

Read this reference when Path A may use OpenReview, or when OpenReview coverage must be compared
with DBLP and official proceedings.

## Scope

Only main-conference accepted papers are included. The venue group must exactly match the main
conference, for example `ICLR.cc/2026/Conference`.

Oral, Spotlight, and Poster are presentation formats of the same main-conference paper. Findings, Workshops, Demo, Industry, Short, Student Abstract, Doctoral Consortium, rejected, withdrawn, and desk-rejected records are excluded from this enumeration.

OpenReview has two separate backends:

- v2: `https://api2.openreview.net`, current venues;
- v1: `https://api.openreview.net`, legacy venues.

Do not infer the API from the year. Use `--api-version auto` unless the venue is known to require
an explicit version.

## Installation

```bash
python -m pip install -r requirements-openreview.txt
```

## Authentication

Set these values in `~/.config/paper-search-mcp/.env` or the file passed with `--env-file`:

```text
OPENREVIEW_USERNAME=...
OPENREVIEW_PASSWORD=...
```

For an account using an authenticator app in unattended runs, also provide:

```text
OPENREVIEW_TOTP_SECRET=...
```

Do not put credentials or session tokens in task logs.

## Invocation

```bash
python scripts/openreview_enumerate.py \
  --venue-id ICLR.cc/2026/Conference \
  --api-version auto \
  --out <TASK>/openreview_catalog.jsonl \
  --summary <TASK>/openreview_summary.json
```

Use `--limit 2` only for a smoke test. Omit it for the full accepted main-conference catalog.

`auto` probes the venue submission invitation on v2 and then v1. Add `--api-version v1` or
`--api-version v2` only to bypass probing. For v2, the script queries accepted submissions with
`content.venueid` equal to the main venue group. For v1, it reads main-conference submissions with
`directReplies` and uses the exact `venueid` or the latest `Decision` reply to identify acceptance.

## Fallback

Inspect `openreview_summary.json`. If `status` is `failed` or `empty`, or the accepted count
conflicts with the official record:

1. enumerate the DBLP main-conference volume with `scripts/enumerate.py`;
2. use official proceedings when the DBLP volume is missing, ambiguous, or incomplete;
3. record the selected source and the reason for fallback in the task report.

Do not silently merge OpenReview, DBLP, and proceedings as if they were the same snapshot.

After a full v1/v2 run, compare it with DBLP using:

```bash
python scripts/path_a_audit.py \
  --primary <TASK>/openreview_catalog.jsonl --primary-label openreview \
  --secondary <TASK>/dblp_catalog.jsonl --secondary-label dblp \
  --out <TASK>/path_a_audit.json
```

Inspect both `only_primary_examples` and `only_secondary_examples`; title formatting changes may require Agent review even when total counts agree.

## Outputs

- `openreview_catalog.jsonl`: normalized accepted main-conference papers;
- `openreview_summary.json`: selected API, venue, invitation, counts, acceptance source, fallback
  requirement, policy, and timestamps;
- `raw/openreview/<venue>.json`: raw OpenReview notes for audit.
