#!/usr/bin/env python3
"""Build the shareable zip and refuse to include machine-specific or personal data.

    python scripts/package_skill.py                         # -> ../dist/paper-cat-<version>.zip
    python scripts/package_skill.py --forbid "MyName" --forbid "my-lab"   # extra strings that must not appear
    python scripts/package_skill.py --check                 # scan only, write nothing

Never packaged: runtime.local.json (this machine's paths), caches, logs, partial downloads, .env files,
cookies. Every packaged text file is scanned for absolute user paths, e-mail addresses and the
--forbid strings; any hit stops the build.
"""
import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import utf8_output  # noqa: E402  (GBK pipes on Chinese Windows)

utf8_output()
EXCLUDED_NAMES = {"runtime.local.json", ".DS_Store", "Thumbs.db"}
EXCLUDED_DIRS = {"__pycache__", ".git", ".pytest_cache", ".venv", "dist"}
EXCLUDED_PATTERNS = (r"\.pyc$", r"\.part$", r"\.tmp$", r"\.log$", r"^\.env", r"cookies.*\.json$")
TEXT_SUFFIXES = {".md", ".py", ".json", ".jsonl", ".yaml", ".yml", ".txt", ".toml", ".ps1", ".csv", ".svg", ".html"}
PERSONAL = [
    (re.compile(r"\b[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+[^\\/<>\s\"']+", re.I), "Windows user path"),
    (re.compile(r"(?<![<\w])/(?:Users|home)/[^/<>\s\"']+/"), "home directory path"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "e-mail address"),
]
ALLOWED_EMAIL = re.compile(r"(?:noreply|example)\b|@(?:example\.|anthropic\.com|[a-z]\.(?:org|com)$)")  # + test fakes


def frontmatter(text):
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        raise SystemExit("SKILL.md has no YAML frontmatter")
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if line and not line.startswith(" ") and value is not None:
            fields[key.strip()] = value.strip().strip('"')
        elif line.strip().startswith("version:"):
            fields["version"] = line.split(":", 1)[1].strip().strip('"')
    return fields


def included(root):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_dir() or set(relative.parts) & EXCLUDED_DIRS or path.name in EXCLUDED_NAMES:
            continue
        if any(re.search(pattern, path.name) for pattern in EXCLUDED_PATTERNS):
            continue
        yield path, relative


def scan(files, forbid):
    problems = []
    for path, relative in files:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            for pattern, label in PERSONAL:
                for match in pattern.finditer(line):
                    if label == "e-mail address" and ALLOWED_EMAIL.search(match.group(0)):
                        continue
                    problems.append(f"{relative}:{number}: {label}: {match.group(0)[:60]}")
            for word in forbid:
                if word and word.lower() in line.lower():
                    problems.append(f"{relative}:{number}: forbidden string: {word}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", default=str(SKILL_DIR.parent / "dist"))
    parser.add_argument("--forbid", action="append", default=[], help="string that must not appear (repeatable)")
    parser.add_argument("--check", action="store_true", help="scan only")
    args = parser.parse_args()
    meta = frontmatter((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"))
    for key in ("name", "description", "version"):
        if not meta.get(key):
            raise SystemExit(f"SKILL.md frontmatter is missing {key}")
    if len(meta["description"]) > 1024:
        raise SystemExit(f"SKILL.md description is {len(meta['description'])} characters; keep it within 1024")
    files = list(included(SKILL_DIR))
    problems = scan(files, args.forbid)
    if problems:
        print("PACKAGE_BLOCKED: personal or machine-specific data found:")
        print("\n".join("  " + p for p in problems[:50]))
        raise SystemExit(1)
    if args.check:
        print(f"PACKAGE_CHECK ok: {len(files)} files, version {meta['version']}")
        return
    out = Path(args.out_dir) / f"{meta['name']}-{meta['version']}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, relative in files:
            archive.write(path, Path(meta["name"]) / relative)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"PACKAGE_BUILT {out} ({len(files)} files, {out.stat().st_size / 1024:.0f} KB, sha256 {digest})")


if __name__ == "__main__":
    sys.exit(main())
