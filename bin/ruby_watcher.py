#!/usr/bin/env python3
"""ruby-watcher — proactive background assistant for Ruby.

Monitors (configurable via ~/.config/voice-assistant/watcher.conf):
- Upcoming iCloud calendar events (15 min before)
- Docker container unexpected exits/crashes
- Canvas/NJIT assignments due within 6 hours (if not completed)
- Unlock brief after session idle/locked > 2 hours

Respects Ruby quiet/DND mode, deduplicates alerts, and logs triggers.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = HOME / ".config/voice-assistant"
STATE_DIR = HOME / ".local/state/voice-assistant"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "voice-assistant"
CONF = CONFIG_DIR / "watcher.conf"
LOG = STATE_DIR / "watcher.log"
SEEN = STATE_DIR / "watcher_seen.json"  # persisted last-alerted timestamps (survives restarts)
DOCKER_STATE = STATE_DIR / "watcher_docker.json"
LOCK_STATE = STATE_DIR / "watcher_lock.json"

STOP = False


def log(msg: str):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass


def read_conf():
    cfg = {
        "WATCHER_ENABLED": "0",
        "CALENDAR_ALERTS": "1",
        "DOCKER_ALERTS": "1",
        "CANVAS_ALERTS": "1",
        "UNLOCK_BRIEF": "1",
        "BRIEF_BRAIN": "1",
        "BRIEF_TIMEOUT": "45",
        "POLL_INTERVAL": "60",
    }
    try:
        for line in CONF.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            cfg[k.strip().upper()] = v.strip()
    except OSError:
        pass
    return cfg


def truthy(v: str, default=False):
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "on")


def quiet_active() -> bool:
    p = STATE_DIR / "quiet_until"
    try:
        return time.time() < float(p.read_text().strip())
    except Exception:
        return False


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, data):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    except Exception:
        pass


def dedupe(key: str, ttl: int) -> bool:
    """Return True if this alert should fire (not alerted within ttl).
    Persists last-alerted timestamps on disk so restarts never re-fire alerts.
    """
    seen = load_json(SEEN, {})
    if not isinstance(seen, dict):
        seen = {}
    now = time.time()
    # Backward/forward compatible: value may be expiry timestamp or {last_alerted, expires_at}.
    pruned = {}
    last_alerted = None
    for k, v in seen.items():
        ts = None
        exp = None
        if isinstance(v, dict):
            try:
                ts = float(v.get("last_alerted"))
            except Exception:
                ts = None
            try:
                exp = float(v.get("expires_at")) if v.get("expires_at") is not None else None
            except Exception:
                exp = None
        elif isinstance(v, (int, float)):
            # legacy format stored expiry timestamp
            exp = float(v)
        if ts is None and exp is not None:
            ts = exp - ttl
        if exp is None and ts is not None:
            exp = ts + ttl
        if ts is None or exp is None:
            continue
        if exp > now:
            pruned[k] = {"last_alerted": ts, "expires_at": exp}
            if k == key:
                last_alerted = ts
    if last_alerted is not None and (now - last_alerted) < ttl:
        save_json(SEEN, pruned)
        return False
    pruned[key] = {"last_alerted": now, "expires_at": now + ttl}
    save_json(SEEN, pruned)
    return True


def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return 1, "", str(e)


def notify(title: str, body: str, timeout_ms=10000, speak_text: str | None = None):
    if quiet_active():
        log(f"suppressed (quiet): {title} :: {body}")
        return
    try:
        if shutil_which("omarchy-notification-send"):
            subprocess.run(["omarchy-notification-send", "-u", "normal", "-t", str(timeout_ms), title, body[:800]],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        else:
            subprocess.run(["notify-send", "-t", str(timeout_ms), title, body[:800]],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        pass
    if speak_text:
        try:
            say = HOME / ".local/bin/local-voice-say"
            if say.exists():
                subprocess.Popen([str(say), speak_text[:220]], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def shutil_which(cmd: str):
    for p in os.environ.get("PATH", "").split(os.pathsep):
        c = Path(p) / cmd
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def is_locked() -> bool:
    # Heuristics: hyprlock process or loginctl LockedHint.
    try:
        r = subprocess.run(["pidof", "hyprlock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            return True
    except Exception:
        pass
    try:
        rc, out, _ = run(["loginctl", "show-session", "self", "-p", "LockedHint", "--value"], timeout=3)
        if rc == 0 and out.strip().lower() == "yes":
            return True
    except Exception:
        pass
    return False


def brain_configured() -> bool:
    """True when a brain (cloud or OpenCode) is enabled in cloud.conf."""
    try:
        for line in (CONFIG_DIR / "cloud.conf").read_text().splitlines():
            line = line.strip()
            if line.startswith("CLOUD_ENABLED="):
                value = line.partition("=")[2].strip().strip("'\"")
                return truthy(value, False)
    except OSError:
        pass
    return False


def brain_brief(cfg, hours: int, facts: str) -> str:
    """Ask the configured brain for a concise welcome-back brief.

    Falls back to "" on any error so the caller can use the template.
    """
    if not truthy(cfg.get("BRIEF_BRAIN", "1"), True) or not brain_configured():
        return ""
    agent = HOME / ".local" / "bin" / "ruby_agent.py"
    if not agent.is_file():
        agent = Path(__file__).resolve().parent / "ruby_agent.py"
    if not agent.is_file():
        return ""
    prompt = (
        "Write my welcome-back brief. Use only the facts below; do not call tools. "
        "One or two short spoken sentences, no lists, no markdown. Mention the most "
        "time-sensitive item first. If nothing is notable, say it is quiet."
    )
    payload = json.dumps({
        "prompt": prompt,
        "history": [],
        "context": f"Away for about {hours} hour(s). {facts}",
    })
    try:
        timeout = max(10, int(cfg.get("BRIEF_TIMEOUT", "45") or "45"))
    except ValueError:
        timeout = 45
    rc, out, err = run([sys.executable, str(agent), "--json", payload], timeout=timeout)
    if rc != 0:
        log(f"brief brain failed: {(err or out)[:160]}")
        return ""
    try:
        data = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return ""
    answer = (data.get("answer") or "").strip()
    return answer[:300] if data.get("ok") and answer else ""


def check_unlock_brief(cfg):
    if not truthy(cfg.get("UNLOCK_BRIEF", "1"), True):
        return
    state = load_json(LOCK_STATE, {"locked_since": None, "last_unlock": 0})
    locked = is_locked()
    now = time.time()
    if locked:
        if not state.get("locked_since"):
            state["locked_since"] = now
            save_json(LOCK_STATE, state)
        return
    # unlocked
    locked_since = state.get("locked_since")
    state["locked_since"] = None
    save_json(LOCK_STATE, state)
    if not locked_since:
        return
    try:
        duration = now - float(locked_since)
    except Exception:
        return
    if duration < 2 * 3600:
        return
    # Avoid repeating too often on rapid changes.
    if not dedupe("unlock_brief", 3600):
        return
    hours = max(1, int(duration // 3600))
    # Facts: calendar + docker counts (best effort).
    facts = []
    parts = [f"Welcome back. You've been away about {hours} hour{'s' if hours != 1 else ''}."]
    helper = HOME / ".local/bin/local-voice-calendar"
    if helper.exists():
        rc, out, _ = run([str(helper), "next"], timeout=8)
        if rc == 0 and out.strip():
            next_event = out.strip().splitlines()[0][:120]
            parts.append(next_event)
            facts.append(f"Next calendar item: {next_event}")
    rc, out, _ = run(["docker", "ps", "--format", "{{.Names}}"], timeout=5)
    if rc == 0:
        names = [l.strip() for l in out.splitlines() if l.strip()]
        if names:
            parts.append(f"{len(names)} containers running.")
            facts.append(f"Running containers: {', '.join(names[:8])}")
    msg = brain_brief(cfg, hours, " ".join(facts) or "No calendar events or containers.")
    source = "brain"
    if not msg:
        msg = " ".join(parts)[:240]
        source = "template"
    log(f"unlock brief ({source}): {msg}")
    notify("Ruby", "Welcome back", speak_text=msg)


def check_calendar(cfg):
    if not truthy(cfg.get("CALENDAR_ALERTS", "1"), True):
        return
    helper = HOME / ".local/bin/local-voice-calendar"
    if not helper.exists():
        return
    # Try to get upcoming events; prefer a machine-readable mode if available.
    # Fallback: parse `today` output is hard; best-effort with `next` and also
    # attempt `upcoming` if helper supports it.
    events = []
    for args in (["upcoming", "--json"], ["today", "--json"]):
        rc, out, _ = run([str(helper)] + args, timeout=10)
        if rc == 0 and out.strip().startswith("["):
            try:
                data = json.loads(out)
                if isinstance(data, list):
                    events = data
                    break
            except Exception:
                pass
    # If no JSON support, skip to avoid false parsing/alerts.
    if not events:
        return
    now = datetime.now().astimezone()
    for ev in events:
        try:
            title = str(ev.get("title") or ev.get("summary") or "Event")
            start_s = str(ev.get("start") or ev.get("start_time") or "")
            if not start_s:
                continue
            # Accept ISO-ish formats.
            try:
                start = datetime.fromisoformat(start_s.replace("Z", "+00:00"))
            except Exception:
                continue
            if start.tzinfo is None:
                start = start.astimezone()
            delta = (start - now).total_seconds()
            if 0 < delta <= 15 * 60:
                key = f"cal:{title}:{start.isoformat()}"
                if dedupe(key, 3600):
                    mins = max(1, int(delta // 60))
                    msg = f"{title} starts in {mins} minutes."
                    log(f"calendar alert: {msg}")
                    notify("Calendar", msg, speak_text=msg)
        except Exception:
            continue


def check_docker(cfg):
    if not truthy(cfg.get("DOCKER_ALERTS", "1"), True):
        return
    if not shutil_which("docker"):
        return
    rc, out, _ = run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"], timeout=8)
    if rc != 0:
        return
    prev = load_json(DOCKER_STATE, {"statuses": {}})
    prev_statuses = prev.get("statuses") if isinstance(prev, dict) else {}
    if not isinstance(prev_statuses, dict):
        prev_statuses = {}
    curr = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        name, status = line.split("\t", 1)
        curr[name.strip()] = status.strip()
    # detect unexpected exits/crashes
    for name, status in curr.items():
        old = str(prev_statuses.get(name, ""))
        low = status.lower()
        was_running = old.lower().startswith("up")
        now_bad = low.startswith("exited") or "dead" in low or "oom" in low
        if was_running and now_bad:
            if dedupe(f"docker_exit:{name}:{status}", 6 * 3600):
                msg = f"Container {name} stopped unexpectedly: {status}."
                log(f"docker alert: {msg}")
                notify("Docker", msg, speak_text=f"Container {name} stopped unexpectedly.")
    save_json(DOCKER_STATE, {"statuses": curr, "ts": time.time()})


def check_canvas(cfg):
    if not truthy(cfg.get("CANVAS_ALERTS", "1"), True):
        return
    helper = HOME / "Work/bin/canvas-siri"
    if not helper.exists():
        helper = Path(shutil_which("canvas-siri") or "")
    if not helper or not helper.exists():
        return
    # Best-effort: try JSON modes if supported; otherwise skip to avoid noisy parsing.
    for args in (["due-soon", "--json", "--hours", "6"], ["upcoming", "--json"], ["voice", "what is due soon"]):
        rc, out, _ = run([str(helper)] + args, timeout=15)
        if rc != 0 or not out.strip():
            continue
        if out.lstrip().startswith("["):
            try:
                items = json.loads(out)
            except Exception:
                items = []
            if isinstance(items, list):
                for it in items:
                    try:
                        title = str(it.get("title") or it.get("name") or "Assignment")
                        due_s = str(it.get("due") or it.get("due_at") or "")
                        done = bool(it.get("completed") or it.get("submitted") or it.get("done"))
                        if done or not due_s:
                            continue
                        due = datetime.fromisoformat(due_s.replace("Z", "+00:00"))
                        if due.tzinfo is None:
                            due = due.astimezone()
                        now = datetime.now().astimezone()
                        delta = (due - now).total_seconds()
                        if 0 < delta <= 6 * 3600:
                            key = f"canvas:{title}:{due.isoformat()}"
                            if dedupe(key, 6 * 3600):
                                hrs = max(1, int(delta // 3600))
                                msg = f"{title} is due in about {hrs} hours."
                                log(f"canvas alert: {msg}")
                                notify("Canvas", msg, speak_text=msg)
                    except Exception:
                        continue
            return
        # Non-JSON voice text fallback: only alert if clearly indicates due soon (heuristic)
        low = out.lower()
        if "due" in low and ("hour" in low or "today" in low) and "no " not in low[:40]:
            key = f"canvas_text:{hash(out.strip())}"
            if dedupe(key, 6 * 3600):
                msg = out.strip().splitlines()[0][:220]
                log(f"canvas alert(text): {msg}")
                notify("Canvas", msg, speak_text=msg[:180])
        return


def handle_stop(signum, frame):
    global STOP
    STOP = True


def main():
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    try:
        RUNTIME_DIR.chmod(0o700)
    except Exception:
        pass
    log("ruby-watcher started")
    while not STOP:
        cfg = read_conf()
        try:
            interval = max(15, int(float(cfg.get("POLL_INTERVAL", "60"))))
        except Exception:
            interval = 60
        if truthy(cfg.get("WATCHER_ENABLED", "0"), False):
            try:
                check_unlock_brief(cfg)
            except Exception as e:
                log(f"unlock check error: {e}")
            try:
                check_calendar(cfg)
            except Exception as e:
                log(f"calendar check error: {e}")
            try:
                check_docker(cfg)
            except Exception as e:
                log(f"docker check error: {e}")
            try:
                check_canvas(cfg)
            except Exception as e:
                log(f"canvas check error: {e}")
        # sleep in small steps for responsive stop
        for _ in range(interval):
            if STOP:
                break
            time.sleep(1)
    log("ruby-watcher stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
