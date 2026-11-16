#!/usr/bin/env python3
"""Opt-in Xiaohongshu MCP bootstrap: verified download, optional Defender exclusion, QR login, health check.

    python scripts/xhs_setup.py --check       # what is installed / running / logged in
    python scripts/xhs_setup.py --install     # download the two official binaries
    python scripts/xhs_setup.py --setup       # install + Defender check + start + browser login
    python scripts/xhs_setup.py --login       # open the login browser and wait for the scan
    python scripts/xhs_setup.py --start       # start the service with saved cookies
    python scripts/xhs_setup.py --stop
    python scripts/xhs_setup.py --remove-exclusions   # undo Defender exclusions this tool added

Windows: if Defender blocks the leakless.exe helper, the USER may opt in with --allow-defender-exclusion
(one UAC prompt; only that single file is excluded; undo with --remove-exclusions). macOS/Linux have no Defender
problem, so the same commands just download and start the service.

Everything is optional: the literature pipeline never calls this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import port_open, read_json, runtime_settings, write_json  # noqa: E402

RELEASE = "v2.7.4"
RELEASE_BASE = f"https://github.com/xpzouying/xiaohongshu-mcp/releases/download/{RELEASE}"
# SHA-256 published by GitHub for the pinned release; a download or cached file must match exactly.
SHA256 = {
    "xiaohongshu-login-darwin-arm64": "3b4dcbe50d4055585e4ccab0d54ff59650213284375f6c3efdf6bcebbbaac61c",
    "xiaohongshu-login-linux-amd64": "2bf99cb365a968015cd096f825c4f838c4cc47a4786252cd9eca145d42b074ff",
    "xiaohongshu-login-windows-amd64.exe": "4ea35659e6de5be354921dee0fc09bb4d10d7e107a64abae050a95b1a8189021",
    "xiaohongshu-mcp-darwin-arm64": "8e0194952251f6da2e2911bbc30934abec760b8eaa9ee811b0d0358656435673",
    "xiaohongshu-mcp-linux-amd64": "7360e4f1c617acd0d64df807afe64b0c46ca95492ab768c337483cc367f96407",
    "xiaohongshu-mcp-windows-amd64.exe": "19e8b13418bb6e618951fcc0e8296abaab69688fbdc0a1320b68491d2292885e",
}
STATE = "xhs_setup.json"
DEFAULT_PORT = 18060
HOST = "127.0.0.1"  # never listen on the LAN: the service can act on the logged-in account
LEAKLESS_FILE = "leakless.exe"
BOM = chr(0xFEFF)  # PowerShell 5.1 reads BOM-less UTF-8 scripts as the ANSI code page


# --------------------------------------------------------------------------- paths

def default_tool_dir():
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    new, old = base / "paper-cat" / "xiaohongshu-mcp", base / "paper-cat" / "xiaohongshu-mcp"
    return old if old.exists() and not new.exists() else new  # keep a login made before the rename


def runtime_tool_dir():
    """--tool-dir > runtime.local.json 的 xiaohongshu_tool_dir > 每用户默认目录。"""
    value = runtime_settings().get("xiaohongshu_tool_dir")
    return Path(value).expanduser() if value else default_tool_dir()


def asset_names():
    if sys.platform == "win32":
        return {"mcp": "xiaohongshu-mcp-windows-amd64.exe", "login": "xiaohongshu-login-windows-amd64.exe"}
    if sys.platform == "darwin":
        return {"mcp": "xiaohongshu-mcp-darwin-arm64", "login": "xiaohongshu-login-darwin-arm64"}
    return {"mcp": "xiaohongshu-mcp-linux-amd64", "login": "xiaohongshu-login-linux-amd64"}


def exe_path(tool, kind):
    return Path(tool) / asset_names()[kind]


def state_path(tool):
    return Path(tool) / STATE


def load_state(tool):
    return read_json(state_path(tool), {}) or {}


def save_state(tool, value):
    write_json(state_path(tool), value)


# --------------------------------------------------------------------------- download

def sha256_of(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, target, expected, timeout=600):
    """Download to a .part file, verify the pinned SHA-256, then move into place."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256_of(target) == expected:
            return f"已存在且校验通过：{target.name}"
        target.unlink()  # corrupted or tampered copy: never run it
    temporary = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "paper-cat/xhs-setup"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as stream:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                stream.write(chunk)
        actual = sha256_of(temporary)
        if actual != expected:
            raise SystemExit(f"校验失败，已删除下载文件：{target.name}\n  期望 {expected}\n  实际 {actual}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    if sys.platform != "win32":
        target.chmod(0o755)
    return f"已下载并校验 {target.name}（{target.stat().st_size / 1e6:.1f} MB）"


def verify_installed(tool):
    """Executables must match the pinned hashes before they are ever started."""
    for name in asset_names().values():
        path = Path(tool) / name
        if not path.exists():
            raise SystemExit(f"找不到 {path}，先运行 --install")
        if sha256_of(path) != SHA256[name]:
            raise SystemExit(f"{path.name} 与官方发布版本的校验值不一致，拒绝运行；请运行 --install --force 重新下载")


def install(tool, force=False):
    names = asset_names()
    tool.mkdir(parents=True, exist_ok=True)
    lines = []
    for kind, name in names.items():
        target = tool / name
        if force:
            target.unlink(missing_ok=True)
        lines.append(download(f"{RELEASE_BASE}/{name}", target, SHA256[name]))
    return lines


# --------------------------------------------------------------------------- leakless exclusion

def leakless_dirs():
    temp = Path(tempfile.gettempdir())
    return sorted(p for p in temp.glob("leakless-*") if p.is_dir())


def leakless_files():
    """The exact helper files rod extracts; only these are ever proposed as exclusions."""
    return [d / LEAKLESS_FILE for d in leakless_dirs()]


def trigger_leakless(tool, port):
    """Windows only: start the service once so rod extracts leakless.exe (Defender may remove it)."""
    if sys.platform == "win32" and not leakless_dirs():
        start_service(tool, port)
        if wait_port(port, timeout=20):
            try:
                call_tool(port, "check_login_status")
            except (urllib.error.URLError, RuntimeError, OSError):
                pass  # the helper is extracted before the browser starts; a failed call is expected here
        stop_service(tool, port)
    return leakless_files()


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_elevated(tool, lines, name):
    """Run a short PowerShell script elevated (one UAC prompt the user must approve); return (ok, message)."""
    result = Path(tool) / f"{name}_result.txt"
    result.unlink(missing_ok=True)
    script = Path(tool) / f"{name}.ps1"
    body = ["$ErrorActionPreference='Stop'", f"$out={ps_quote(result)}", "try {"]
    body += [f"  {line}" for line in lines]
    body += ["  'OK' | Out-File -Encoding utf8 $out", "} catch {",
             "  ('FAIL: ' + $_.Exception.Message) | Out-File -Encoding utf8 $out", "}"]
    script.write_text(BOM + "\n".join(body) + "\n", encoding="utf-8")
    command = (f"Start-Process powershell -Verb RunAs -Wait -ArgumentList "
               f"'-NoProfile','-ExecutionPolicy','Bypass','-File',{ps_quote(script)}")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", command], check=False, timeout=180,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "UAC 授权超时（没有点“是”），未做任何修改"
    if not result.exists():
        return False, "UAC 被取消，未做任何修改"
    text = result.read_text(encoding="utf-8-sig", errors="replace").strip()
    return (True, "OK") if text == "OK" else (False, text[6:].strip() or text)


def add_exclusions(files, tool):
    """Exclude only the exact leakless.exe files from Defender scanning (never a folder or a process name)."""
    if sys.platform != "win32":
        return "非 Windows，无需排除项"
    state = load_state(tool)
    done = set(state.get("excluded_files") or [])
    wanted = [str(p) for p in files if str(p) not in done]
    if not wanted:
        return f"排除项已存在（{len(done)} 个文件），无需再次授权"
    ok, message = run_elevated(tool, [f"Add-MpPreference -ExclusionPath {ps_quote(p)}" for p in wanted], "add_exclusions")
    if not ok:
        return f"未添加排除项：{message}"
    state["excluded_files"] = sorted(done | set(wanted))
    save_state(tool, state)
    return "已添加 Defender 排除项（仅以下文件）：" + "；".join(wanted) + "\n可随时运行 --remove-exclusions 撤销"


def remove_exclusions(tool):
    """Undo every exclusion this tool ever recorded, including the broad ones from earlier versions."""
    if sys.platform != "win32":
        return "非 Windows，无需处理"
    state = load_state(tool)
    paths = sorted(set(state.get("excluded_files") or []) |
                   {p for p in state.get("excluded") or [] if p != LEAKLESS_FILE})
    lines = [f"Remove-MpPreference -ExclusionPath {ps_quote(p)}" for p in paths]
    if LEAKLESS_FILE in (state.get("excluded") or []):
        lines.append(f"Remove-MpPreference -ExclusionProcess {ps_quote(LEAKLESS_FILE)}")
    if not lines:
        return "本工具没有记录过任何排除项"
    ok, message = run_elevated(tool, lines, "remove_exclusions")
    if not ok:
        return f"未撤销：{message}"
    state.pop("excluded_files", None)
    state.pop("excluded", None)
    save_state(tool, state)
    return f"已撤销 {len(lines)} 项 Defender 排除"


# --------------------------------------------------------------------------- service

def service_env(tool):
    env = dict(os.environ)
    env["COOKIES_PATH"] = str(Path(tool) / "cookies.json")
    return env


def process_image(pid):
    """Executable name of a running PID, or '' if it is not running."""
    try:
        if sys.platform == "win32":
            # tasklist prints in the console code page (e.g. GBK); read bytes, the image name itself is ASCII.
            raw = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, timeout=15).stdout or b""
            out = raw.decode("ascii", errors="replace").strip()
            return out.split('","')[0].strip('"') if out.startswith('"') else ""
        raw = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, timeout=15).stdout or b""
        return Path(raw.decode("utf-8", errors="replace").strip()).name
    except (subprocess.SubprocessError, OSError):
        return ""


