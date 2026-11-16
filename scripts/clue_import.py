#!/usr/bin/env python3
"""Turn Xiaohongshu (RedNote) notes and comments into verified paper seeds.

Input files (any mix):
  * JSON / JSONL saved from a Xiaohongshu MCP server (e.g. xiaohongshu-mcp search_feeds /
    get_feed_detail with comments) or from MediaCrawler exports;
  * .txt / .md with post text the user pasted or transcribed from screenshots.

For every note the script extracts paper identifiers (arXiv IDs, DOIs, OpenReview / ACL / arXiv
links) and candidate English titles from the title, body and comments. With ``--resolve`` each clue
is looked up in arXiv / OpenAlex; only clues that resolve to a real paper become seeds. Community
signals (likes, saves, comment snippets) are kept separately as context, never as evidence of quality.
"""
import argparse
import datetime
import html
import json
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import http_get, http_json, load_env, normalize_doi, write_jsonl  # noqa: E402

ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|arxiv[:：\s]*)?(?<![\d.])((?:1\d|2\d)(?:0[1-9]|1[0-2])\.\d{4,5})(?:v\d+)?", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>，。；、）)]+)", re.I)
URL_RE = re.compile(r"https?://[^\s\"'<>，。；、）)]+", re.I)
TITLE_RES = [
    re.compile(r"[《“\"「『]([A-Z][^《》“”\"「」『』\n]{12,200}?)[》”\"」』]"),
    re.compile(r"(?:论文|文章|paper|题目|标题|title)\s*[:：]\s*([A-Z][^\n]{12,200})", re.I),
]
NOTE_TEXT_KEYS = ("desc", "content", "text", "note_text", "body")
NOTE_TITLE_KEYS = ("title", "display_title", "displayTitle", "note_title")
NOTE_ID_KEYS = ("note_id", "noteId", "feed_id", "feedId", "id")
COMMENT_LIST_KEYS = ("comments", "comment_list", "commentList", "sub_comments", "subComments", "replies")
COUNT_KEYS = {"likes": ("liked_count", "likedCount", "like_count", "likes"),
              "collects": ("collected_count", "collectedCount", "collect_count"),
              "comments_count": ("comment_count", "commentCount", "comments_count")}


