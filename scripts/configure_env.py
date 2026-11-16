"""Interactively store source credentials in the paper-search-mcp .env file.

Secrets are read with getpass, tested against the real service, and written only
after the test succeeds (or the user explicitly chooses to keep them anyway).
Nothing secret is printed or logged.

Usage:
    python -X utf8 -B scripts/configure_env.py openreview
    python -X utf8 -B scripts/configure_env.py semantic
    python -X utf8 -B scripts/configure_env.py openalex
    python -X utf8 -B scripts/configure_env.py unpaywall
    python -X utf8 -B scripts/configure_env.py status     # what is configured, what each source adds
"""
import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_ENV_FILE = Path.home() / ".config" / "paper-search-mcp" / ".env"
OPENREVIEW_PROBE_GROUP = "ICLR.cc/2025/Conference"
SEMANTIC_PROBE = "https://api.semanticscholar.org/graph/v1/paper/search?query=transformer&limit=1&fields=title"


def merge_env(text, updates):
    """Return .env text with `updates` applied; other lines are kept verbatim.

    A key whose value is None is removed. New keys are appended at the end.
    """
    lines = text.splitlines()
    pending = dict(updates)
    output = []
    for raw in lines:
        stripped = raw.strip()
        body = stripped[7:].strip() if stripped.startswith("export ") else stripped
        key = body.split("=", 1)[0].strip() if "=" in body and not body.startswith("#") else None
        if key in pending:
            value = pending.pop(key)
            if value is not None:
                output.append(f"{key}={value}")
            continue
        output.append(raw)
    for key, value in pending.items():
        if value is not None:
            output.append(f"{key}={value}")
    return "\n".join(output) + "\n"


def check_value(name, value):
    if not value:
        raise ValueError(f"{name} is empty")
    if value != value.strip():
        raise ValueError(f"{name} has leading or trailing spaces")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} contains a line break")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        raise ValueError(f"{name} starts and ends with the same quote; the .env reader would strip them")


def write_env(path, updates):
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as stream:  # Path.write_text(newline=) is 3.10+
        stream.write(merge_env(current, updates))
    os.replace(temporary, path)


def redact(message, secrets):
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    return message[:300]


def ask_secret(prompt):
    first = getpass.getpass(prompt + "（输入时不显示）: ")
    second = getpass.getpass("再输入一次确认: ")
    if first != second:
        raise ValueError("两次输入不一致")
    return first


def present(updates):
    """Settings as the .env reader would return them: removed keys are absent, not None."""
    return {key: value for key, value in updates.items() if value is not None}


def test_openreview(updates):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from openreview_enumerate import build_client
    try:
        import openreview
    except ImportError:
        raise RuntimeError("需要先安装 OpenReview 客户端：pip install openreview-py（只有 OpenReview 会议枚举用得到）")

    client = build_client(openreview, present(updates), "v2", "https://api2.openreview.net")
    group = client.get_group(id=OPENREVIEW_PROBE_GROUP)
    return f"登录成功，可读取 {group.id}"


