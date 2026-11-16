"""Shared helpers: UTF-8 file IO, settings, polite HTTP, identifiers, dates and quote checks.

Everything here is standard-library only and behaves the same on Windows, macOS and Linux.
"""
from __future__ import annotations

import datetime
import email.utils
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def utf8_output():
    """Agents read our output through a pipe; on Chinese Windows that pipe (and NUL, which Python reports
    as a terminal) defaults to GBK, which cannot encode ✓, emoji or many paper titles and crashes the
    script. Any non-UTF-8 stream is switched to UTF-8; a real Windows console already reports UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


utf8_output()

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = Path.home() / ".config" / "paper-search-mcp" / ".env"
USER_AGENT = "PaperCat/1.0 (academic literature screening)"
ENV_KEYS = (
    "SEMANTIC_SCHOLAR_API_KEY", "OPENALEX_API_KEY", "OPENALEX_EMAIL", "UNPAYWALL_EMAIL",
    "OPENREVIEW_USERNAME", "OPENREVIEW_PASSWORD", "OPENREVIEW_TOTP_SECRET",
)


# --------------------------------------------------------------------------- files

def read_jsonl(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as stream:  # Path.write_text(newline=) is 3.10+
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def runtime_settings():
    path = SKILL_DIR / "runtime.local.json"
    return read_json(path, {}) or {}


def cache_dir(explicit=None):
    """Registry/cache folder: --cache-dir > $PAPERCAT_CACHE > runtime.local.json > ~/.cache/paper-cat.

    Names from before the rename (CS_PAPER_SEARCH_CACHE, ~/.cache/cs-paper-search) keep working."""
    value = (explicit or os.environ.get("PAPERCAT_CACHE") or os.environ.get("CS_PAPER_SEARCH_CACHE")
             or runtime_settings().get("cache_dir"))
    if value:
        return Path(value).expanduser()
    new, old = Path.home() / ".cache" / "paper-cat", Path.home() / ".cache" / "cs-paper-search"
    return old if old.exists() and not new.exists() else new


def load_env(path=None):
    """Read KEY=VALUE lines; environment variables override the file. Values are never printed."""
    path = Path(path or os.environ.get("PAPER_SEARCH_MCP_ENV_FILE") or DEFAULT_ENV_FILE)
    values = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key.strip()] = value
    for key in ENV_KEYS:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


# --------------------------------------------------------------------------- polite HTTP

# Minimum spacing between requests to one host, shared by every script and process on this machine.
HOST_INTERVALS = {
    "export.arxiv.org": 3.2,   # arXiv API terms: <= 1 request / 3 s, one connection
    "arxiv.org": 3.2,
    "api.openalex.org": 0.15,
    "api.crossref.org": 0.3,
    "api.unpaywall.org": 0.2,
    "api.semanticscholar.org": 1.1,
    "dblp.org": 1.5,
}


class HostThrottle:
    """Cross-process spacing using a timestamp file per host (no extra dependencies)."""

    def __init__(self, directory=None):
        self.directory = Path(directory or cache_dir()) / "throttle"

    def wait(self, host, interval=None):
        interval = HOST_INTERVALS.get(host, 0) if interval is None else interval
        if interval <= 0:
            return 0.0
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = self.directory / (re.sub(r"[^A-Za-z0-9.]+", "_", host) + ".last")
        lock = stamp.with_suffix(".lock")
        deadline = time.monotonic() + 120
        while True:
            try:
                descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 60:
                        lock.unlink()  # stale lock from a crashed process
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"throttle lock busy for {host}")
                time.sleep(0.05)
        try:
            try:
                last = float(stamp.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                last = 0.0
            delay = max(0.0, last + interval - time.time())
            if delay:
                time.sleep(delay)
            stamp.write_text(f"{time.time():.3f}", encoding="utf-8")
            return delay
        finally:
            os.close(descriptor)
            try:
                lock.unlink()
            except FileNotFoundError:
                pass


_THROTTLE = None


def throttle():
    global _THROTTLE
    if _THROTTLE is None:
        _THROTTLE = HostThrottle()
    return _THROTTLE


def retry_after_seconds(error, default):
    value = getattr(error, "headers", None) and error.headers.get("Retry-After")
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        parsed = email.utils.parsedate_to_datetime(value)
        return max(0.0, (parsed - datetime.datetime.now(parsed.tzinfo)).total_seconds())


def http_get(url, headers=None, timeout=30, binary=False, retries=3, backoff=(10, 30, 60),
             max_wait=120, opener=None, sleep=time.sleep):
    """GET with per-host spacing and retries on 429/5xx that honour Retry-After."""
    host = urllib.parse.urlsplit(url).hostname or ""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    opener = opener or urllib.request.build_opener()
    attempt = 0
    while True:
        throttle().wait(host)
        try:
            with opener.open(request, timeout=timeout) as response:
                payload = response.read()
            return payload if binary else payload.decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            transient = error.code == 429 or 500 <= error.code < 600
            if not transient or attempt >= retries:
                raise
            default = backoff[min(attempt, len(backoff) - 1)]
            sleep(min(max_wait, retry_after_seconds(error, default)))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt >= retries:
                raise
            sleep(min(max_wait, backoff[min(attempt, len(backoff) - 1)] / 2))
        attempt += 1


def http_json(url, **kwargs):
    return json.loads(http_get(url, **kwargs))


# --------------------------------------------------------------------------- identifiers

def normalize_doi(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value.rstrip("/") if value.startswith("10.") else ""


def arxiv_id_of(row):
    external = row.get("externalIds") or {}
    values = [row.get("arxiv_id"), external.get("ArXiv"), external.get("ARXIV")]
    doi = normalize_doi(row.get("doi") or external.get("DOI"))
    if doi.startswith("10.48550/arxiv."):
        values.append(doi.split("arxiv.", 1)[1])
    values += [row.get("url"), row.get("pdf_url")]
    for value in values:
        match = re.search(r"(?<![\d.])(\d{4}\.\d{4,5})(?:v\d+)?", str(value or ""))
        if match:
            return match.group(1)
    return ""


ACL_VENUES = {
    "acl": "conf/acl", "emnlp": "conf/emnlp", "naacl": "conf/naacl", "eacl": "conf/eacl",
    "coling": "conf/coling", "conll": "conf/conll", "aacl": "conf/ijcnlp", "ijcnlp": "conf/ijcnlp",
    "lrec": "conf/lrec", "tacl": "journals/tacl", "cl": "journals/coling",
}
ACL_TRACKS = {
    "long": "main", "main": "main", "short": "short", "findings": "findings", "demo": "demo",
    "demos": "demo", "industry": "industry", "srw": "student", "tutorials": "tutorial",
    "tutorial": "tutorial",
}


def acl_anthology_id(row):
    """Return the new-style ACL Anthology ID (e.g. 2026.findings-emnlp.889) from DOI or URL."""
    for value in (row.get("doi"), row.get("url"), row.get("pdf_url"), row.get("acl_id")):
        text = str(value or "")
        match = re.search(r"(?:10\.18653/(?:v1/)?|aclanthology\.org/)(\d{4}\.[a-z0-9-]+\.\d+)", text, re.I)
        if match:
            return match.group(1).lower()
    return ""


def parse_acl_id(anthology_id):
    """2026.findings-emnlp.889 -> {'year': 2026, 'venue_key': 'conf/emnlp', 'track': 'findings'}."""
    match = re.fullmatch(r"(\d{4})\.([a-z0-9-]+)\.(\d+)", anthology_id or "")
    if not match:
        return None
    year, volume = int(match.group(1)), match.group(2)
    parts = volume.split("-")
    if parts[0] == "findings" and len(parts) > 1:
        venue, track = parts[1], "findings"
    else:
        venue = parts[0]
        suffix = parts[1] if len(parts) > 1 else ""
        if venue in {"tacl", "cl"}:
            track = "journal"
        else:
            track = ACL_TRACKS.get(suffix, "unknown")
    key = ACL_VENUES.get(venue)
    if not key:
        track = "workshop"  # volumes named after a workshop acronym (e.g. 2026.nlp4science-1)
    return {"year": year, "volume": volume, "venue_key": key, "track": track, "venue_abbrev": venue.upper()}


# --------------------------------------------------------------------------- dates with precision

def date_interval(value, precision):
    """Return (first_day, last_day) covered by a date of the given precision."""
    year = int(value[:4])
    if precision == "year":
        return datetime.date(year, 1, 1), datetime.date(year, 12, 31)
    month = int(value[5:7])
    if precision == "month":
        nxt = datetime.date(year + (month == 12), month % 12 + 1, 1)
        return datetime.date(year, month, 1), nxt - datetime.timedelta(days=1)
    day = datetime.date.fromisoformat(value[:10])
    return day, day


def infer_date(row):
    """Best publication date with honest precision: (iso_value, precision, source) or (None, None, None).

    OpenAlex reports year-only works as YYYY-01-01, so a January 1st date from metadata is
    treated as year precision unless the record says otherwise (``date_precision``).
    """
    explicit = row.get("date_precision")
    for key in ("published_date", "publicationDate", "publication_date", "date"):
        text = str(row.get(key) or "")
        match = re.search(r"((?:19|20|21)\d{2})-(\d{2})(?:-(\d{2}))?", text)
        if match:
            year, month, day = match.group(1), match.group(2), match.group(3)
            if explicit in {"year", "month", "day"}:
                precision = explicit
            elif not day:
                precision = "month"
            elif month == "01" and day == "01":
                precision = "year"
            else:
                precision = "day"
            return f"{year}-{month}-{day or '01'}", precision, key
    arxiv = arxiv_id_of(row)
    if arxiv:
        return f"20{arxiv[:2]}-{arxiv[2:4]}-01", "month", "arxiv_id"
    acl = acl_anthology_id(row)
    if acl:
        return f"{acl[:4]}-01-01", "year", "acl_anthology_id"
    if row.get("year"):
        return f"{int(row['year']):04d}-01-01", "year", "year"
    return None, None, None


def window_status(row, start, end, today=None):
    """in_window / out_of_window / boundary (date interval straddles the window) / unknown."""
    value, precision, source = infer_date(row)
    if not value:
        return "unknown", None, None, None
    first, last = date_interval(value, precision)
    # A paper we can already see was not published in the future: "2026" seen in Nov 2026 means Jan-Nov.
    last = max(first, min(last, today or datetime.date.today()))
    if start <= first and last <= end:
        status = "in_window"
    elif last < start or first > end:
        status = "out_of_window"
    else:
        status = "boundary"
    return status, value, precision, source


# --------------------------------------------------------------------------- full-text quotes

def _fold(text):
    text = unicodedata.normalize("NFKC", html.unescape(str(text or "")))
    text = text.replace("­", "")
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)  # join hyphenated line breaks
    return text.casefold()


def quote_tokens(text):
    return re.findall(r"[^\W_]+", _fold(text))


def locate_quote(quote, pages, min_ratio=0.9):
    """Find a quote in page texts. Returns {'found', 'ratio', 'page'}.

    Exact match on letters/digits first; otherwise the best contiguous token window must
    cover >= min_ratio of the quote tokens in order (tolerates ligatures, line breaks and
    minor extraction noise, not invented sentences).
    """
    wanted = quote_tokens(quote)
    if len(wanted) < 4:
        return {"found": False, "ratio": 0.0, "page": None, "reason": "quote too short to verify"}
    joined = "".join(wanted)
    best = {"found": False, "ratio": 0.0, "page": None}
    for page_number, page in enumerate(pages, 1):
        page_tokens = quote_tokens(page)
        if joined in "".join(page_tokens):
            return {"found": True, "ratio": 1.0, "page": page_number}
        index = {}
        for position, token in enumerate(page_tokens):
            index.setdefault(token, []).append(position)
        anchors = index.get(wanted[0], []) + index.get(wanted[len(wanted) // 2], [])
        for anchor in anchors[:400]:
            window = page_tokens[max(0, anchor - len(wanted)): anchor + 2 * len(wanted)]
            ratio = _ordered_overlap(wanted, window) / len(wanted)
            if ratio > best["ratio"]:
                best = {"found": ratio >= min_ratio, "ratio": round(ratio, 3), "page": page_number}
        if best["found"]:
            return best
    return best


def _ordered_overlap(left, right):
    """Length of the longest common subsequence (small inputs only)."""
    previous = [0] * (len(right) + 1)
    for token in left:
        current = [0]
        for column, other in enumerate(right, 1):
            current.append(previous[column - 1] + 1 if token == other else max(previous[column], current[-1]))
        previous = current
    return previous[-1]


PAGE_MARK = "\n\n===== PAGE {n} =====\n"


def split_pages(text):
    """Split text written with PAGE_MARK (or legacy form feeds) back into pages."""
    if "===== PAGE " in text:
        parts = re.split(r"\n*===== PAGE \d+ =====\n", text)
        return [part for part in parts[1:]] if parts and not parts[0].strip() else parts
    return [page for page in text.split("\f") if page.strip()]


# --------------------------------------------------------------------------- optional data sources

# Every source is optional: the workflow runs on arXiv + OpenAlex (keyless) + Crossref alone. This table drives the
# friendly "you could also configure ..." hints in configure_env.py status and the init checkpoint.
SOURCE_GUIDE = [
    {"id": "openalex", "name": "OpenAlex", "keys": ["OPENALEX_API_KEY"], "priority": "推荐",
     "benefit": "免 key 也能用；配了 key 有自己的每日额度，检索、补摘要、正式发表载体与 ISSN（等级/分区靠它）更稳定，不易被限流",
     "without": "仍可免 key 使用，但与同一网络的其他人共享每日免费额度，高峰期可能被限流",
     "get": "在 openalex.org 注册，账户设置里复制 API key（免费额度）",
     "command": "configure_env.py openalex"},
    {"id": "semantic", "name": "Semantic Scholar", "keys": ["SEMANTIC_SCHOLAR_API_KEY"], "priority": "推荐",
     "benefit": "多一个高质量检索源，摘要覆盖更全",
     "without": "少一个来源，其余照常",
     "get": "semanticscholar.org/product/api 点 Request an API Key 填表，key 通过邮件发放（可能要等几天）",
     "command": "configure_env.py semantic"},
    {"id": "openreview", "name": "OpenReview", "keys": ["OPENREVIEW_USERNAME", "OPENREVIEW_PASSWORD"], "priority": "按需",
     "benefit": "按会议找全（如 ICLR）时枚举全部主会录用论文，并区分 Oral/Spotlight/Poster",
     "without": "按主题检索不受影响；按会议找全改用 DBLP/官网目录",
     "get": "openreview.net 免费注册（机构邮箱激活更快）",
     "command": "configure_env.py openreview"},
    {"id": "unpaywall", "name": "Unpaywall", "keys": ["UNPAYWALL_EMAIL"], "priority": "可选",
     "benefit": "多一个合法开放获取 PDF 下载渠道，全文获取率更高",
     "without": "少一个下载渠道",
     "get": "无需注册，只需填写一个联系邮箱",
     "command": "configure_env.py unpaywall"},
    {"id": "xiaohongshu", "name": "小红书（xiaohongshu-mcp）", "keys": [], "priority": "可选",
     "benefit": "从帖子和评论发现大家在讨论、推荐的论文（只作线索，必须再核实）",
     "without": "也可以直接把帖子文字或截图内容贴给 Agent",
     "get": "一条命令：下载并校验官方程序、启动本机服务、弹出浏览器由你扫码（Windows 若被 Defender 误拦，会提示你自行决定是否排除）",
     "command": "xhs_setup.py --setup"},
]
XHS_MCP_PORT = 18060


def port_open(port, host="127.0.0.1", timeout=0.5):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, int(port))) == 0


def source_status(settings=None):
    """[{**guide, 'configured': bool}] without network calls (fast enough for every checkpoint)."""
    settings = load_env() if settings is None else settings
    rows = []
    for guide in SOURCE_GUIDE:
        if guide["id"] == "xiaohongshu":
            configured = port_open(XHS_MCP_PORT)
        else:
            configured = all(str(settings.get(key) or "").strip() for key in guide["keys"])
        rows.append({**guide, "configured": configured})
    return rows


# --------------------------------------------------------------------------- discipline packs

def list_packs():
    folder = SKILL_DIR / "packs"
    return sorted(path.stem for path in folder.glob("*.json")) if folder.exists() else []


def load_pack(name="general"):
    """Discipline pack (packs/<name>.json): default sources and which grading systems apply."""
    name = str(name or "general")
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", name) or name not in list_packs():
        raise ValueError(f"unknown discipline pack {name!r}; available: {', '.join(list_packs())}")
    pack = read_json(SKILL_DIR / "packs" / f"{name}.json")
    for key in ("id", "name", "sources", "grading"):
        if key not in pack:
            raise ValueError(f"pack {name} is missing {key!r}")
    return pack