def first(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def comment_texts(note):
    texts = []
    for key in COMMENT_LIST_KEYS:
        for item in note.get(key) or []:
            if isinstance(item, dict):
                text = first(item, ("content", "text", "comment"))
                if text:
                    texts.append({"text": str(text), "likes": first(item, ("like_count", "likeCount", "liked_count"))})
                texts.extend(comment_texts(item))
    return texts


def looks_like_note(item):
    return bool(first(item, NOTE_TITLE_KEYS) or first(item, NOTE_TEXT_KEYS)) and (
        bool(first(item, NOTE_ID_KEYS)) or any(key in item for key in COMMENT_LIST_KEYS)
        or any(first(item, keys) is not None for keys in COUNT_KEYS.values()))


def _mcp_note(note_id, card, token=None, comments=None):
    """One note in our shape from xiaohongshu-mcp's search card or feed-detail note."""
    interact = card.get("interactInfo") or {}
    user = card.get("user") or {}
    stamp = card.get("time")
    published = (datetime.datetime.fromtimestamp(stamp / 1000).date().isoformat()
                 if isinstance(stamp, (int, float)) and stamp > 0 else None)
    url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else None
    return {"note_id": note_id, "url": url, "xsec_token": token or card.get("xsecToken"),
            "title": str(card.get("displayTitle") or card.get("title") or ""), "text": str(card.get("desc") or ""),
            "author": user.get("nickname") or user.get("nickName"), "published": published,
            # counts usually sit under interactInfo; some versions put them on the note itself
            "likes": interact.get("likedCount", card.get("likedCount")),
            "collects": interact.get("collectedCount", card.get("collectedCount")),
            "comments_count": interact.get("commentCount", card.get("commentCount")),
            "comments": comment_texts({"comments": comments or []})}


def notes_from_mcp(payload):
    """Notes from xiaohongshu-mcp results (search_feeds and get_feed_detail), anywhere in the payload.

    Details win over search cards for the same note: they carry the full text and the comments.
    """
    notes = {}
    for item in walk(payload):
        if isinstance(item.get("feeds"), list):
            for feed in item["feeds"]:
                if isinstance(feed, dict) and isinstance(feed.get("noteCard"), dict) and feed.get("id"):
                    notes.setdefault(feed["id"], _mcp_note(feed["id"], feed["noteCard"], feed.get("xsecToken")))
        note = item.get("note")
        if isinstance(note, dict) and (note.get("noteId") or item.get("feed_id")) and "desc" in note:
            comments = item.get("comments")
            comments = comments.get("list") if isinstance(comments, dict) else comments
            note_id = note.get("noteId") or item.get("feed_id")
            notes[note_id] = _mcp_note(note_id, note, comments=comments if isinstance(comments, list) else [])
    return list(notes.values())


def notes_from_file(path):
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    if Path(path).suffix.lower() in {".txt", ".md"}:
        return [{"note_id": Path(path).stem, "title": "", "text": text, "comments": [], "source_file": str(path)}]
    try:
        payloads = [json.loads(text)]
    except json.JSONDecodeError:
        payloads = [json.loads(line) for line in text.splitlines() if line.strip()]
    mcp = notes_from_mcp(payloads)
    if mcp:
        return [{**note, "source_file": str(path)} for note in mcp]
    notes, seen = [], set()
    for payload in payloads:
        candidates = []
        for item in walk(payload):
            # Feed-detail responses often put comments next to the note ({"note": {...}, "comments": [...]}).
            sibling_comments = {key: item[key] for key in COMMENT_LIST_KEYS if isinstance(item.get(key), list)}
            child_notes = [value for value in item.values() if isinstance(value, dict) and looks_like_note(value)]
            if sibling_comments and child_notes and not looks_like_note(item):
                candidates.append({**child_notes[0], **sibling_comments})
            elif looks_like_note(item):
                candidates.append(item)
        for item in candidates:
            note_id = str(first(item, NOTE_ID_KEYS) or "")
            key = note_id or hash(str(first(item, NOTE_TITLE_KEYS)) + str(first(item, NOTE_TEXT_KEYS)))
            if key in seen:
                continue
            seen.add(key)
            notes.append({
                "note_id": note_id,
                "url": first(item, ("note_url", "url", "share_url", "link")) or (
                    f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else None),
                "title": str(first(item, NOTE_TITLE_KEYS) or ""),
                "text": str(first(item, NOTE_TEXT_KEYS) or ""),
                "author": first(item, ("nickname", "author", "user_name")) or first(item.get("user") or {}, ("nickname",)),
                "published": first(item, ("time", "publish_time", "create_time")),
                **{name: first(item, keys) for name, keys in COUNT_KEYS.items()},
                "comments": comment_texts(item),
                "source_file": str(path),
            })
    return notes


def strip_list_marker(line):
    """Drop leading emoji, bullets, numbering and short bracket tags such as [一R] or 【1】."""
    while True:
        stripped = re.sub(r"^[^\w\[【]+|^\d+[.、)）]\s*", "", line)
        stripped = re.sub(r"^(?:\[[^\]]{0,6}\]|【[^】]{0,6}】)", "", stripped)
        if stripped == line:
            return line
        line = stripped


def paper_names(text, limit=12):
    """Lines that name a paper the way posts usually do: '🤖 HarnessDev' or 'R-Quest：一句点评'."""
    names = []
    for line in text.splitlines():
        line = strip_list_marker(line.strip())
        head = re.split(r"[：:｜|（(]", line, maxsplit=1)[0].strip(" .-")
        if not (2 <= len(head) <= 80) or head.startswith(("#", "http")) or not head[0].isalpha():
            continue
        ascii_share = sum(c.isascii() for c in head) / len(head)
        if ascii_share >= 0.9 and len(head.split()) <= 12 and re.search(r"[A-Za-z]{2}", head):
            names.append(head)
    return list(dict.fromkeys(names))[:limit]


def extract(text):
    text = html.unescape(text or "")
    found = {"arxiv": [], "doi": [], "urls": [], "titles": [], "names": paper_names(text)}
    for match in ARXIV_RE.finditer(text):
        found["arxiv"].append(match.group(1))
    for match in DOI_RE.finditer(text):
        doi = normalize_doi(match.group(1).rstrip(".,;"))
        if doi and not doi.startswith("10.48550/arxiv"):
            found["doi"].append(doi)
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;")
        if any(host in url for host in ("openreview.net", "aclanthology.org", "arxiv.org", "doi.org",
                                        "proceedings.", "dl.acm.org", "ieeexplore", "github.com")):
            found["urls"].append(url)
    for pattern in TITLE_RES:
        for match in pattern.finditer(text):
            title = re.sub(r"\s+", " ", match.group(1)).strip(" .")
            if len(title.split()) >= 3:
                found["titles"].append(title)
    return {key: list(dict.fromkeys(values)) for key, values in found.items()}


def note_clues(note):
    main = extract(note["title"] + "\n" + note["text"])
    from_comments = [extract(item["text"]) for item in note["comments"]]
    merged = {key: list(dict.fromkeys(main[key] + [v for c in from_comments for v in c[key]])) for key in main}
    return merged, sorted({value for values in main.values() for value in values})


def resolve_arxiv(ids, timeout=30):
    found = {}
    ids = list(dict.fromkeys(ids))
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": ",".join(chunk), "max_results": len(chunk)})
        root = ET.fromstring(http_get(url, timeout=timeout))
        atom = "{http://www.w3.org/2005/Atom}"
        for entry in root.findall(atom + "entry"):
            match = re.search(r"abs/(\d{4}\.\d{4,5})", entry.findtext(atom + "id") or "")
            title = re.sub(r"\s+", " ", entry.findtext(atom + "title") or "").strip()
            if match and title and title.lower() != "error":
                arxiv_id = match.group(1)
                found[arxiv_id] = {
                    "title": title, "abstract": re.sub(r"\s+", " ", entry.findtext(atom + "summary") or "").strip(),
                    "authors": [(a.findtext(atom + "name") or "").strip() for a in entry.findall(atom + "author")],
                    "published_date": (entry.findtext(atom + "published") or "")[:10], "date_precision": "day",
                    "doi": f"10.48550/arxiv.{arxiv_id}", "url": f"https://arxiv.org/abs/{arxiv_id}",
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}"}
    return found


def openalex_work(settings, doi=None, title=None, timeout=30):
    key = settings.get("OPENALEX_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    select = "id,doi,title,publication_date,authorships,abstract_inverted_index,primary_location"
    if doi:
        url = f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi)}?select={select}"
        return [http_json(url, headers=headers, timeout=timeout, retries=1)]
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
        {"filter": f"title.search:{title}", "per-page": 3, "select": select})
    return http_json(url, headers=headers, timeout=timeout, retries=1).get("results") or []