def test_semantic(key):
    request = urllib.request.Request(SEMANTIC_PROBE, headers={"x-api-key": key, "User-Agent": "paper-cat/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return f"API 可用，测试查询返回 {len(payload.get('data') or [])} 条"


def collect(source):
    if source == "openreview":
        username = input("OpenReview 登录邮箱: ").strip()
        check_value("OPENREVIEW_USERNAME", username)
        if "@" not in username and not username.startswith("~"):
            print("提示：登录名通常是注册邮箱；个人 ID 需写成 ~Name1 形式。")
        password = ask_secret("OpenReview 密码")
        check_value("OPENREVIEW_PASSWORD", password)
        totp = getpass.getpass("两步验证 TOTP 密钥（没开两步验证直接回车）: ").strip()
        updates = {
            "OPENREVIEW_USERNAME": username,
            "OPENREVIEW_PASSWORD": password,
            "OPENREVIEW_TOTP_SECRET": totp or None,
        }
        return updates, [password, totp], lambda: test_openreview(updates)
    if source == "unpaywall":
        email = input("联系邮箱（Unpaywall 只用它标识请求，不发邮件）: ").strip()
        check_value("UNPAYWALL_EMAIL", email)
        if "@" not in email:
            raise ValueError("需要一个邮箱地址")
        return {"UNPAYWALL_EMAIL": email}, [], lambda: test_unpaywall(email)
    names = {"semantic": ("Semantic Scholar API key", "SEMANTIC_SCHOLAR_API_KEY", test_semantic),
             "openalex": ("OpenAlex API key", "OPENALEX_API_KEY", test_openalex)}
    label, env_key, tester = names[source]
    key = ask_secret(label)
    check_value(env_key, key)
    if not key.isascii():
        raise ValueError(f"{env_key} contains non-ASCII characters; check the copied value")
    return {env_key: key}, [key], lambda: tester(key)


def test_openalex(key):
    request = urllib.request.Request("https://api.openalex.org/rate-limit",
                                     headers={"Authorization": f"Bearer {key}", "User-Agent": "paper-cat/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        rate = (json.load(response).get("rate_limit") or {})
    return f"API 可用，今日剩余额度 {rate.get('daily_remaining_usd', '?')} 美元"


def test_unpaywall(email):
    url = "https://api.unpaywall.org/v2/10.1038/nature12373?email=" + urllib.parse.quote(email)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "paper-cat/1.0"}), timeout=30) as response:
        payload = json.load(response)
    return f"API 可用（测试 DOI 开放获取：{payload.get('is_oa')}）"


def show_status(env_file):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import load_env, source_status

    rows = source_status(load_env(env_file))
    print("数据源配置状态（全部可选：不配置也能用 arXiv + Crossref 跑完整流程）\n")
    for row in rows:
        mark = "✓ 已配置" if row["configured"] else "· 未配置"
        if row["id"] == "xiaohongshu":
            mark = "✓ 运行中" if row["configured"] else "· 未运行"
        print(f"{mark}  {row['name']}（{row['priority']}）")
        if not row["configured"]:
            print(f"      配置后：{row['benefit']}")
            print(f"      不配置：{row['without']}")
            print(f"      获取：{row['get']}")
            if row["command"]:
                print(f"      配置命令：python scripts/{row['command']}")
    print("\n密钥和密码只在你自己的终端里输入（不回显），不要发到对话里。")
    return rows


def main():
    parser = argparse.ArgumentParser(description="Store and test source credentials without echoing them.")
    parser.add_argument("source", choices=("status", "openalex", "semantic", "openreview", "unpaywall"),
                        help="status shows what is configured and what each optional source adds")
    parser.add_argument("--env-file", default=os.environ.get("PAPER_SEARCH_MCP_ENV_FILE", str(DEFAULT_ENV_FILE)))
    args = parser.parse_args()
    if args.source == "status":
        show_status(args.env_file)
        return
    if not sys.stdin.isatty():
        parser.error("needs an interactive terminal so secrets can be typed without echo")

    env_path = Path(args.env_file)
    try:
        updates, secrets, run_test = collect(args.source)
    except ValueError as error:
        print(f"未保存：{error}")
        raise SystemExit(2)
    except (EOFError, KeyboardInterrupt):
        print("\n未保存：输入被中断，请在可交互的终端里运行。")
        raise SystemExit(2)

    print("正在测试连接……")
    try:
        print(run_test())
        ok = True
    except Exception as error:
        detail = redact(str(error), secrets)
        if isinstance(error, urllib.error.HTTPError):
            detail = f"HTTP {error.code}"
        print(f"测试失败：{type(error).__name__}: {detail}")
        ok = False

    if not ok:
        answer = input("测试未通过。仍然保存到配置文件吗？[y/N]: ").strip().lower()
        if answer != "y":
            print("未保存。")
            raise SystemExit(1)

    write_env(env_path, updates)
    print(f"已写入 {env_path}（只更新了 {', '.join(k for k, v in updates.items() if v is not None)}，其他行保持不变）")


if __name__ == "__main__":
    main()
