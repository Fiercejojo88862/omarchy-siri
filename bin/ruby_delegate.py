#!/usr/bin/env python3
"""Delegate a coding task to an installed coding-agent CLI.

Supports OpenCode, Claude Code, Codex, Cursor Agent, Aider and Gemini. Detects
which are installed, runs one non-interactively in a chosen directory, and
returns a compact result. Long jobs can run detached and notify when done.

Usage:
    ruby_delegate.py --list
    ruby_delegate.py --json --cli auto --cwd ~/proj --prompt "fix tests"
    ruby_delegate.py --json --background --prompt "refactor the parser"
    ruby_delegate.py --selftest
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "voice-assistant"
CLOUD_CONF = CONFIG_DIR / "cloud.conf"
STATE_DIR = HOME / ".local" / "state" / "voice-assistant" / "delegate"
DEFAULT_TIMEOUT = 600
MAX_ANSWER = 4000

EXTRA_PATH = [
    str(HOME / ".opencode" / "bin"),
    str(HOME / ".local" / "share" / "mise" / "command-wrappers" / "bin"),
    str(HOME / ".local" / "bin"),
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
]

# Detection order for --cli auto: most agentic first.
CLIS = ("opencode", "claude", "codex", "cursor", "aider", "gemini")
BINARIES = {
    "opencode": ("opencode",),
    "claude": ("claude",),
    "codex": ("codex",),
    "cursor": ("cursor-agent", "agent"),
    "aider": ("aider",),
    "gemini": ("gemini",),
}


def _read_kv(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip().upper()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def config() -> dict:
    c = _read_kv(CLOUD_CONF)
    cwd = c.get("OPENCODE_CWD") or ""
    return {
        "cwd": Path(os.path.expandvars(os.path.expanduser(cwd))) if cwd else Path.cwd(),
        "agent": c.get("OPENCODE_DELEGATE_AGENT") or c.get("OPENCODE_AGENT") or "ruby-dev",
        "model": c.get("OPENCODE_MODEL") or "",
        "unsafe": _truthy(c.get("DELEGATE_UNSAFE")),
    }


def env() -> dict:
    e = dict(os.environ)
    path = e.get("PATH", "")
    parts = [p for p in path.split(os.pathsep) if p]
    for extra in EXTRA_PATH:
        if extra not in parts:
            parts.append(extra)
    e["PATH"] = os.pathsep.join(parts)
    return e


def find_cli(name: str) -> str | None:
    for binary in BINARIES.get(name, ()):
        found = shutil.which(binary, path=env().get("PATH"))
        if found:
            return found
    return None


def installed() -> list[str]:
    return [name for name in CLIS if find_cli(name)]


def build_argv(cli: str, binary: str, prompt: str, conf: dict) -> list[str]:
    if cli == "opencode":
        argv = [binary, "run", "--format", "json", "--auto", "--agent", conf["agent"]]
        if conf.get("model"):
            argv += ["--model", conf["model"]]
        return argv + [prompt]
    if cli == "claude":
        argv = [binary, "-p", prompt, "--output-format", "text"]
        if conf.get("unsafe"):
            argv.append("--dangerously-skip-permissions")
        else:
            argv += ["--permission-mode", "acceptEdits"]
        return argv
    if cli == "codex":
        argv = [binary, "exec", "--skip-git-repo-check"]
        if conf.get("unsafe"):
            argv.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            argv += ["--sandbox", "workspace-write"]
        return argv + [prompt]
    if cli == "cursor":
        return [binary, "-p", "--output-format", "text", prompt]
    if cli == "aider":
        return [binary, "--message", prompt, "--yes", "--no-auto-commits"]
    if cli == "gemini":
        return [binary, "-p", prompt]
    raise ValueError(f"unknown cli: {cli}")


def _clean_output(cli: str, raw: str, rc: int) -> tuple[str, list]:
    """Return (answer, actions) for the CLI's output format."""
    if cli == "opencode":
        try:
            import ruby_opencode

            parsed = ruby_opencode.parse_events(raw)
            if parsed["answer"]:
                return parsed["answer"][:MAX_ANSWER], parsed["actions"]
            # No final text: fall back to the last tool output/error.
            for action in reversed(parsed["actions"]):
                if action.get("output"):
                    return action["output"][:MAX_ANSWER], parsed["actions"]
        except Exception:
            pass
    text = (raw or "").strip()
    return text[-MAX_ANSWER:], []