def from_openalex(work):
    inverted = work.get("abstract_inverted_index") or {}
    words = {pos: word for word, positions in inverted.items() for pos in positions}
    return {"title": work.get("title") or "", "doi": normalize_doi(work.get("doi")),
            "published_date": work.get("publication_date"),
            "authors": [(a.get("author") or {}).get("display_name", "") for a in work.get("authorships") or []],
            "abstract": " ".join(words[i] for i in sorted(words)),
            "url": work.get("doi") or work.get("id")}


def resolve_name(name, timeout=30):
    """A short paper name ('HarnessDev') -> the arXiv paper whose title is that name or starts with 'name:'."""
    query = "ti:" + (f'"{name}"' if " " in name else name)
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"search_query": query, "max_results": 5})
    try:
        root = ET.fromstring(http_get(url, timeout=timeout))
    except Exception:
        return None
    atom = "{http://www.w3.org/2005/Atom}"
    wanted = re.sub(r"\s+", " ", name).strip().lower()
    for entry in root.findall(atom + "entry"):
        title = re.sub(r"\s+", " ", entry.findtext(atom + "title") or "").strip()
        head = re.split(r"[:：]", title, maxsplit=1)[0].strip().lower()
        match = re.search(r"abs/(\d{4}\.\d{4,5})", entry.findtext(atom + "id") or "")
        if match and (head == wanted or title.lower() == wanted):
            arxiv_id = match.group(1)
            return {"title": title, "abstract": re.sub(r"\s+", " ", entry.findtext(atom + "summary") or "").strip(),
                    "authors": [(a.findtext(atom + "name") or "").strip() for a in entry.findall(atom + "author")],
                    "published_date": (entry.findtext(atom + "published") or "")[:10], "date_precision": "day",
                    "doi": f"10.48550/arxiv.{arxiv_id}", "url": f"https://arxiv.org/abs/{arxiv_id}",
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}"}
    return None


