#!/usr/bin/env python3
"""Install (or update) this skill for one or more agent harnesses.

    python scripts/install_skill.py --target claude          # ~/.claude/skills/paper-cat
    python scripts/install_skill.py --target codex           # ~/.codex/skills/paper-cat
    python scripts/install_skill.py --target claude --project /path/to/repo   # <repo>/.claude/skills/
    python scripts/install_skill.py --target /any/skills/dir  # other harnesses that read SKILL.md folders

The copy excludes caches and, unless --with-runtime is given, the machine-specific
runtime.local.json (each machine creates its own from runtime.example.*.json).
"""
import argparse
import shutil
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import utf8_output  # noqa: E402  (GBK pipes on Chinese Windows)

utf8_output()


def skill_name():
    """The name in SKILL.md, not the folder name (a GitHub download unpacks as 'paper-cat-main')."""
    for line in (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").splitlines()[1:]:
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"') or "paper-cat"
        if line.strip() == "---":
            break
    return "paper-cat"


NAME = skill_name()
HARNESS_DIRS = {"claude": Path(".claude") / "skills", "codex": Path(".codex") / "skills"}
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.tmp", ".DS_Store", "runtime.local.json")


def destination(target, project):
    if target in HARNESS_DIRS:
        base = Path(project).expanduser().resolve() if project else Path.home()
        return base / HARNESS_DIRS[target] / NAME
    return Path(target).expanduser().resolve() / NAME


def install(target, project=None, with_runtime=False, dry_run=False):
    dest = destination(target, project)
    if dest.resolve() == SKILL_DIR.resolve():
        raise SystemExit(f"refusing to install onto the source folder itself: {dest}")
    if dry_run:
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SKILL_DIR, dest, ignore=IGNORE)
    runtime = SKILL_DIR / "runtime.local.json"
    if with_runtime and runtime.exists():
        shutil.copy2(runtime, dest / "runtime.local.json")
    return dest


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", action="append", required=True,
                        help="claude | codex | a skills directory path (repeatable)")
    parser.add_argument("--project", help="install into <project>/.claude/skills or <project>/.codex/skills")
    parser.add_argument("--with-runtime", action="store_true",
                        help="also copy runtime.local.json (only when installing on this same machine)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for target in args.target:
        dest = install(target, args.project, args.with_runtime, args.dry_run)
        print(f"{'would install' if args.dry_run else 'installed'} -> {dest}")
    if not args.dry_run:
        print("Ready: no configuration needed. Run `python scripts/doctor.py` in the installed folder to see "
              "optional upgrades (a local ranking model uses runtime.local.json; see references/runtime-setup.md).")


if __name__ == "__main__":
    main()
