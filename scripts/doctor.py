#!/usr/bin/env python3
"""Instant environment check: what works now, and what the user could unlock next.

    python scripts/doctor.py                 # human-readable, offline, < 1 s
    python scripts/doctor.py --json          # for agents
    python scripts/doctor.py --dismiss xiaohongshu   # stop suggesting an optional item
    python scripts/doctor.py --reset-tips

Quick mode needs nothing beyond Python and a network connection. Every other item is optional and
is offered once, with what it adds and how to enable it. Dismissed tips are remembered per machine.
"""
import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SOURCE_GUIDE, cache_dir, load_env, port_open, read_json, runtime_settings, write_json  # noqa: E402

STATE = "onboarding.json"
LOCAL_MODEL_TIP = {
    "id": "local_model", "name": "本地排序模型（可选）", "priority": "可选",
    "benefit": "候选很多（数百篇以上）时用本地小模型快速初排，深度模式更快",
    "without": "不影响使用：由当前 Agent 读摘要排序",
    "get": "按 references/runtime-setup.md 配置 runtime.local.json", "command": None,
}


def state_path():
    return cache_dir() / STATE


def load_state():
    return read_json(state_path(), {"dismissed": []}) or {"dismissed": []}


def xhs_ready():
    """已登录（有 cookie）且服务在跑才算可用；没装好不能阻塞主流程。"""
    try:
        import xhs_setup
    except ImportError:
        return False
    return port_open(xhs_setup.DEFAULT_PORT) and xhs_setup.cookie_populated(
        xhs_setup.runtime_tool_dir() / "cookies.json")


def check():
    settings = load_env()
    runtime = runtime_settings()
    sources = {}
    for guide in SOURCE_GUIDE:
        if guide["id"] == "xiaohongshu":
            ok = xhs_ready()
        else:
            ok = all(str(settings.get(key) or "").strip() for key in guide["keys"])
        sources[guide["id"]] = ok
    pdf_text = bool(importlib.util.find_spec("pymupdf") or importlib.util.find_spec("fitz") or shutil.which("pdftotext"))
    local_model = bool(runtime.get("service") and runtime.get("model_name"))
    tiers = {
        "quick": {"ready": sys.version_info >= (3, 9), "needs": "Python 3.9+ 与网络"},
        "deep": {"ready": pdf_text, "needs": "PDF 文本抽取（pip install pymupdf）"},
        "deep_multisource": {"ready": bool(shutil.which("uvx")), "needs": "uv（多源检索层 paper-search-mcp）"},
    }
    registry = cache_dir() / "venue_registry.json"
    return {"python": sys.version.split()[0], "tiers": tiers, "sources": sources,
            "pdf_text": pdf_text, "local_model": local_model, "state": load_state(),
            "grade_tables": registry.exists()}


def ladder(status=None, include_dismissed=False):
    """Optional upgrades not yet enabled (and not dismissed), most useful first."""
    status = status or check()
    dismissed = set(status["state"].get("dismissed") or [])
    items = [g for g in SOURCE_GUIDE if not status["sources"].get(g["id"])]
    if not status["local_model"]:
        items.append(LOCAL_MODEL_TIP)
    order = {"推荐": 0, "按需": 1, "可选": 2}
    items.sort(key=lambda g: order.get(g["priority"], 3))
    return [{"id": g["id"], "title": g["name"], "benefit": g["benefit"], "without": g["without"],
             "how": f"python scripts/{g['command']}" if g.get("command") else g["get"]}
            for g in items if include_dismissed or g["id"] not in dismissed]


def message(status):
    lines = ["环境检查"]
    quick = status["tiers"]["quick"]["ready"]
    lines.append(f"✓ 两分钟速览：{'可直接使用（无需任何账号或配置）' if quick else '需要 Python 3.9+'}")
    deep = status["tiers"]["deep"]
    lines.append(f"{'✓' if deep['ready'] else '·'} 深度检索（全文核实）：" + ("可用" if deep["ready"] else f"还需 {deep['needs']}"))
    lines.append("  等级名录（CCF / 中科院分区）：" + ("已下载" if status.get("grade_tables") else
                 "首次深度检索时自动下载（需访问 GitHub）；下载失败也能继续，只是不标等级"))
    enabled = [g["name"] for g in SOURCE_GUIDE if status["sources"].get(g["id"])]
    if enabled:
        lines.append("✓ 已启用：" + "、".join(enabled))
    tips = ladder(status)
    if tips:
        lines.append("可选增强（不配也能用）：")
        lines += [f"  · {t['title']}：{t['benefit']} → {t['how']}" for t in tips]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dismiss", metavar="ID", help="stop suggesting this optional item (e.g. xiaohongshu, semantic)")
    parser.add_argument("--reset-tips", action="store_true")
    args = parser.parse_args()
    known = {g["id"] for g in SOURCE_GUIDE} | {LOCAL_MODEL_TIP["id"]}
    if args.dismiss or args.reset_tips:
        state = load_state()
        if args.reset_tips:
            state["dismissed"] = []
        else:
            if args.dismiss not in known:
                parser.error(f"unknown item {args.dismiss!r}; choose from {sorted(known)}")
            state["dismissed"] = sorted(set(state.get("dismissed") or []) | {args.dismiss})
        write_json(state_path(), state)
    status = check()
    if args.json:
        print(json.dumps({**status, "ladder": ladder(status)}, ensure_ascii=False, indent=2))
    else:
        print(message(status))


if __name__ == "__main__":
    main()