def resolve_clues(clues, settings):
    from metadata_repair import title_matches
    arxiv = resolve_arxiv([value for clue in clues for value in clue["found"]["arxiv"]])
    seeds = []
    for clue in clues:
        resolved = []
        for value in clue["found"]["arxiv"]:
            if value in arxiv:
                resolved.append({**arxiv[value], "matched_by": f"arxiv:{value}"})
        for doi in clue["found"]["doi"]:
            try:
                works = openalex_work(settings, doi=doi)
                resolved += [{**from_openalex(w), "matched_by": f"doi:{doi}"} for w in works if w.get("title")]
            except Exception as error:
                clue.setdefault("errors", []).append(f"doi {doi}: {type(error).__name__}")
        for title in clue["found"]["titles"]:
            try:
                works = [from_openalex(w) for w in openalex_work(settings, title=title)]
            except Exception as error:
                clue.setdefault("errors", []).append(f"title: {type(error).__name__}")
                continue
            match = next((w for w in works if title_matches(title, w["title"])), None)
            if match:
                resolved.append({**match, "matched_by": f"title:{title}"})
            else:
                clue.setdefault("unresolved_titles", []).append(title)
        for name in clue["found"].get("names") or []:
            if any(name.lower() in r["title"].lower() for r in resolved):
                continue
            match = resolve_name(name)
            if match:
                resolved.append({**match, "matched_by": f"name:{name}"})
            else:
                clue.setdefault("unresolved_names", []).append(name)
        clue["resolved"] = [{"title": r["title"], "matched_by": r["matched_by"]} for r in resolved]
        for record in resolved:
            seeds.append({**record, "source": "xhs_clue", "seed_role": "community_recommendation",
                          "clue_note_id": clue["note_id"], "clue_note_url": clue["url"],
                          "clue_in_comments_only": record["matched_by"].split(":", 1)[1] not in clue["found_in_post"],
                          "community_signal": {k: clue.get(k) for k in ("likes", "collects", "comments_count")}})
    return merge_seeds(seeds)


def merge_seeds(seeds):
    """One seed per paper; every note that mentioned it is kept (how widely it is discussed)."""
    merged = {}
    for seed in seeds:
        # Title first: the same paper reached by arXiv id and by OpenAlex title carries two different DOIs.
        key = (re.sub(r"[^a-z0-9]+", "", str(seed.get("title") or "").lower()) or normalize_doi(seed.get("doi"))
               or seed.get("url") or f"unkeyed-{len(merged)}")
        if key not in merged:
            merged[key] = {**seed, "mentioned_in_notes": []}
        notes = merged[key]["mentioned_in_notes"]
        if seed.get("clue_note_url") and seed["clue_note_url"] not in notes:
            notes.append(seed["clue_note_url"])
        merged[key]["clue_in_comments_only"] = bool(merged[key].get("clue_in_comments_only")
                                                    and seed.get("clue_in_comments_only"))
    for seed in merged.values():
        seed["mention_count"] = len(seed["mentioned_in_notes"])
    return sorted(merged.values(), key=lambda s: -s["mention_count"])


def short_name(title):
    """'HarnessDev: Can LLMs ...' -> 'HarnessDev'; otherwise the first five words."""
    head = title.split(":", 1)[0].strip()
    return head if ":" in title and 1 <= len(head.split()) <= 4 else " ".join(title.split()[:5])


