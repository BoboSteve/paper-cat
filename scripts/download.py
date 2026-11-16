#!/usr/bin/env python3
"""download.py: fetch full-text PDFs for Top K by trying every legal open channel in turn.

Order per record (first valid PDF wins, every attempt is logged in manifest.json):
  1. PDF link supplied by the source record (pdf_url)
  2. Open-access PDF already found during metadata repair (openAccessPdf)
  3. ACL Anthology PDF for ACL DOIs
  4. arXiv PDF (with a version label from comment / journal-ref)
  5. OpenAlex open-access locations looked up by DOI
  6. Unpaywall by DOI (needs UNPAYWALL_EMAIL or OPENALEX_EMAIL in the env file)
Failures go to the manual-retrieval queue. ACM DL pages that need a browser are marked for the
agent's browser tool or the user. No shadow libraries are used.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (acl_anthology_id, arxiv_id_of, http_get, http_json, load_env, normalize_doi,  # noqa: E402
                    read_jsonl, write_json)

record_arxiv = arxiv_id_of  # backwards-compatible name used by older tests


def arxiv_meta(arxiv_id):
    xml = http_get(f"https://export.arxiv.org/api/query?id_list={arxiv_id}", timeout=30)
    entry = xml.split("<entry>")[1] if "<entry>" in xml else ""

    def tag(name):
        match = re.search(rf"<{name}[^>]*>(.*?)</{name}>", entry, re.S)
        return match.group(1).strip() if match else ""

    comment = re.search(r"<arxiv:comment[^>]*>(.*?)</arxiv:comment>", entry, re.S)
    jref = re.search(r"<arxiv:journal_ref[^>]*>(.*?)</arxiv:journal_ref>", entry, re.S)
    return {"published": tag("published"), "updated": tag("updated"),
            "comment": re.sub(r"<[^>]+>", "", comment.group(1)).strip() if comment else "",
            "journal_ref": re.sub(r"<[^>]+>", "", jref.group(1)).strip() if jref else ""}


def version_label(meta, pub_year):
    """Comment / journal-ref signals first, update date only as a fallback."""
    signal = (meta.get("comment", "") + " " + meta.get("journal_ref", "")).lower()
    for keyword in ["accepted", "camera-ready", "camera ready", "proceedings", "published in"]:
        if keyword in signal:
            return f"预印本（疑似同录用版：信号『{keyword}』）", "fresh"
    updated = (meta.get("updated") or "")[:10]
    if pub_year and updated and updated[:4] >= str(pub_year):
        return "预印本（版本时间不早于发表年，疑似较新）", "fresh"
    return "预印本（可能过时，待核实）", "stale"


def openalex_pdf_urls(doi, settings):
    key = settings.get("OPENALEX_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    work = http_json(f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi)}",
                     headers=headers, timeout=30, retries=1)
    urls = [(work.get("best_oa_location") or {}).get("pdf_url")]
    urls += [location.get("pdf_url") for location in work.get("locations") or []]
    return [url for url in dict.fromkeys(urls) if url]


def unpaywall_pdf_urls(doi, email):
    payload = http_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(email)}",
                        timeout=30, retries=1)
    urls = [(payload.get("best_oa_location") or {}).get("url_for_pdf")]
    urls += [location.get("url_for_pdf") for location in payload.get("oa_locations") or []]
    return [url for url in dict.fromkeys(urls) if url]


def channels(row, settings):
    """Yield (channel, version_label, url_or_callable) in priority order."""
    if row.get("pdf_url"):
        yield "source_pdf", "来源提供的 PDF", row["pdf_url"]
    if row.get("openAccessPdf"):
        yield "open_access_pdf", "开放获取版本（OA 元数据）", row["openAccessPdf"]
    acl = acl_anthology_id(row)
    if acl:
        yield "acl_anthology", "ACL Anthology 正式版", f"https://aclanthology.org/{acl}.pdf"
    arxiv = arxiv_id_of(row)
    if arxiv:
        yield "arxiv", None, f"https://arxiv.org/pdf/{arxiv}"
    doi = normalize_doi(row.get("doi"))
    if doi and not doi.startswith("10.48550/"):
        yield "openalex_oa", "开放获取版本（OpenAlex 位置）", lambda: openalex_pdf_urls(doi, settings)
        email = settings.get("UNPAYWALL_EMAIL") or settings.get("OPENALEX_EMAIL")
        if email:
            yield "unpaywall", "开放获取版本（Unpaywall）", lambda: unpaywall_pdf_urls(doi, email)


def fetch_record(index, row, out_dir, settings, fetch=None):
    fetch = fetch or (lambda url: http_get(url, timeout=60, binary=True, retries=2))
    item = {"idx": index, "record_id": row.get("canonical_record_id") or row.get("record_id"),
            "title": str(row.get("title") or "")[:120], "status": "failed", "channel": None,
            "version_label": None, "file": None, "sha256": None, "note": "", "attempts": []}
    manual = Path(out_dir) / "manual" / f"{index}.pdf"  # the user's own copy (see checkpoint 4)
    if manual.exists() and manual.read_bytes()[:4] == b"%PDF":
        payload = manual.read_bytes()
        item.update(status="ok", channel="manual", version_label="用户提供的 PDF", file=str(manual),
                    sha256=hashlib.sha256(payload).hexdigest()[:16], note="pdfs/manual")
        return item
    tried = set()
    for channel, label, target in channels(row, settings):
        try:
            urls = target() if callable(target) else [target]
        except Exception as error:
            item["attempts"].append({"channel": channel, "status": "lookup_failed", "error": type(error).__name__})
            continue
        for url in urls:
            if url in tried:
                continue
            tried.add(url)
            try:
                payload = fetch(url)
                if not payload.startswith(b"%PDF"):
                    raise ValueError("response is not a PDF (login page, HTML landing page or captcha)")
                name = f"{index}_{channel}.pdf"
                path = Path(out_dir) / name
                temporary = path.with_name(name + ".tmp")
                temporary.write_bytes(payload)
                os.replace(temporary, path)
                if channel == "arxiv":
                    try:
                        meta = arxiv_meta(arxiv_id_of(row))
                        label, _ = version_label(meta, row.get("year"))
                        item["note"] = f"arXiv updated={meta['updated'][:10]} comment={meta['comment'][:60]}"
                    except Exception as error:
                        label = "预印本（版本信息获取失败）"
                        item["note"] = f"arXiv metadata: {type(error).__name__}"
                item.update(status="ok", channel=channel, version_label=label, file=str(path),
                            sha256=hashlib.sha256(payload).hexdigest()[:16])
                item["attempts"].append({"channel": channel, "url": url, "status": "ok"})
                return item
            except Exception as error:
                item["attempts"].append({"channel": channel, "url": url, "status": "failed",
                                         "error": f"{type(error).__name__}: {str(error)[:80]}"})
    doi = normalize_doi(row.get("doi"))
    if doi.startswith("10.1145/"):
        item["note"] = "ACM DL 需浏览器访问：请用 Agent 的浏览器工具打开 https://dl.acm.org/doi/pdf/" + doi + " 或手动下载"
        item["manual_channel"] = "acm_dl_browser"
    else:
        item["note"] = "所有开放渠道均失败 → 进入人工取全文清单"
    return item


def select_indices(rows, plan_path, idx_arg, top_k):
    if idx_arg:
        return [int(value) for value in idx_arg.split(",") if value.strip()]
    if top_k is None and plan_path and Path(plan_path).exists():
        top_k = int((json.loads(Path(plan_path).read_text(encoding="utf-8")).get("jev") or {}).get("selected_k") or 0)
    eligible = [i for i, row in enumerate(rows) if not row.get("track_excluded")]
    flagged = [i for i, row in enumerate(rows) if row.get("status") == "flagged"]
    return list(dict.fromkeys(eligible[: top_k or 0] + flagged))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ranked", required=True)
    parser.add_argument("--idx", help="comma-separated rank positions; default: Top K from --plan")
    parser.add_argument("--top-k", type=int, help="override K")
    parser.add_argument("--plan")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--env-file")
    parser.add_argument("--skip-existing", action="store_true", help="keep records already downloaded")
    args = parser.parse_args()
    rows = read_jsonl(args.ranked)
    indices = select_indices(rows, args.plan, args.idx, args.top_k)
    if not indices:
        parser.error("no records selected: pass --idx, --top-k or a plan with jev.selected_k")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    previous = {}
    if args.skip_existing and manifest_path.exists():
        previous = {item["idx"]: item for item in json.loads(manifest_path.read_text(encoding="utf-8"))
                    if item.get("status") == "ok" and item.get("file") and Path(item["file"]).exists()}
    settings = load_env(args.env_file)
    manifest = []
    for index in indices:
        if not 0 <= index < len(rows):
            parser.error(f"idx out of range: {index}")
        item = previous.get(index) or fetch_record(index, rows[index], out_dir, settings)
        manifest.append(item)
        print(f"[{index}] {item['status']} {item['channel'] or ''} "
              f"{item['version_label'] or item['note'][:70]} (attempts={len(item.get('attempts') or [])})", flush=True)
    write_json(manifest_path, manifest)
    ok = sum(item["status"] == "ok" for item in manifest)
    print(f"DOWNLOAD_FINISHED ok={ok} failed={len(manifest) - ok} -> {manifest_path}")


if __name__ == "__main__":
    main()
