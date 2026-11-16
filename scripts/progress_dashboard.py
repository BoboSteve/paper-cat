#!/usr/bin/env python3
"""Maintain a small progress state and refreshable HTML dashboard for a task."""
import argparse
import datetime
import html
import json
from pathlib import Path


STAGES = ("alignment", "search", "merge", "jev", "pre_final", "fulltext", "final", "report")


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(value):
    return value.isoformat()


def render(state, summaries):
    stage = html.escape(str(state.get("stage") or "未开始"))
    status = html.escape(str(state.get("status") or "running"))
    completed = int(state.get("completed") or 0)
    total = int(state.get("total") or 0)
    percent = min(100, round(100 * completed / total, 1)) if total else 0
    eta = state.get("eta_seconds")
    eta_text = f"{round(eta / 60, 1)} 分钟" if isinstance(eta, (int, float)) else "—"
    errors = state.get("errors") or 0
    message = html.escape(str(state.get("message") or ""))
    updated = html.escape(str(state.get("updated_at") or ""))
    summary_items = "".join(
        f"<li><b>{html.escape(item.get('time', ''))}</b> "
        f"[{html.escape(item.get('stage', ''))}] {html.escape(item.get('message', ''))}</li>"
        for item in summaries[-20:][::-1]
    ) or "<li>尚无阶段总结</li>"
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="15">
<title>论文检索进度</title>
<style>
:root{{--bg:#f7f6f3;--fg:#202124;--card:#fff;--line:#ddd9d2;--acc:#0969da;--warn:#9a6700}}
@media(prefers-color-scheme:dark){{:root{{--bg:#17191c;--fg:#e8e6e1;--card:#202328;--line:#34383f;--acc:#70b7ff;--warn:#e3b341}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC",sans-serif}}
main{{max-width:900px;margin:0 auto;padding:32px 20px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin:16px 0}}
h1{{margin:.2em 0}} .big{{font-size:2.2em;font-weight:700}} .dim{{color:#777}}
.bar{{height:16px;background:var(--line);border-radius:8px;overflow:hidden}}.bar span{{display:block;height:100%;background:var(--acc);width:{percent}%}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.grid div{{background:var(--bg);padding:10px;border-radius:8px}}
@media(max-width:650px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main>
<h1>论文检索进度</h1><p class="dim">每 15 秒自动刷新 · {updated}</p>
<div class="card"><div class="big">{stage}</div><p>状态：{status}</p>
<p>{completed} / {total} · {percent}% · 预计剩余 {eta_text} · 错误 {errors}</p>
<div class="bar"><span></span></div><p>{message}</p></div>
<div class="card"><div class="grid">
<div><b>阶段</b><br>{stage}</div><div><b>已完成</b><br>{completed}</div>
<div><b>总数</b><br>{total}</div><div><b>错误</b><br>{errors}</div>
</div></div>
<div class="card"><h2>阶段总结</h2><ul>{summary_items}</ul></div>
</main></body></html>"""


def update(task, *, stage=None, status=None, completed=None, total=None, errors=None, message=None, summary=None):
    task = Path(task)
    task.mkdir(parents=True, exist_ok=True)
    state_path = task / "progress_state.json"
    summaries_path = task / "stage_summaries.jsonl"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    previous_stage = state.get("stage")
    current = now()
    updates = {
        "stage": stage,
        "status": status,
        "completed": completed,
        "total": total,
        "errors": errors,
        "message": message,
    }
    for key, value in updates.items():
        if value is not None:
            state[key] = value
    state.setdefault("started_at", iso(current))
    state.setdefault("stage_started_at", iso(current))
    if stage and stage != previous_stage:
        state["stage_started_at"] = iso(current)
    completed_value, total_value = int(state.get("completed") or 0), int(state.get("total") or 0)
    if completed_value < 0 or total_value < 0 or completed_value > total_value and total_value > 0:
        raise ValueError("progress requires 0 <= completed <= total")
    try:
        started = datetime.datetime.fromisoformat(state["stage_started_at"])
        elapsed = max(0, (current - started).total_seconds())
        state["eta_seconds"] = (
            round(elapsed * (total_value - completed_value) / completed_value)
            if completed_value and total_value > completed_value else 0
        )
    except Exception:
        state["eta_seconds"] = None
    state["percent"] = round(100 * completed_value / total_value, 1) if total_value else 0
    state["updated_at"] = iso(current)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summaries = []
    if summaries_path.exists():
        summaries = [json.loads(line) for line in summaries_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if summary:
        summaries.append({
            "time": current.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "stage": state.get("stage"),
            "completed": completed_value,
            "total": total_value,
            "message": summary,
        })
        with summaries_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(summaries[-1], ensure_ascii=False) + "\n")
    dashboard = task / "dashboard.html"
    dashboard.write_text(render(state, summaries), encoding="utf-8")
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--status", choices=("running", "waiting_user", "done", "failed", "degraded"))
    parser.add_argument("--completed", type=int)
    parser.add_argument("--total", type=int)
    parser.add_argument("--errors", type=int)
    parser.add_argument("--message")
    parser.add_argument("--summary", help="append a periodic stage summary")
    args = parser.parse_args()
    try:
        state = update(
            args.task,
            stage=args.stage,
            status=args.status,
            completed=args.completed,
            total=args.total,
            errors=args.errors,
            message=args.message,
            summary=args.summary,
        )
    except ValueError as error:
        parser.error(str(error))
    print(
        f"PROGRESS stage={state.get('stage')} "
        f"completed={state.get('completed')}/{state.get('total')} -> {Path(args.task) / 'dashboard.html'}"
    )


if __name__ == "__main__":
    main()
