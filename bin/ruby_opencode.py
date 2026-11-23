#!/usr/bin/env python3
"""OpenCode brain adapter for Ruby.

Runs a local OpenCode agent non-interactively and turns its JSON event stream
into a spoken answer plus a compact tool log. Used by ruby_agent.py when
cloud.conf selects the OpenCode backend, and by ruby_watcher.py for proactive
briefs.

Config (cloud.conf, all optional except the provider switch):
    CLOUD_ENABLED=1
    CLOUD_PROVIDER=opencode
    OPENCODE_BIN=opencode
    OPENCODE_AGENT=ruby
    OPENCODE_MODEL=            # blank = OpenCode default model
    OPENCODE_CWD=~/.local/share/voice-assistant/agent
    OPENCODE_TIMEOUT=60
    OPENCODE_AUTO=1            # auto-approve everything the agent allowlists

Usage:
    ruby_opencode.py --json '{"prompt":"...","history":[...],"context":"..."}'
    ruby_opencode.py --check
    ruby_opencode.py --selftest
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "voice-assistant"
CLOUD_CONF = CONFIG_DIR / "cloud.conf"
DEFAULT_CWD = HOME / ".local" / "share" / "voice-assistant" / "agent"

DEFAULT_AGENT = "ruby"
DEFAULT_TIMEOUT = 60

# Avoid the assistant answering with a wall of markdown when the user is
# listening rather than reading.
_MARKDOWN_RULES = (
    (re.compile(r"```.*?```", re.DOTALL), ""),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    (re.compile(r"[*_#>•]+"), " "),
    (re.compile(r"\s+"), " "),
)


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


def _truthy(value, default=False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path)))


def config() -> dict:
    c = _read_kv(CLOUD_CONF)
    cwd = c.get("OPENCODE_CWD") or str(DEFAULT_CWD)
    provider = (c.get("CLOUD_PROVIDER") or "groq").strip().lower()
    backend = (c.get("BRAIN_BACKEND") or "").strip().lower()
    if not backend:
        backend = "opencode" if provider == "opencode" else "cloud"
    return {
        "enabled": _truthy(c.get("CLOUD_ENABLED"), False),
        "backend": backend,
        "provider": "opencode" if backend == "opencode" else provider,
        "bin": c.get("OPENCODE_BIN") or "opencode",
        "agent": c.get("OPENCODE_AGENT") or DEFAULT_AGENT,
        "model": c.get("OPENCODE_MODEL") or "",
        "cwd": _expand(cwd),
        "timeout": int(c.get("OPENCODE_TIMEOUT") or DEFAULT_TIMEOUT),
        "auto": _truthy(c.get("OPENCODE_AUTO"), True),
    }


def _find_bin(conf: dict) -> str | None:
    configured = conf.get("bin") or "opencode"
    # An explicit path is authoritative: never silently use a different binary.
    if "/" in configured:
        path = _expand(configured)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which(configured)
    if found:
        return found
    # Non-login services (watcher, Hyprland exec) often miss these locations.
    for candidate in (
        Path(configured),
        HOME / ".opencode" / "bin" / "opencode",
        HOME / ".local" / "bin" / "opencode",
        Path("/usr/local/bin/opencode"),
        Path("/usr/bin/opencode"),
    ):
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def clean_answer(text: str, limit: int = 900) -> str:
    out = (text or "").strip()
    for pattern, repl in _MARKDOWN_RULES:
        out = pattern.sub(repl, out)
    return out.strip()[:limit]


def build_message(prompt: str, history=None, context: str = "") -> str:
    """Flatten recent turns into the single message `opencode run` accepts."""
    lines = []
    for turn in (history or [])[-8:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role", "")).lower()
        if role not in ("user", "assistant"):
            continue
        content = str(turn.get("content", "")).strip()
        if content:
            lines.append(f"{'User' if role == 'user' else 'Ruby'}: {content[:800]}")
    if context:
        lines.append(f"Context: {context[:2000]}")
    if lines:
        lines.append("")
    lines.append(f"User: {prompt.strip()}")
    lines.append("")
    lines.append(
        "Reply as Ruby for a spoken voice assistant: 1-3 short sentences, no "
        "markdown, no lists, no file paths unless the user asked for them. If "
        "the request needs the desktop, use the available tools, verify the "
        "result, then confirm briefly."
    )
    return "\n".join(lines)


def parse_events(raw: str) -> dict:
    """Parse `opencode run --format json` JSONL into answer + tool actions.

    Text parts may be emitted more than once as they stream. Keep the latest
    text for each part id so a final answer is not duplicated.
    """
    texts: dict[str, str] = {}
    order: list[str] = []
    actions: list[dict] = []
    errors: list[str] = []
    session = ""

    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        session = event.get("sessionID") or session
        part = event.get("part") or {}
        kind = event.get("type")
        ptype = part.get("type")

        if kind == "text" and ptype == "text":
            pid = part.get("partID") or part.get("id") or str(len(order))
            if pid not in texts:
                order.append(pid)
            texts[pid] = part.get("text") or ""
        elif kind == "tool_use" and ptype == "tool":
            state = part.get("state") or {}
            actions.append({
                "tool": part.get("tool") or "tool",
                "status": state.get("status") or "",
                "input": state.get("input") or {},
                "output": str(state.get("output") or "")[:1200],
            })
        elif kind in ("error", "session.error"):
            detail = part.get("error") or event.get("error") or event
            errors.append(str(detail)[:400])

    answer = clean_answer("\n".join(texts[pid] for pid in order if texts.get(pid)))
    return {"answer": answer, "actions": actions, "errors": errors, "session": session}


def _run(cmd: list[str], cwd: Path, timeout: int, env: dict) -> tuple[int, str, str]:
    """Run opencode in its own process group and kill the whole tree on timeout."""
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode or 0, out or "", err or ""
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
        return 124, out or "", (err or "") + f"\nopencode timed out after {timeout}s"


def ask(prompt: str, history=None, context: str = "", conf: dict | None = None) -> dict:
    conf = conf or config()
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "empty prompt", "backend": "opencode"}

    binary = _find_bin(conf)
    if not binary:
        return {
            "ok": False,
            "error": "opencode not installed",
            "hint": "install OpenCode, or set OPENCODE_BIN in cloud.conf",
            "backend": "opencode",
        }

    cwd = conf["cwd"]
    try:
        cwd.mkdir(parents=True, exist_ok=True)
        cwd.chmod(0o700)
    except OSError:
        cwd = HOME

    cmd = [binary, "run", "--format", "json", "--agent", conf["agent"]]
    if conf.get("auto", True):
        cmd.append("--auto")
    if conf.get("model"):
        cmd.extend(["--model", conf["model"]])
    cmd.append(build_message(prompt, history=history, context=context))

    env = dict(os.environ)
    env.setdefault("PATH", f"{HOME / '.opencode' / 'bin'}:{HOME / '.local' / 'bin'}:/usr/local/bin:/usr/bin:/bin")
    started = time.monotonic()
    rc, out, err = _run(cmd, cwd=cwd, timeout=int(conf.get("timeout") or DEFAULT_TIMEOUT), env=env)
    elapsed = round(time.monotonic() - started, 2)
    parsed = parse_events(out)
    base = {
        "backend": "opencode",
        "agent": conf["agent"],
        "model": conf.get("model") or "",
        "actions": parsed["actions"],
        "session": parsed["session"],
        "elapsed": elapsed,
    }

    if rc == 124:
        return {"ok": False, "error": f"opencode timed out after {elapsed}s", **base}
    if parsed["answer"]:
        return {"ok": True, "answer": parsed["answer"], **base}
    if rc != 0:
        detail = (err or out or "").strip().splitlines()
        return {
            "ok": False,
            "error": (detail[-1] if detail else f"opencode exited {rc}")[:400],
            **base,
        }
    return {"ok": False, "error": "agent finished without a spoken answer", **base}


ASSETS_SRC = Path(__file__).resolve().parent.parent / "integrations" / "opencode"


def install_assets() -> dict:
    """Copy the bundled ruby agents and skills into ~/.config/opencode.

    Idempotent. Existing files are backed up before a differing version is
    replaced, so local edits are never lost silently.
    """
    src = ASSETS_SRC
    if not src.is_dir():
        # Installed copy: <prefix>/bin/ruby_opencode.py -> <prefix>/integrations
        src = Path(__file__).resolve().parent.parent / "integrations" / "opencode"
    if not src.is_dir():
        return {"ok": False, "error": "bundled agent assets not found", "installed": []}

    oc = HOME / ".config" / "opencode"
    installed: list[str] = []
    try:
        (oc / "agents").mkdir(parents=True, exist_ok=True)
        (oc / "skills").mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": str(e)[:200], "installed": []}

    def put(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.read_bytes() == source.read_bytes():
            return
        if target.is_file():
            backup = target.with_name(f"{target.name}.bak.{int(time.time())}")
            try:
                backup.write_bytes(target.read_bytes())
            except OSError:
                pass
        target.write_bytes(source.read_bytes())
        installed.append(str(target))

    for source in sorted((src / "agents").glob("*.md")):
        put(source, oc / "agents" / source.name)
    for skill_dir in sorted((src / "skills").iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if skill_file.is_file():
            put(skill_file, oc / "skills" / skill_dir.name / "SKILL.md")

    return {"ok": True, "installed": installed, "dir": str(oc)}


def check() -> dict:
    conf = config()
    binary = _find_bin(conf)
    agent_file = HOME / ".config" / "opencode" / "agents" / f"{conf['agent']}.md"
    return {
        "ok": bool(binary),
        "provider": conf["provider"],
        "enabled": conf["enabled"],
        "bin": binary or "",
        "agent": conf["agent"],
        "agent_file": str(agent_file) if agent_file.is_file() else "",
        "agent_installed": agent_file.is_file(),
        "model": conf["model"],
        "cwd": str(conf["cwd"]),
        "timeout": conf["timeout"],
    }


SELFTEST_EVENTS = "\n".join([
    json.dumps({"type": "step_start", "sessionID": "ses_test", "part": {"type": "step-start"}}),
    json.dumps({"type": "tool_use", "sessionID": "ses_test", "part": {
        "type": "tool", "tool": "shell",
        "state": {"status": "completed", "input": {"command": "hyprctl activewindow"},
                  "output": "class: ghostty\n"}}}),
    json.dumps({"type": "text", "sessionID": "ses_test", "part": {
        "type": "text", "partID": "p1", "text": "**Opening** the terminal now."}}),
    json.dumps({"type": "text", "sessionID": "ses_test", "part": {
        "type": "text", "partID": "p1", "text": "Opening the terminal now."}}),
    "not json at all",
])


def selftest() -> int:
    parsed = parse_events(SELFTEST_EVENTS)
    checks = [
        ("answer extracted", parsed["answer"] == "Opening the terminal now."),
        ("last text part wins", parsed["answer"].count("Opening") == 1),
        ("tool captured", len(parsed["actions"]) == 1 and parsed["actions"][0]["tool"] == "shell"),
        ("session captured", parsed["session"] == "ses_test"),
        ("markdown stripped", "**" not in parsed["answer"]),
        ("history flattened", "User: hi" in build_message("now?", [{"role": "user", "content": "hi"}])),
    ]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(ok for _, ok in checks) else 1


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--check":
        status = check()
        print(json.dumps(status, ensure_ascii=False))
        return 0 if status["ok"] else 1
    if args and args[0] == "--install":
        res = install_assets()
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1
    if args and args[0] == "--selftest":
        return selftest()
    if len(args) >= 2 and args[0] == "--json":
        try:
            req = json.loads(args[1])
        except ValueError:
            print(json.dumps({"ok": False, "error": "bad json"}))
            return 1
        res = ask(req.get("prompt", ""), history=req.get("history") or [],
                  context=req.get("context") or "")
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1
    print(json.dumps({"ok": False, "error": "usage: ruby_opencode.py --json '{...}' | --check | --selftest"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