def _run(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str, str, float]:
    started = time.monotonic()
    proc = subprocess.Popen(
        argv, cwd=str(cwd), env=env(), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode or 0, out or "", err or "", round(time.monotonic() - started, 2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            pass
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                pass
            out, err = "", ""
        return 124, out or "", (err or "") + f"\ndelegate timed out after {timeout}s", round(
            time.monotonic() - started, 2)


def _log_path(cli: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        STATE_DIR.chmod(0o700)
    except OSError:
        pass
    return STATE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{cli}.log"


def _notify(title: str, body: str) -> None:
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(["notify-send", "-t", "8000", title, body[:400]], timeout=5)
    except Exception:
        pass


def run(prompt: str, cli: str = "auto", cwd: Path | None = None, timeout: int = DEFAULT_TIMEOUT,
        background: bool = False) -> dict:
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "empty prompt"}
    conf = config()
    cwd = Path(os.path.expandvars(os.path.expanduser(str(cwd)))) if cwd else conf["cwd"]
    if not cwd.is_dir():
        return {"ok": False, "error": f"working directory not found: {cwd}"}

    available = installed()
    if cli == "auto":
        if not available:
            return {"ok": False, "error": "no coding CLI installed",
                    "hint": "install opencode, claude, codex, cursor-agent, aider or gemini"}
        cli = available[0]
    elif cli not in CLIS:
        return {"ok": False, "error": f"unknown cli: {cli}", "installed": available}
    binary = find_cli(cli)
    if not binary:
        return {"ok": False, "error": f"{cli} is not installed", "installed": available}

    argv = build_argv(cli, binary, prompt, conf)
    if background:
        log = _log_path(cli)
        worker = [sys.executable, str(Path(__file__).resolve()), "--worker",
                  "--cli", cli, "--cwd", str(cwd), "--prompt", prompt,
                  "--timeout", str(timeout), "--log", str(log)]
        try:
            with log.open("w", encoding="utf-8") as handle:
                subprocess.Popen(worker, cwd=str(cwd), env=env(), stdin=subprocess.DEVNULL,
                                 stdout=handle, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        except OSError as e:
            return {"ok": False, "error": str(e)[:200]}
        return {
            "ok": True, "background": True, "cli": cli, "cwd": str(cwd),
            "log": str(log),
            "answer": f"Started {cli} in {cwd.name or cwd}. I'll notify you when it finishes.",
        }

    rc, out, err, elapsed = _run(argv, cwd, timeout)
    answer, actions = _clean_output(cli, out, rc)
    if rc != 0 or not answer:
        detail = (err or out or "").strip().splitlines()
        answer = answer or (detail[-1] if detail else f"{cli} exited {rc}")
        return {
            "ok": False, "cli": cli, "cwd": str(cwd), "exit": rc,
            "elapsed": elapsed, "answer": answer[:MAX_ANSWER], "actions": actions,
            "error": (detail[-1] if detail else f"{cli} exited {rc}")[:400],
        }
    return {
        "ok": True, "cli": cli, "cwd": str(cwd), "exit": rc,
        "elapsed": elapsed, "answer": answer, "actions": actions,
    }


def worker(args: dict) -> int:
    """Detached entry point: run, write JSON, notify, exit."""
    log = Path(args.get("log") or _log_path(args.get("cli", "auto")))
    result = run(args["prompt"], cli=args.get("cli", "auto"),
                 cwd=Path(args["cwd"]) if args.get("cwd") else None,
                 timeout=int(args.get("timeout") or DEFAULT_TIMEOUT))
    result["log"] = str(log)
    try:
        log.with_suffix(log.suffix + ".json").write_text(json.dumps(result, ensure_ascii=False))
        with log.open("a", encoding="utf-8") as handle:
            handle.write("\n\n=== result ===\n")
            handle.write(json.dumps(result, ensure_ascii=False))
    except OSError:
        pass
    if result.get("ok"):
        summary = (result.get("answer") or "").strip().replace("\n", " ")[:260]
        _notify(f"Ruby: {result.get('cli')} finished", summary or "Task finished.")
    else:
        _notify(f"Ruby: {result.get('cli')} failed", (result.get("error") or "unknown error")[:260])
    return 0 if result.get("ok") else 1


def selftest() -> int:
    conf = {"agent": "ruby-dev", "model": "", "unsafe": False}
    checks = []
    checks.append(("opencode agent",
                   build_argv("opencode", "opencode", "hi", conf)[:7]
                   == ["opencode", "run", "--format", "json", "--auto", "--agent", "ruby-dev"]))
    checks.append(("claude print",
                   "--print" in build_argv("claude", "claude", "hi", conf) or
                   "-p" in build_argv("claude", "claude", "hi", conf)))
    checks.append(("codex exec", build_argv("codex", "codex", "hi", conf)[1] == "exec"))
    checks.append(("cursor print", "-p" in build_argv("cursor", "cursor-agent", "hi", conf)))
    checks.append(("aider yes", "--yes" in build_argv("aider", "aider", "hi", conf)))
    checks.append(("gemini print", "-p" in build_argv("gemini", "gemini", "hi", conf)))
    checks.append(("unsafe flag opt-in",
                   "--dangerously-skip-permissions" in
                   build_argv("claude", "claude", "hi", {**conf, "unsafe": True})))
    checks.append(("opencode json parsed",
                   _clean_output("opencode", json.dumps({
                       "type": "text", "part": {"type": "text", "partID": "p", "text": "done"},
                   }), 0)[0] == "done"))
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"installed: {', '.join(installed()) or 'none'}")
    return 0 if all(ok for _, ok in checks) else 1


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if "--selftest" in args:
        return selftest()
    if "--list" in args:
        print(json.dumps({"installed": installed(), "supported": list(CLIS)}))
        return 0
    if "--worker" in args:
        def value(flag: str, default: str = "") -> str:
            return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default
        return worker({
            "prompt": value("--prompt"), "cli": value("--cli", "auto"),
            "cwd": value("--cwd"), "timeout": value("--timeout", str(DEFAULT_TIMEOUT)),
            "log": value("--log"),
        })

    def value(flag: str, default: str = "") -> str:
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default

    prompt = value("--prompt")
    if not prompt and "-" in args:
        prompt = sys.stdin.read()
    result = run(
        prompt,
        cli=value("--cli", "auto"),
        cwd=Path(value("--cwd")) if value("--cwd") else None,
        timeout=int(value("--timeout", str(DEFAULT_TIMEOUT))),
        background="--background" in args,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
