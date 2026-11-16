#!/usr/bin/env python3
"""Check configured paper sources, local tools, storage, and the Jev service."""
import argparse
import datetime
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from startlux_runtime import validate_service


DEFAULT_ENV_FILE = Path.home() / ".config" / "paper-search-mcp" / ".env"
OPENALEX_RATE_LIMIT = "https://api.openalex.org/rate-limit"
SEMANTIC_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_env(path):
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
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key.strip()] = value
    for key in (
        "SEMANTIC_SCHOLAR_API_KEY",
        "OPENALEX_API_KEY",
        "OPENALEX_EMAIL",
        "UNPAYWALL_EMAIL",
        "OPENREVIEW_USERNAME",
        "OPENREVIEW_PASSWORD",
        "OPENREVIEW_TOTP_SECRET",
    ):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def request_json(url, headers=None, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": "paper-cat-preflight/1.0", **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def check_semantic(settings, timeout):
    key = settings.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if not key:
        return {"status": "degraded", "configured": False, "reason": "SEMANTIC_SCHOLAR_API_KEY missing"}
    if any(ord(character) > 127 for character in key):
        return {
            "status": "blocked",
            "configured": True,
            "reason": "SEMANTIC_SCHOLAR_API_KEY contains non-ASCII characters; check the copied value",
        }
    url = SEMANTIC_SEARCH + "?query=large%20language%20model&limit=1&fields=title"
    try:
        payload = request_json(url, headers={"x-api-key": key}, timeout=timeout)
    except urllib.error.HTTPError as error:
        return {"status": "blocked", "configured": True, "http_status": error.code}
    except Exception as error:
        return {"status": "blocked", "configured": True, "error_type": type(error).__name__}
    return {"status": "ok", "configured": True, "results": len(payload.get("data") or [])}


def check_openalex(settings, timeout):
    key = settings.get("OPENALEX_API_KEY", "").strip()
    if not key:
        # Search works without a key on the shared public quota; only /rate-limit needs one.
        try:
            request_json("https://api.openalex.org/works?search=language%20model&per-page=1&select=id", timeout=timeout)
            return {"status": "ok", "configured": False, "note": "public quota (no key): works, lower limits"}
        except Exception as error:
            return {"status": "blocked", "configured": False, "error_type": type(error).__name__}
    headers = {"Authorization": f"Bearer {key}"}
    try:
        payload = request_json(OPENALEX_RATE_LIMIT, headers=headers, timeout=timeout)
    except urllib.error.HTTPError as error:
        return {"status": "blocked", "configured": bool(key), "http_status": error.code}
    except Exception as error:
        return {"status": "blocked", "configured": bool(key), "error_type": type(error).__name__}
    rate = payload.get("rate_limit") or {}
    return {
        "status": "ok",
        "configured": bool(key),
        "daily_budget_usd": rate.get("daily_budget_usd"),
        "daily_used_usd": rate.get("daily_used_usd"),
        "daily_remaining_usd": rate.get("daily_remaining_usd"),
        "resets_at": rate.get("resets_at"),
    }


def check_jev(url, timeout):
    try:
        payload = request_json(url.rstrip("/") + "/health", timeout=timeout)
    except Exception as error:
        return {"status": "blocked", "error_type": type(error).__name__}
    engine = payload.get("engine") or {}
    model = str(payload.get("model") or "")
    quant = (engine.get("quantization") or {}).get("bits")
    try:
        validate_service(payload)
        valid, reason = True, None
    except ValueError as error:
        valid, reason = False, str(error)
    return {
        "status": "ok" if valid else "degraded",
        "model": model,
        "engine": engine.get("name"),
        "quantization_bits": quant,
        "prompt_attention": payload.get("prompt_attention"),
        "reason": reason,
    }


def check_openreview(settings, env_file):
    configured = bool(settings.get("OPENREVIEW_USERNAME") and settings.get("OPENREVIEW_PASSWORD"))
    if not configured:
        return {"status": "degraded", "configured": False, "reason": "credentials missing"}
    command = [
        sys.executable,
        "-c",
        "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('openreview') else 1)",
    ]
    installed = subprocess.run(command, check=False, capture_output=True).returncode == 0
    requirements = Path(__file__).resolve().parents[1] / "requirements-openreview.txt"
    provisionable = bool(shutil.which("uv") and requirements.exists())
    return {
        "status": "ok" if installed or provisionable else "degraded",
        "configured": True,
        "installed_in_current_python": installed,
        "provisionable_with_uv": provisionable,
        "env_file": str(env_file),
        "note": None if installed or provisionable else "use requirements-openreview.txt or uv run with openreview-py==2.9.1",
    }


def check_file_security(path):
    path = Path(path)
    if not path.exists():
        return {"status": "degraded", "reason": "env file missing"}
    if os.name == "nt":
        return {"status": "degraded", "reason": "Windows ACL not evaluated by POSIX mode checks"}
    mode = stat.S_IMODE(path.stat().st_mode)
    return {
        "status": "ok" if mode & 0o077 == 0 else "degraded",
        "mode": oct(mode),
        "reason": None if mode & 0o077 == 0 else "env file is readable by group or others",
    }


def check_tools():
    tools = {}
    for name in ("uv", "uvx", "pdftotext"):
        path = shutil.which(name)
        tools[name] = {"status": "ok" if path else "degraded", "path": path}
    return tools


def overall_status(components):
    statuses = [value.get("status") for value in components.values()]
    if "blocked" in statuses:
        return "blocked"
    if "degraded" in statuses:
        return "degraded"
    return "ok"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=os.environ.get("PAPER_SEARCH_MCP_ENV_FILE", str(DEFAULT_ENV_FILE)))
    parser.add_argument("--jev-url", default="http://127.0.0.1:8090")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--out", required=True)
    parser.add_argument("--require", default="", help="comma-separated components that must be ok")
    parser.add_argument("--allow-degraded", action="store_true", help="return success for non-blocking degradation")
    parser.add_argument("--skip-network", action="store_true")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    env_file = Path(args.env_file)
    settings = load_env(env_file)
    tools = check_tools()
    components = {
        "env_file": check_file_security(env_file),
        "tools": {
            "status": "degraded" if any(value["status"] != "ok" for value in tools.values()) else "ok",
            **tools,
        },
        "disk": {
            "status": "ok",
            "free_gb": round(shutil.disk_usage(Path.cwd()).free / 1024**3, 2),
        },
        "openreview": check_openreview(settings, env_file),
        "jev": check_jev(args.jev_url, args.timeout),
    }
    if components["disk"]["free_gb"] < 1:
        components["disk"]["status"] = "degraded"
    if args.skip_network:
        components["semantic"] = {"status": "degraded", "reason": "network checks skipped"}
        components["openalex"] = {"status": "degraded", "reason": "network checks skipped"}
    else:
        components["semantic"] = check_semantic(settings, args.timeout)
        components["openalex"] = check_openalex(settings, args.timeout)

    required = [value.strip() for value in args.require.split(",") if value.strip()]
    failures = [
        name for name in required
        if (components.get(name) or {}).get("status") != "ok"
    ]
    status = overall_status(components)
    if status == "blocked" and required and not failures:
        status = "degraded"
    report = {
        "status": status,
        "started_at": now(),
        "env_file": str(env_file),
        "required_ok": not failures,
        "failed_requirements": failures,
        "components": components,
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PREFLIGHT {report['status']} required_ok={report['required_ok']} -> {output}")
    for name, value in components.items():
        print(f"  {name}: {value.get('status')}")
    if failures or report["status"] == "blocked":
        raise SystemExit(2)
    if report["status"] == "degraded" and not args.allow_degraded:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
