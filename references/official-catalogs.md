# Official venue catalogs

Read this reference for Path A or when a hybrid topic search needs an authoritative venue universe.

## ACL Anthology and PMLR

`scripts/official_enumerate.py` parses saved or live official indexes.

```bash
python scripts/official_enumerate.py --source acl \
  --label emnlp_main --venue "EMNLP 2026" --year 2026 \
  --url https://aclanthology.org/volumes/2026.emnlp-main/ \
  --out emnlp.jsonl --summary emnlp.summary.json
```

Use `--input` for an already saved official page when reproducibility requires a frozen snapshot. ACL records include abstracts when the volume exposes them. PMLR records normally need abstract enrichment.

## Other proceedings pages

`scripts/official_html.py` handles official main-track HTML lists such as NeurIPS proceedings, CVF Open Access, ECVA, USENIX, AAAI OJS and IJCAI proceedings.

```bash
python scripts/official_html.py \
  --url https://proceedings.neurips.cc/paper_files/paper/2024 \
  --venue "NeurIPS 2024" --venueid NeurIPS.cc/2024/Conference \
  --track-label main --year 2024 \
  --link-pattern '/paper_files/paper/2024/hash/' \
  --out neurips.jsonl --summary neurips.summary.json
```

The caller must provide an official main-track page and a link regex that selects paper records. Do not point it at a Findings, Workshop, Demo, Industry, tutorial, or mixed archive page. If the page mixes tracks, use a dedicated main-track URL or a source-specific parser.

## Acceptance

- verify record count against the official page or DBLP volume;
- confirm every catalog summary says the requested main track;
- use `path_a_audit.py` when two independent catalogs exist;
- keep official URL, snapshot hash, year and venue ID in the summary;
- do not treat title-only official records as irrelevant merely because abstracts are missing.