def owned_pid(tool):
    """The recorded service PID, only if that process is still our MCP executable."""
    pid = load_state(tool).get("pid")
    if not isinstance(pid, int):
        return None
    return pid if process_image(pid).lower() == asset_names()["mcp"].lower() else None


def start_service(tool, port):
    """Start the MCP service on 127.0.0.1:<port>; reuse it if our own instance is already running."""
    pid = owned_pid(tool)
    if pid and port_open(port):
        return pid
    if port_open(port):
        raise SystemExit(f"端口 {port} 已被其他程序占用，未启动服务（可用 --port 换一个端口）")
    verify_installed(tool)
    exe = exe_path(tool, "mcp")
    address = f"{HOST}:{port}"
    log = Path(tool) / "mcp.log"
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        kwargs = {"creationflags": flags}
    else:
        kwargs = {"start_new_session": True}
    with log.open("ab") as stream:
        process = subprocess.Popen([str(exe), "-port", address, "-headless=true"], cwd=str(tool),
                                   env=service_env(tool), stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                   **kwargs)
    state = load_state(tool)
    state.update({"pid": process.pid, "port": port})
    save_state(tool, state)
    return process.pid


def stop_service(tool, port):
    """Stop only the process this tool started (never whatever else happens to use the port)."""
    pid = owned_pid(tool)
    if pid:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20)
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
    state = load_state(tool)
    state.pop("pid", None)
    save_state(tool, state)
    for _ in range(20):
        if not port_open(port):
            break
        time.sleep(0.25)
    if pid:
        return f"已停止服务（PID {pid}）"
    return "服务未在运行" if not port_open(port) else f"端口 {port} 被其他程序占用，本工具不会去结束它"


