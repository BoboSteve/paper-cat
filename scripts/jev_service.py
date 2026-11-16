#!/usr/bin/env python3
"""Start, check or stop the local Jev ranking model on Windows, macOS or Linux.

    python scripts/jev_service.py start | status | stop

Configured in ``runtime.local.json`` under ``service`` (see runtime.example.*.json):

* ``"mode": "command"`` delegates to existing scripts, e.g. a PowerShell launcher on Windows;
* ``"mode": "managed"`` (default) launches the listed processes itself, records their PIDs, waits
  for each ``/health`` endpoint, and stops only the processes it started.

Placeholders ``{tool_root}``, ``{skill_dir}`` and ``{python}`` are expanded in commands.
"""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SKILL_DIR, cache_dir, read_json, runtime_settings, write_json  # noqa: E402


def expand(value, settings):
    mapping = {"tool_root": settings.get("tool_root", ""), "skill_dir": str(SKILL_DIR),
               "python": settings.get("python") or sys.executable}
    return value.format(**mapping) if isinstance(value, str) else value


def health(url, timeout=3):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=timeout) as response:
            return json.load(response)
    except Exception:
        return None


def port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def state_path():
    return cache_dir() / "jev_service_state.json"


def default_health(settings):
    return settings.get("service_url", "http://127.0.0.1:8090").rstrip("/") + "/health"


def run_command_mode(action, config, settings):
    command = config.get(action)
    if not command:
        raise SystemExit(f"runtime.local.json service.{action} is not configured")
    # Output goes to a file, not a pipe: background model processes inherit the handles, and an
    # inherited pipe would keep the caller (an agent shell) waiting until the model exits.
    log = cache_dir() / "logs" / f"service-{action}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "wb") as stream:
        code = subprocess.call([expand(part, settings) for part in command],
                               stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
    print(log.read_text(encoding="utf-8", errors="replace").strip())
    return code


def start_managed(config, settings, timeout):
    processes = config.get("processes") or []
    if not processes:
        raise SystemExit("runtime.local.json service.processes is empty; see runtime.example.*.json")
    if health(default_health(settings)) and state_path().exists():
        print("Jev service is already running.")
        return 0
    for process in processes:
        if process.get("port") and port_busy(process["port"]):
            raise SystemExit(f"port {process['port']} is in use by another program; not replacing it")
    logs = cache_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    state = {"started_at": time.time(), "processes": []}
    try:
        for process in processes:
            env = {**os.environ, "PYTHONUTF8": "1", **{k: expand(v, settings) for k, v in (process.get("env") or {}).items()}}
            command = [expand(part, settings) for part in process["command"]]
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                kwargs["start_new_session"] = True
            name = process.get("name", Path(command[0]).stem)
            with open(logs / f"{name}.stdout.log", "ab") as out, open(logs / f"{name}.stderr.log", "ab") as err:
                child = subprocess.Popen(command, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                         cwd=expand(process.get("cwd") or settings.get("tool_root") or str(SKILL_DIR), settings),
                                         env=env, **kwargs)
            state["processes"].append({"name": name, "pid": child.pid, "command": command})
            write_json(state_path(), state)
            url = expand(process.get("health") or "", settings)
            deadline = time.monotonic() + timeout
            while url and time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError(f"{name} exited early; see {logs / (name + '.stderr.log')}")
                if health(url):
                    break
                time.sleep(1)
            else:
                if url:
                    raise RuntimeError(f"{name} did not become healthy within {timeout}s")
            print(f"{name} ready (pid {child.pid})")
    except Exception:
        stop_managed()
        raise
    return 0


def kill_tree(pid):
    if os.name == "nt":
        subprocess.call(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except ProcessLookupError:
        pass


def stop_managed():
    state = read_json(state_path(), None)
    if not state:
        print("No Jev service started by this skill is recorded.")
        return 0
    for process in reversed(state.get("processes") or []):
        kill_tree(process["pid"])
        print(f"stopped {process['name']} (pid {process['pid']})")
    state_path().unlink(missing_ok=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("start", "status", "stop"))
    parser.add_argument("--timeout", type=float, default=180, help="seconds to wait for health on start")
    args = parser.parse_args()
    settings = runtime_settings()
    config = settings.get("service") or {}
    if args.action == "status":
        payload = health(default_health(settings))
        print(json.dumps({"healthy": bool(payload), "health": payload,
                          "recorded_by_skill": state_path().exists()}, ensure_ascii=False, indent=2))
        return 0 if payload else 1
    if config.get("mode") == "command":
        return run_command_mode(args.action, config, settings)
    return start_managed(config, settings, args.timeout) if args.action == "start" else stop_managed()


if __name__ == "__main__":
    raise SystemExit(main())