def suggest(task, decisions=("core", "periphery")):
    """Reverse lookup plan: one Xiaohongshu search keyword per paper that was read."""
    ranked = [json.loads(line) for line in (task / "ranked.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    judged = [json.loads(line) for line in (task / "judgments.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    plan = [{"position": j["idx"], "keyword": short_name(ranked[j["idx"]]["title"]), "title": ranked[j["idx"]]["title"]}
            for j in judged if j.get("decision") in decisions]
    (task / "clues").mkdir(exist_ok=True)
    (task / "clues" / "lookup_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    for item in plan:
        print(f"position {item['position']}: search 「{item['keyword']}」 -> save as clues/lookup_{item['position']}.json")
    return plan


def mentions(note, keyword, title):
    text = " ".join([note["title"], note["text"]] + [c["text"] for c in note["comments"]]).lower()
    if len(keyword) >= 4 and re.search(rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])", text):
        return True
    words = set(re.findall(r"[a-z0-9]+", title.lower())) - {"a", "an", "the", "of", "for", "and", "with", "in", "on", "to"}
    return bool(words) and len(words & set(re.findall(r"[a-z0-9]+", text))) / len(words) >= 0.6


def attach(task):
    """Attach community signals from clues/lookup_<position>*.json to ranked.jsonl rows (reference only)."""
    plan = {item["position"]: item for item in json.loads((task / "clues" / "lookup_plan.json").read_text(encoding="utf-8"))}
    ranked_path = task / "ranked.jsonl"
    ranked = [json.loads(line) for line in ranked_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    attached = 0
    for path in sorted((task / "clues").glob("lookup_*.json")):
        match = re.match(r"lookup_(\d+)", path.stem)
        if not match or int(match.group(1)) not in plan:
            continue
        item = plan[int(match.group(1))]
        notes = [n for n in notes_from_file(path) if mentions(n, item["keyword"], item["title"])]
        if not notes:
            continue
        comments = sorted((c for n in notes for c in n["comments"]), key=lambda c: -_count(c.get("likes")))
        line = (comments[0]["text"] if comments else notes[0]["title"] or notes[0]["text"])[:60]
        ranked[item["position"]]["community"] = {
            "posts": len(notes), "likes": sum(_count(n.get("likes")) for n in notes), "line": line,
            "notes": [n["url"] for n in notes if n.get("url")][:5]}
        attached += 1
    write_jsonl(ranked_path, ranked)
    print(f"XHS_ATTACHED papers={attached} (shown in the report as community feedback, never as evidence)")


def _count(value):
    text = str(value or "0").replace(",", "")
    match = re.match(r"([\d.]+)\s*([万wWkK]?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    return int(number * {"万": 10000, "w": 10000, "W": 10000, "k": 1000, "K": 1000}.get(match.group(2), 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", action="append")
    parser.add_argument("--out-clues", help="one line per note: identifiers, comments, signals")
    parser.add_argument("--out-seeds", help="verified paper seeds for merge_candidates.py")
    parser.add_argument("--resolve", action="store_true", help="look clues up in arXiv/OpenAlex (needed for seeds)")
    parser.add_argument("--suggest", metavar="TASK", help="write a reverse-lookup keyword per read paper")
    parser.add_argument("--attach", metavar="TASK", help="attach saved lookup results to the task's papers")
    parser.add_argument("--env-file")
    args = parser.parse_args()
    if args.suggest:
        suggest(Path(args.suggest).resolve())
        return
    if args.attach:
        attach(Path(args.attach).resolve())
        return
    if not (args.input and args.out_clues and args.out_seeds):
        parser.error("--input, --out-clues and --out-seeds are required (or use --suggest / --attach)")
    clues = []
    for path in args.input:
        for note in notes_from_file(path):
            found, in_post = note_clues(note)
            clues.append({**{k: v for k, v in note.items() if k != "comments"},
                          "comment_samples": [c["text"][:200] for c in note["comments"][:10]],
                          "found": found, "found_in_post": in_post})
    seeds = resolve_clues(clues, load_env(args.env_file)) if args.resolve else []
    write_jsonl(args.out_clues, clues)
    write_jsonl(args.out_seeds, seeds)
    total = sum(len(c["found"][key]) for c in clues for key in ("arxiv", "doi", "titles", "names"))
    print(f"CLUES notes={len(clues)} paper_mentions={total} verified_seeds={len(seeds)} -> {args.out_seeds}")


if __name__ == "__main__":
    main()