def wait_port(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return True
        time.sleep(0.6)
    return False


# --------------------------------------------------------------------------- MCP client

class Mcp:
    def __init__(self, port):
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.session = None

    def _post(self, payload):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=120) as response:
            self.session = response.headers.get("Mcp-Session-Id") or self.session
            raw = response.read().decode("utf-8", errors="replace")
        if "text/event-stream" in str(response.headers.get("Content-Type")):
            events = [json.loads(line[5:].strip()) for line in raw.splitlines() if line.startswith("data:")]
            return events[-1] if events else None
        return json.loads(raw) if raw.strip() else None

    def connect(self):
        self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "paper-cat", "version": "1.0"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        return self

    def call(self, name, arguments=None):
        result = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": name, "arguments": arguments or {}}})
        if not result or "result" not in result:
            raise RuntimeError(f"{name} 调用失败：{json.dumps(result, ensure_ascii=False)[:300]}")
        return result["result"]


def call_tool(port, name, arguments=None):
    return Mcp(port).connect().call(name, arguments)


def tool_text(result):
    blocks = result.get("content") or []
    return "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text").strip()


def login_status(port):
    try:
        result = call_tool(port, "check_login_status")
    except (urllib.error.URLError, RuntimeError, OSError) as error:
        return False, f"服务不可用：{error}"
    text = tool_text(result)
    lowered = text.lower()
    if result.get("isError") or "未登录" in text or "not logged" in lowered or "内部错误" in text or "失败" in text:
        return False, text
    return True, text


