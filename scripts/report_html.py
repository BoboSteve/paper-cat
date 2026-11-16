"""One clean, self-contained HTML report for both quick and deep mode.

render(page) takes plain data and returns HTML. Everything user- or source-provided is escaped, and
only http(s) links are emitted, so paper metadata or notes cannot inject markup or scripts.

page = {
  "mode": "quick" | "deep", "title": str, "subtitle": str, "funnel": [(label, number)],
  "notice": str | None,                      # honest scope statement, e.g. "基于摘要的速览"
  "highlights": [str],                       # 3-5 take-aways written by the agent
  "papers": [paper], "secondary": [paper],   # recommended / peripheral
  "upgrade": [{"title", "benefit", "how"}],  # optional next steps not yet enabled
  "details": [{"title", "items": [str]} | {"title", "table": {"headers": [...], "rows": [[...]]}, "searchable": bool}],
  "footer": str,
}
paper = {"title", "url", "pdf", "meta", "badges": [(text, kind)], "roles": [code], "cluster",
         "summary", "why", "evidence": {"quote", "page", "verified"} | None,
         "community": {"posts", "line"} | None}
"""
from __future__ import annotations

import html
import json
import re

ROLE_LABELS = {"direct_competitor": "直接相关", "method_donor": "可借鉴方法",
               "failure_warning": "失败与风险", "data_eval_support": "数据与评测"}

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1f23;--sub:#5d6470;--line:#e6e6e2;--accent:#2f6fde;--accent-soft:#e8f0fd;
--ok:#1f8a4c;--warn:#b26a00;--chip:#f0f0ec;--shadow:0 1px 2px rgba(0,0,0,.04),0 4px 16px rgba(0,0,0,.04)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141518;--card:#1c1e22;--ink:#e8e8e6;--sub:#a0a6b0;
--line:#2c2f35;--accent:#7aa7ff;--accent-soft:#1e2a40;--ok:#5cc98a;--warn:#e0a341;--chip:#25282d;--shadow:none}}
:root[data-theme=dark]{--bg:#141518;--card:#1c1e22;--ink:#e8e8e6;--sub:#a0a6b0;--line:#2c2f35;--accent:#7aa7ff;
--accent-soft:#1e2a40;--ok:#5cc98a;--warn:#e0a341;--chip:#25282d;--shadow:none}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
"Hiragino Sans GB","Microsoft YaHei",sans-serif}
main{max-width:860px;margin:0 auto;padding:28px 16px 64px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{margin-bottom:20px}.pill{display:inline-block;font-size:12px;padding:2px 10px;border-radius:99px;
background:var(--accent-soft);color:var(--accent);font-weight:600;letter-spacing:.02em}
h1{font-size:26px;line-height:1.3;margin:10px 0 4px}.sub{color:var(--sub);font-size:13px}
.funnel{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:14px;font-size:13px;color:var(--sub)}
.funnel b{color:var(--ink);font-size:18px;margin-right:4px}
.notice{margin:16px 0;padding:10px 14px;border-radius:10px;background:var(--accent-soft);font-size:13px}
section{margin-top:28px}h2{font-size:16px;margin:0 0 12px}
.hl{margin:0;padding-left:20px}.hl li{margin:4px 0}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.chip{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:99px;padding:3px 12px;
font-size:13px;cursor:pointer}.chip[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:12px;box-shadow:var(--shadow)}
.card h3{font-size:16px;line-height:1.4;margin:0 0 4px}.meta{color:var(--sub);font-size:12.5px}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.tag{font-size:12px;padding:1px 8px;border-radius:6px;background:var(--chip);color:var(--sub)}
.tag.grade{background:var(--accent-soft);color:var(--accent);font-weight:600}.tag.warn{color:var(--warn)}
.sum{margin:6px 0 2px}.why{color:var(--sub);font-size:13.5px;margin:2px 0}
details.ev{margin-top:8px;font-size:13px}details.ev summary{cursor:pointer;color:var(--sub)}
blockquote{margin:8px 0 0;padding:6px 12px;border-left:3px solid var(--line);color:var(--ink);font-style:italic}
.ok{color:var(--ok)}.links{margin-top:8px;font-size:13px;display:flex;gap:14px}
.community{margin-top:6px;font-size:12.5px;color:var(--sub)}
.compact{list-style:none;padding:0;margin:0}.compact li{padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}
.compact .meta{display:block}
.up{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.up div{background:var(--card);border:1px dashed var(--line);border-radius:12px;padding:12px 14px;font-size:13px}
.up b{display:block;font-size:14px;margin-bottom:2px}.up code{font-size:12px}
details.more{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:8px}
details.more>summary{cursor:pointer;padding:10px 14px;font-size:14px}details.more>div{padding:0 14px 12px;font-size:13px}
.tbl{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
input[type=search]{width:100%;padding:6px 10px;margin:4px 0 8px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--ink)}
footer{margin-top:36px;color:var(--sub);font-size:12px;text-align:center}
.theme{position:fixed;top:12px;right:12px;border:1px solid var(--line);background:var(--card);color:var(--sub);
border-radius:99px;padding:3px 10px;font-size:12px;cursor:pointer}
@media print{.theme,.chips,input[type=search]{display:none}details{display:block}}
"""

JS = """
(function(){var r=document.documentElement;try{var t=localStorage.getItem('cps-theme');if(t)r.dataset.theme=t}catch(e){}
document.querySelector('.theme').onclick=function(){var d=r.dataset.theme||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
r.dataset.theme=d==='dark'?'light':'dark';try{localStorage.setItem('cps-theme',r.dataset.theme)}catch(e){}};
document.querySelectorAll('.chip').forEach(function(c){c.onclick=function(){var k=c.dataset.k;
document.querySelectorAll('.chip').forEach(function(x){x.setAttribute('aria-pressed',x===c)});
document.querySelectorAll('.card[data-c]').forEach(function(p){p.hidden=!(k==='*'||p.dataset.c===k)})}});
document.querySelectorAll('input[data-filter]').forEach(function(i){i.oninput=function(){var q=i.value.toLowerCase();
document.querySelectorAll('#'+i.dataset.filter+' tbody tr').forEach(function(tr){tr.hidden=q&&tr.textContent.toLowerCase().indexOf(q)<0})}})})();
"""


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def safe_url(value):
    """http(s) links, or plain relative paths such as pdfs/3_arxiv.pdf; any other scheme is dropped."""
    value = str(value or "").strip()
    if re.match(r"^https?://", value, re.I):
        return value
    if re.fullmatch(r"(?!//)[\w.\-/%]+", value) and ":" not in value:
        return value
    return ""


def _link(url, text):
    url = safe_url(url)
    return f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(text)}</a>' if url else esc(text)


def _paper_card(paper):
    tags = "".join(f'<span class="tag {esc(kind)}">{esc(text)}</span>' for text, kind in paper.get("badges") or [])
    tags += "".join(f'<span class="tag">{esc(ROLE_LABELS.get(role, role))}</span>' for role in paper.get("roles") or [])
    evidence = paper.get("evidence")
    ev_html = ""
    if evidence and evidence.get("quote"):
        mark = (f'<span class="ok">✓ 已在原文第 {esc(evidence.get("page"))} 页核对</span>' if evidence.get("verified")
                else '<span class="tag warn">引句未核对</span>')
        ev_html = (f'<details class="ev"><summary>原文证据 · {mark}</summary>'
                   f'<blockquote>{esc(evidence["quote"])}</blockquote></details>')
    community = paper.get("community")
    co_html = (f'<div class="community">💬 小红书 {esc(community.get("posts"))} 篇讨论 · {esc(community.get("line"))}'
               '（社区反馈，仅供参考）</div>') if community else ""
    links = " ".join(_link(raw, label) for raw, label in ((paper.get("url"), "原文页"), (paper.get("pdf"), "PDF"))
                     if safe_url(raw))
    pieces = [
        f'<h3>{_link(paper.get("url"), paper.get("title"))}</h3>',
        f'<div class="meta">{esc(paper.get("meta"))}</div>',
        f'<div class="tags">{tags}</div>' if tags else "",
        f'<p class="sum">{esc(paper["summary"])}</p>' if paper.get("summary") else "",
        f'<p class="why">{esc(paper["why"])}</p>' if paper.get("why") else "",
        ev_html,
        co_html,
        f'<div class="links">{links}</div>' if links else "",
    ]
    return f'<article class="card" data-c="{esc(paper.get("cluster") or "")}">{"".join(pieces)}</article>'


def _details(block, number):
    title = esc(block.get("title"))
    if "table" in block:
        table = block["table"]
        tid = f"t{number}"
        head = "".join(f"<th>{esc(h)}</th>" for h in table.get("headers") or [])
        rows = "".join("<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>" for row in table.get("rows") or [])
        search = (f'<input type="search" placeholder="搜索标题、来源或原因" data-filter="{tid}">'
                  if block.get("searchable") else "")
        body = f'{search}<div class="tbl"><table id="{tid}"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
    else:
        body = "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in block.get("items") or []) + "</ul>"
    return f'<details class="more"><summary>{title}</summary><div>{body}</div></details>'


def render(page):
    papers = page.get("papers") or []
    clusters = []
    for paper in papers:
        if paper.get("cluster") and paper["cluster"] not in clusters:
            clusters.append(paper["cluster"])
    chips = ""
    if len(clusters) > 1:
        chips = ('<div class="chips"><button class="chip" data-k="*" aria-pressed="true">全部 '
                 f'{len(papers)}</button>' + "".join(
                     f'<button class="chip" data-k="{esc(c)}" aria-pressed="false">{esc(c)} '
                     f'{sum(p.get("cluster") == c for p in papers)}</button>' for c in clusters) + "</div>")
    funnel = "".join(f"<span><b>{esc(n)}</b>{esc(label)}</span>" for label, n in page.get("funnel") or [])
    highlights = page.get("highlights") or []
    secondary = page.get("secondary") or []
    upgrade = page.get("upgrade") or []
    mode_label = {"quick": "速览 · 2 分钟", "deep": "深度检索 · 全文核实"}.get(page.get("mode"), "论文检索")
    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{esc(page.get('title'))} · 论文速递</title><style>{CSS}</style></head><body>",
        '<button class="theme" type="button" aria-label="切换明暗">明 / 暗</button><main>',
        f'<header><span class="pill">{esc(mode_label)}</span><h1>{esc(page.get("title"))}</h1>',
        f'<div class="sub">{esc(page.get("subtitle"))}</div>',
        f'{f"<div class=funnel>{funnel}</div>" if funnel else ""}</header>',
        f'<div class="notice">{esc(page["notice"])}</div>' if page.get("notice") else "",
    ]
    if highlights:
        parts.append('<section><h2>要点</h2><ul class="hl">' + "".join(f"<li>{esc(h)}</li>" for h in highlights) + "</ul></section>")
    parts.append(f'<section><h2>推荐阅读（{len(papers)}）</h2>{chips}{"".join(_paper_card(p) for p in papers)}'
                 f'{"" if papers else "<p class=sub>这次没有论文达到推荐标准——这本身也是结论，可以放宽时间或关键词再试。</p>"}</section>')
    if secondary:
        items = "".join(f'<li>{_link(p.get("url"), p.get("title"))}<span class="meta">{esc(p.get("meta"))}'
                        f'{" · " + esc(p.get("why")) if p.get("why") else ""}</span></li>' for p in secondary)
        parts.append(f'<section><h2>也值得一看（{len(secondary)}）</h2><ul class="compact">{items}</ul></section>')
    if upgrade:
        cells = "".join(f'<div><b>{esc(u["title"])}</b>{esc(u["benefit"])}<br><code>{esc(u["how"])}</code></div>' for u in upgrade)
        parts.append(f'<section><h2>想要更全、更可靠的结果</h2><div class="up">{cells}</div></section>')
    if page.get("details"):
        parts.append("<section><h2>检索与可复现细节</h2>" +
                     "".join(_details(block, n) for n, block in enumerate(page["details"])) + "</section>")
    parts += [f'<footer>{esc(page.get("footer") or "论文喵 PaperCat")}</footer></main>',
              f"<script>{JS}</script></body></html>"]
    return "".join(parts)


def write(page, path):
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as stream:  # Path.write_text(newline=) is 3.10+
        stream.write(render(page))
    Path(path).with_name("report_data.json").write_text(json.dumps(page, ensure_ascii=False, indent=1), encoding="utf-8")
