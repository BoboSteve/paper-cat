#!/usr/bin/env python3
"""Enumerate accepted main-conference submissions from OpenReview API v1 or v2."""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path


DEFAULT_ENV_FILE = Path.home() / ".config" / "paper-search-mcp" / ".env"
API_BASEURLS = {
    "v1": "https://api.openreview.net",
    "v2": "https://api2.openreview.net",
}
NON_MAIN_MARKERS = (
    "findings",
    "workshop",
    "demo",
    "industry",
    "short",
    "student abstract",
    "doctoral consortium",
    "withdraw",
    "desk reject",
)
SETTING_NAMES = (
    "OPENREVIEW_USERNAME",
    "OPENREVIEW_PASSWORD",
    "OPENREVIEW_TOTP_SECRET",
)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha256_json(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_settings(path):
    values = {}
    path = Path(path)
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value
    for key in SETTING_NAMES:
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def content_value(content, key, default=None):
    value = (content or {}).get(key, default)
    return value.get("value", default) if isinstance(value, dict) else value


def direct_replies(note):
    details = note.details if isinstance(note.details, dict) else {}
    replies = details.get("directReplies") or []
    return [reply for reply in replies if isinstance(reply, dict)]


def reply_invitations(reply):
    invitations = reply.get("invitations")
    if invitations:
        return [invitations] if isinstance(invitations, str) else invitations
    invitation = reply.get("invitation")
    if isinstance(invitation, str):
        return [invitation]
    return invitation or []


def latest_decision(note):
    decisions = []
    for reply in direct_replies(note):
        content = reply.get("content") or {}
        decision = content_value(content, "decision")
        invitations = reply_invitations(reply)
        if not decision or not any("decision" in str(value).lower() for value in invitations):
            continue
        timestamp = reply.get("mdate") or reply.get("tcdate") or reply.get("cdate") or 0
        decisions.append((timestamp, str(decision)))
    return max(decisions, default=(None, None))[1]


def presentation_for(value):
    value = (value or "").lower()
    for kind in ("oral", "spotlight", "poster"):
        if kind in value:
            return kind
    if "talk" in value:
        return "oral"
    return "main"


def classify_v1_note(note, venue_id):
    content = note.content or {}
    content_venue_id = content_value(content, "venueid")
    venue = str(content_value(content, "venue") or "")
    decision = latest_decision(note)
    combined = f"{venue} {decision or ''}".lower()
    if content_venue_id and content_venue_id != venue_id:
        return "non_main", None, decision
    if any(marker in combined for marker in NON_MAIN_MARKERS):
        return "non_main", None, decision
    accepted = content_venue_id == venue_id or bool(
        decision and re.search(r"\baccept(?:ed)?\b", decision, re.I)
    )
    if accepted:
        return "accepted", presentation_for(combined), decision
    if decision:
        return "rejected", None, decision
    return "unresolved", None, None


def timestamp_iso(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    seconds = value / 1000 if value > 10_000_000_000 else value
    try:
        return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def venue_year(venue_id):
    match = re.search(r"(?:^|/)((?:19|20|21)\d{2})(?:/|$)", venue_id)
    return int(match.group(1)) if match else None


def normalize_note(note, venue_id, invitation, api_version, presentation=None, decision=None):
    content = note.content or {}
    if api_version == "v2" and content_value(content, "venueid") != venue_id:
        return None
    title = str(content_value(content, "title") or "").strip()
    if not title:
        return None
    authors = content_value(content, "authors", []) or []
    if not isinstance(authors, list):
        authors = []
    venue = str(content_value(content, "venue") or decision or venue_id)
    presentation = presentation or presentation_for(venue)
    pdf = str(content_value(content, "pdf") or "")
    if pdf and not pdf.startswith(("http://", "https://")):
        pdf = f"https://openreview.net/pdf?id={note.id}"
    record = {
        "record_id": f"openreview:{note.id}",
        "openreview_id": note.id,
        "forum": getattr(note, "forum", None) or note.id,
        "title": title,
        "authors": [str(author) for author in authors if author],
        "abstract": str(content_value(content, "abstract") or "").strip(),
        "year": venue_year(venue_id),
        "venue": venue,
        "venueid": venue_id,
        "track": "main",
        "presentation": presentation,
        "record_type": "conference",
        "status": "accepted_main",
        "source": "openreview",
        "source_api": api_version,
        "source_invitation": getattr(note, "invitation", None) or invitation,
        "decision": decision,
        "url": f"https://openreview.net/forum?id={note.id}",
        "pdf_url": pdf,
        "published_date": timestamp_iso(getattr(note, "pdate", None) or getattr(note, "cdate", None)),
        "keywords": content_value(content, "keywords", []) or [],
        "primary_area": content_value(content, "primary_area"),
        "tldr": content_value(content, "TLDR"),
        "paperhash": content_value(content, "paperhash"),
        "note_number": getattr(note, "number", None),
    }
    record["source_note_sha256"] = sha256_json(note.to_json())
    return record


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def api_baseurl(api_version, override=None):
    return override or API_BASEURLS[api_version]


def build_client(openreview, settings, api_version, baseurl):
    client_args = {
        "baseurl": baseurl,
        "username": settings["OPENREVIEW_USERNAME"],
        "password": settings["OPENREVIEW_PASSWORD"],
    }
    totp_secret = settings.get("OPENREVIEW_TOTP_SECRET", "").strip()
    if totp_secret:
        try:
            import pyotp
        except ImportError:
            raise RuntimeError("OPENREVIEW_TOTP_SECRET is set but pyotp is not installed")
        client_args["mfa_code_provider"] = lambda method: pyotp.TOTP(totp_secret).now()
    client_class = openreview.Client if api_version == "v1" else openreview.api.OpenReviewClient
    return client_class(**client_args)


def default_invitations(api_version):
    if api_version == "v1":
        return ["{venue}/-/Blind_Submission", "{venue}/-/Submission"]
    return ["{venue}/-/Submission"]


def choose_api(openreview, settings, venue_id, api_version, invitation, baseurl):
    versions = ["v1", "v2"] if api_version == "auto" else [api_version]
    if api_version == "auto" and invitation and "Blind_Submission" in invitation:
        versions.reverse()
    errors = {}
    for version in versions:
        try:
            client = build_client(openreview, settings, version, api_baseurl(version, baseurl))
            group = client.get_group(id=venue_id)
            if not group or group.id != venue_id:
                raise ValueError(f"main venue group not found: {venue_id}")
            candidates = [invitation] if invitation else [
                value.format(venue=venue_id) for value in default_invitations(version)
            ]
            for candidate in candidates:
                notes = client.get_notes(invitation=candidate, limit=1)
                if notes:
                    return client, version, candidate
            if api_version != "auto":
                return client, version, candidates[-1]
        except Exception as error:
            errors[version] = type(error).__name__
    detail = ", ".join(f"{version}={error}" for version, error in errors.items())
    raise ValueError(f"no OpenReview submissions found for {venue_id} ({detail})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--venue-id", required=True, help="exact main venue group, e.g. ICLR.cc/2026/Conference")
    parser.add_argument("--out", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--api-version", choices=("auto", "v1", "v2"), default="auto")
    parser.add_argument("--invitation", help="defaults by API version")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--baseurl", help="override the selected API endpoint")
    parser.add_argument("--limit", type=int, help="testing only; omit for the full main-conference catalog")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")

    settings = load_settings(args.env_file)
    missing = [name for name in ("OPENREVIEW_USERNAME", "OPENREVIEW_PASSWORD") if not settings.get(name)]
    if missing:
        parser.error(f"missing OpenReview settings: {', '.join(missing)} in {args.env_file}")

    try:
        import openreview
    except ImportError:
        parser.error("openreview-py is not installed; install requirements-openreview.txt")

    started_at = now()
    summary_path = Path(args.summary)
    try:
        client, selected_api, invitation = choose_api(
            openreview, settings, args.venue_id, args.api_version, args.invitation, args.baseurl
        )
        query = {"invitation": invitation, "sort": "number:asc"}
        if selected_api == "v2":
            query["content"] = {"venueid": args.venue_id}
        else:
            query["details"] = "directReplies"
        if args.limit:
            notes = client.get_notes(**query, limit=args.limit)
        else:
            notes = client.get_all_notes(**query)
    except Exception as error:
        write_json(
            summary_path,
            {
                "status": "failed",
                "requested_api": args.api_version,
                "venue_id": args.venue_id,
                "error_type": type(error).__name__,
                "started_at": started_at,
                "finished_at": now(),
                "fallback_required": True,
                "fallback_order": ["dblp", "official_proceedings"],
            },
        )
        print(f"OPENREVIEW_FAILED {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)

    records = []
    excluded = {"non_main": 0, "rejected": 0, "unresolved": 0, "malformed": 0}
    seen = set()
    for note in notes:
        if note.id in seen:
            continue
        seen.add(note.id)
        if not str(content_value(note.content or {}, "title") or "").strip():
            excluded["malformed"] += 1
            continue
        presentation = decision = None
        if selected_api == "v1":
            outcome, presentation, decision = classify_v1_note(note, args.venue_id)
            if outcome != "accepted":
                excluded[outcome] += 1
                continue
        record = normalize_note(
            note,
            args.venue_id,
            invitation,
            selected_api,
            presentation=presentation,
            decision=decision,
        )
        if record is None:
            excluded["non_main"] += 1
            continue
        records.append(record)

    output = Path(args.out)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", args.venue_id)
    raw_path = output.parent / "raw" / "openreview" / f"{slug}.json"
    raw = {
        "venue_id": args.venue_id,
        "invitation": invitation,
        "api_version": selected_api,
        "notes": [note.to_json() for note in notes],
    }
    write_jsonl(output, records)
    write_json(raw_path, raw)
    summary = {
        "status": "ok" if records else "empty",
        "requested_api": args.api_version,
        "api_version": selected_api,
        "baseurl": api_baseurl(selected_api, args.baseurl),
        "venue_id": args.venue_id,
        "invitation": invitation,
        "track_policy": "main_only",
        "presentation_formats": ["main", "oral", "spotlight", "poster"],
        "acceptance_source": "venueid" if selected_api == "v2" else "venueid_or_decision_replies",
        "limit": args.limit,
        "notes_returned": len(notes),
        "papers_written": len(records),
        "excluded_non_main": excluded["non_main"],
        "excluded_rejected": excluded["rejected"],
        "excluded_unresolved": excluded["unresolved"],
        "excluded_malformed": excluded["malformed"],
        "fallback_required": not records,
        "fallback_order": ["dblp", "official_proceedings"],
        "started_at": started_at,
        "finished_at": now(),
        "raw_path": str(raw_path.relative_to(output.parent)),
        "output_path": str(output),
    }
    write_json(summary_path, summary)
    if records:
        print(
            f"OPENREVIEW_FINISHED api={selected_api} venue={args.venue_id} notes={len(notes)} "
            f"main_papers={len(records)} excluded={excluded} -> {output}",
            flush=True,
        )
    else:
        print(
            f"OPENREVIEW_EMPTY api={selected_api} venue={args.venue_id} notes={len(notes)} "
            "fallback=dblp,official_proceedings",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