# --------------------------------------------------------------------------- browser login

def cookie_populated(path):
    try:
        data = read_json(path, {}) or {}
    except (ValueError, OSError):
        return False
    cookies = data.get("cookies") if isinstance(data, dict) else data
    return bool(cookies)


def browser_login(tool, port, timeout=300, poll=3.0):
    """Open the visible login browser and wait for a freshly written cookie file.

    The previous cookie file is set aside first, so an old session can never be mistaken for a new
    scan; if the scan does not complete it is restored, so a failed attempt never logs the user out.
    """
    verify_installed(tool)
    cookie = Path(tool) / "cookies.json"
    backup = cookie.with_name("cookies.previous.json")
    stop_service(tool, port)  # the service and the login browser must not share the profile at once
    if cookie.exists():
        os.replace(cookie, backup)
    state = load_state(tool)
    state.pop("logged_in_at", None)
    save_state(tool, state)
    with (Path(tool) / "login.log").open("ab") as log:
        process = subprocess.Popen([str(exe_path(tool, "login"))], cwd=str(tool), env=service_env(tool),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        emit(f"已打开登录浏览器，请用小红书 App 扫码（最多等 {timeout // 60} 分钟）")
        deadline = time.time() + timeout
        while time.time() < deadline and not cookie_populated(cookie) and process.poll() is None:
            time.sleep(poll)
        if not cookie_populated(cookie) and process.poll() is None:
            process.terminate()  # timed out: close the login browser instead of leaving it open
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    if not cookie_populated(cookie):
        if backup.exists():
            os.replace(backup, cookie)
            return False, "未完成扫码，已恢复原来的登录状态（可重新运行 --login）"
        return False, "未完成扫码（没有拿到新的登录信息，可重新运行 --login）"
    backup.unlink(missing_ok=True)
    if sys.platform != "win32":
        cookie.chmod(0o600)  # session cookies: readable by this user only
    state = load_state(tool)
    state["logged_in_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_state(tool, state)
    return True, "扫码成功，登录信息已保存"


# --------------------------------------------------------------------------- commands

def report(tool, port):
    lines = [f"安装目录：{tool}"]
    for kind in ("mcp", "login"):
        path = exe_path(tool, kind)
        if path.exists():
            ok = sha256_of(path) == SHA256[path.name]
            lines.append(f"{'✓' if ok else '✗'} {path.name}（{path.stat().st_size / 1e6:.1f} MB，"
                         f"{'校验通过' if ok else '校验不一致，请 --install --force'}）")
        else:
            lines.append(f"· {path.name} 未安装")
    running = port_open(port)
    lines.append(f"{'✓' if running else '·'} MCP 服务 {HOST}:{port}" + ("（运行中）" if running else "（未运行）"))
    if running:
        ok, text = login_status(port)
        lines.append(f"{'✓' if ok else '·'} 登录状态：{text.splitlines()[0] if text else '未知'}")
    if sys.platform == "win32":
        state = load_state(tool)
        files = leakless_files()
        if not files:
            lines.append("leakless：未生成（首次启动服务后出现）")
        for path in files:
            present = path.exists()
            lines.append(f"leakless：{path}（{'存在' if present else '已被 Defender 移除'}"
                         f"{'，已排除' if str(path) in (state.get('excluded_files') or []) else ''}）")
        if state.get("excluded"):
            lines.append("⚠ 检测到旧版本添加的较宽排除项（整个目录 + 进程名 leakless.exe），"
                         "建议运行 --remove-exclusions 撤销，需要时再用新版只排除单个文件")
    return "\n".join(lines)


# --------------------------------------------------------------------------- read-only search for agents

READ_ONLY_PAUSE = 2.5  # seconds between calls: low frequency, like a person browsing


def require_ready(port):
    if not port_open(port):
        raise SystemExit("小红书服务未运行：先运行 --start（未登录或登录过期则 --login）")
    ok, text = login_status(port)
    if not ok:
        raise SystemExit("小红书未登录或登录已过期：运行 --login 扫码（" + (text.splitlines()[0] if text else "") + "）")


def search_payload(port, keyword, details=0, must_mention=None):
    """search_feeds (+ get_feed_detail for the first `details` matching notes); read-only tools only."""
    result = json.loads(tool_text(call_tool(port, "search_feeds", {"keyword": keyword})) or "{}")
    payload = {"keyword": keyword, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "search": result, "details": []}
    for feed in (result.get("feeds") or [])[:20]:
        if len(payload["details"]) >= details:
            break
        title = str((feed.get("noteCard") or {}).get("displayTitle") or "")
        if must_mention and must_mention.lower() not in title.lower():
            continue
        time.sleep(READ_ONLY_PAUSE)
        try:
            detail = call_tool(port, "get_feed_detail", {"feed_id": feed["id"], "xsec_token": feed.get("xsecToken", ""),
                                                         "load_all_comments": False, "limit": 20})
            payload["details"].append(json.loads(tool_text(detail) or "{}"))
        except (urllib.error.URLError, RuntimeError, OSError, ValueError) as error:
            payload["details"].append({"error": f"{feed.get('id')}: {type(error).__name__}"})
    return payload


def search_command(port, keyword, out, details):
    require_ready(port)
    payload = search_payload(port, keyword, details)
    target = Path(out)
    write_json(target, payload)
    feeds = len((payload["search"] or {}).get("feeds") or [])
    return f"已保存 {feeds} 条搜索结果、{len(payload['details'])} 篇详情 -> {target}"


def lookup_command(task, port, max_papers, details):
    """Reverse lookup for the papers read in a deep-search task, then attach community signals."""
    require_ready(port)
    import clue_import
    task = Path(task).expanduser().resolve()
    plan = clue_import.suggest(task)[:max_papers]
    for number, item in enumerate(plan, 1):
        payload = search_payload(port, item["keyword"], details, must_mention=item["keyword"])
        write_json(task / "clues" / f"lookup_{item['position']}.json", payload)
        emit(f"[{number}/{len(plan)}] {item['keyword']}：{len((payload['search'] or {}).get('feeds') or [])} 条结果")
        time.sleep(READ_ONLY_PAUSE)
    clue_import.attach(task)
    return f"口碑反查完成：{len(plan)} 篇论文；重新运行 report.py 后报告卡片会显示社区口碑"


def emit(message):
    print(message, flush=True)


EXCLUSION_HINT = ("Windows Defender 拦截了浏览器自动化库的辅助程序 leakless.exe（常见误报），服务无法打开浏览器。\n"
                  "需要的话，由你本人运行下面这条命令，只把这一个文件加入排除（会弹出 UAC，点“是”；可随时撤销）：\n"
                  "  python scripts/xhs_setup.py --setup --allow-defender-exclusion\n"
                  "不想改安全设置也可以：把小红书帖子文字直接发给 Agent，或用 Docker 版（见 references/xiaohongshu-clues.md）。")


def setup(tool, port, allow_exclusion=False):
    emit("== 1/4 准备程序（官方版本 + 校验）==")
    for line in install(tool):
        emit(line)
    if sys.platform == "win32":
        emit("== 2/4 检查 Defender 是否拦截 ==")
        files = trigger_leakless(tool, port)
        blocked = [p for p in files if not p.exists()]
        if not files:
            emit("未能定位 leakless 辅助程序；如果后面打不开浏览器，再看本命令的提示")
        elif not blocked:
            emit("未被拦截，无需任何安全设置")
        elif not allow_exclusion:
            return EXCLUSION_HINT
        else:
            emit("即将弹出 UAC 窗口：只把以下文件加入 Defender 排除，点“是”同意、点“否”取消")
            for path in blocked:
                emit(f"  {path}")
            emit(add_exclusions(blocked, tool))
    emit("== 3/4 启动服务（仅本机 127.0.0.1）==")
    start_service(tool, port)
    if not wait_port(port):
        raise SystemExit("服务未能在 30 秒内监听端口，日志见 " + str(Path(tool) / "mcp.log"))
    emit(f"服务已就绪：http://{HOST}:{port}/mcp")
    emit("== 4/4 扫码登录 ==")
    ok, text = login_status(port)
    if ok:
        return "已是登录状态，无需扫码：" + text.splitlines()[0]
    return login(tool, port)


def logout(tool, port):
    """Stop the service and delete the saved session (cookie) from this machine."""
    stopped = stop_service(tool, port)
    removed = [name for name in ("cookies.json", "cookies.previous.json") if (Path(tool) / name).exists()]
    for name in removed:
        (Path(tool) / name).unlink()
    state = load_state(tool)
    state.pop("logged_in_at", None)
    save_state(tool, state)
    return f"{stopped}；已删除登录信息：{', '.join(removed) or '无'}"


def login(tool, port):
    if port_open(port):
        ok, text = login_status(port)
        if ok:
            return "已是登录状态：" + text.splitlines()[0]
    ok, text = browser_login(tool, port)
    if not ok:
        return "登录失败：" + text
    start_service(tool, port)
    if not wait_port(port):
        return "登录已完成，但服务未能启动，日志见 " + str(Path(tool) / "mcp.log")
    ok, text = login_status(port)
    return ("登录成功：" if ok else "登录后状态校验失败：") + (text.splitlines()[0] if text else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="show install / service / login state")
    action.add_argument("--install", action="store_true", help="download and verify the official binaries")
    action.add_argument("--setup", action="store_true", help="install, check Defender, start, QR login")
    action.add_argument("--login", action="store_true", help="open the login browser and wait for the scan")
    action.add_argument("--start", action="store_true", help="start the local MCP service (127.0.0.1 only)")
    action.add_argument("--stop", action="store_true", help="stop the service this tool started")
    action.add_argument("--remove-exclusions", action="store_true", help="undo Defender exclusions added by this tool")
    action.add_argument("--logout", action="store_true", help="stop the service and delete the saved login")
    action.add_argument("--search", metavar="KEYWORD", help="read-only search; saves raw results for clue_import.py")
    action.add_argument("--lookup", metavar="TASK", help="reverse lookup for the papers read in a deep task")
    parser.add_argument("--out", help="with --search: output JSON file")
    parser.add_argument("--details", type=int, help="notes to open per search for text and comments (0-10; default 3 for --search, 1 for --lookup)")
    parser.add_argument("--max-papers", type=int, default=10, help="with --lookup: papers to look up (1-40)")
    parser.add_argument("--allow-defender-exclusion", action="store_true",
                        help="with --setup: let the user approve (UAC) excluding the single leakless.exe file")
    parser.add_argument("--force", action="store_true", help="with --install: download again")
    parser.add_argument("--tool-dir", help="install directory (default: runtime.local.json or per-user app data)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if args.details is None:
        args.details = 1 if args.lookup else 3
    if not 0 <= args.details <= 10 or not 1 <= args.max_papers <= 40:
        parser.error("--details must be 0-10 and --max-papers 1-40")
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if args.allow_defender_exclusion and not args.setup:
        parser.error("--allow-defender-exclusion only applies to --setup")
    tool = Path(args.tool_dir).expanduser() if args.tool_dir else runtime_tool_dir()
    if args.stop:
        print(stop_service(tool, args.port))
    elif args.start:
        pid = start_service(tool, args.port)
        ready = wait_port(args.port)
        print(f"服务已启动（PID {pid}）：http://{HOST}:{args.port}/mcp " + ("就绪" if ready else "尚未就绪"))
    elif args.setup:
        print(setup(tool, args.port, args.allow_defender_exclusion))
    elif args.install:
        print("\n".join(install(tool, force=args.force)))
    elif args.login:
        print(login(tool, args.port))
    elif args.remove_exclusions:
        print(remove_exclusions(tool))
    elif args.logout:
        print(logout(tool, args.port))
    elif args.search:
        if not args.out:
            parser.error("--search needs --out FILE")
        print(search_command(args.port, args.search, args.out, args.details))
    elif args.lookup:
        print(lookup_command(args.lookup, args.port, args.max_papers, args.details))
    else:
        print(report(tool, args.port))


if __name__ == "__main__":
    main()
