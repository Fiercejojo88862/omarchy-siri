#!/usr/bin/env python3
"""LLM tool-calling agent for Ruby (OpenAI-compatible/Groq and Ollama).

Provides structured tool schemas and a tool-execution loop. Designed to be used
by local-voice-exec for fallback/routing while keeping fast deterministic
commands intact in the caller.

Usage:
  ruby_agent.py --json '{"prompt":"...","history":[...],"context":"..."}'
Outputs JSON: {"ok":true,"answer":"...","tool_calls":[...],"actions":[...]}
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = HOME / ".config/voice-assistant"
STATE_DIR = HOME / ".local/state/voice-assistant"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "voice-assistant"
BIN_DIR = HOME / ".local/bin"
LOCAL_BIN = str(BIN_DIR)

# Ensure bin on path for module imports
sys.path.insert(0, str(Path(__file__).resolve().parent))


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


def _cloud_conf():
    c = _read_kv(CONFIG_DIR / "cloud.conf")
    return {
        "enabled": c.get("CLOUD_ENABLED", "0").lower() in ("1", "true", "yes", "on"),
        "key": c.get("CLOUD_API_KEY", ""),
        "base_url": (c.get("CLOUD_BASE_URL", "https://api.groq.com/openai/v1") or "").rstrip("/"),
        "model": c.get("CLOUD_MODEL", "llama-3.3-70b-versatile") or "llama-3.3-70b-versatile",
        "daily_cap": int(c.get("CLOUD_DAILY_CAP", "0") or "0"),
    }


def _local_model():
    c = _read_kv(CONFIG_DIR / "voice.conf")
    return c.get("OLLAMA_MODEL", "qwen2.5:3b") or "qwen2.5:3b"


def _run(cmd, timeout=12, input_text=None):
    try:
        r = subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 1, "", str(e)[:200]


def _which(name):
    return shutil.which(name)


# ---- Tool schemas (OpenAI function/tool format) ----
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_calendar",
            "description": "Get calendar/schedule information from iCloud calendar integration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "when": {"type": "string", "enum": ["today", "tomorrow", "next", "refresh"], "default": "today"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_canvas",
            "description": "Check Canvas/NJIT assignments, homework, quizzes, labs, projects, due/missing/overdue items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to check, e.g. what's due, what's left, missing assignments, course name."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather or forecast for a place (or home by default).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Weather request, e.g. 'weather in London', 'will it rain tomorrow', 'forecast'."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "docker_status",
            "description": "Get Docker container status (running/stopped counts and names).",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "docker_restart",
            "description": "Restart specific Docker containers by name. Only use if user explicitly asked to restart them; caller should confirm for safety when appropriate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "containers": {"type": "array", "items": {"type": "string"}, "description": "Container names to restart."},
                    "confirmed": {"type": "boolean", "description": "Whether user has confirmed the restart.", "default": False}
                },
                "required": ["containers"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hyprland_dispatch",
            "description": "Run a Hyprland 0.56+ dispatch action (hl.dsp.*) or safe hyprctl dispatch for window/workspace control.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "A single hl.dsp.*(...) dispatch expression or a named action like close_window, toggle_float, fullscreen, workspace:<n>, move_to_workspace:<n>, focus:<l|r|u|d>."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hypruse",
            "description": "UI inspection and control via hypruse: describe screen/windows, click controls, scroll, type text, press keys. Refuses in locked sessions, password fields, and auth dialogs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["describe", "click", "scroll", "key", "type", "launch"]},
                    "target": {"type": "string", "description": "For click: label/name. For scroll: up/down/left/right and optional amount. For key: key/combo. For type: text to type. For launch: app and optional workspace."},
                    "amount": {"type": "integer", "description": "Scroll amount (optional)."},
                    "workspace": {"type": "string", "description": "Workspace for launch (optional)."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard",
            "description": "Read or write the system clipboard (wl-paste/wl-copy).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "write"]},
                    "text": {"type": "string", "description": "Text to copy when action=write."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "system_control",
            "description": "System/audio/display/connectivity controls: volume, mute, brightness, night light, wifi, bluetooth status/connect/disconnect, battery/disk/stats, media play/pause/next/prev, lock/wake. Destructive power actions are not executed here.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Action name, e.g. volume_set, volume_up, volume_down, mute_toggle, mute, unmute, brightness_set, brightness_up, brightness_down, nightlight_toggle, wifi_on, wifi_off, wifi_toggle, wifi_status, bluetooth_status, bluetooth_connect, bluetooth_disconnect, battery, disk, stats, media_play_pause, media_next, media_prev, media_status, lock, wake"},
                    "value": {"type": "string", "description": "Optional value (percent, device name, etc.)."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "timer_reminder",
            "description": "Manage timers and reminders: set/list/pause/resume/cancel timers, set reminders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["set_timer", "list_timers", "pause_timer", "resume_timer", "cancel_timer", "set_reminder", "list_reminders"]},
                    "minutes": {"type": "number", "description": "Duration in minutes for timer/reminder."},
                    "label": {"type": "string", "description": "Label/name/message."},
                    "when": {"type": "string", "description": "Time string for reminders (if applicable)."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_lookup",
            "description": "Search the web for quick factual information/snippets to answer a question (no browser open).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."}
                },
                "required": ["query"]
            }
        }
    },
]


# ---- Tool implementations (best-effort, safe, local-first) ----


def tool_get_calendar(args):
    helper = BIN_DIR / "local-voice-calendar"
    when = (args.get("when") or "today").strip().lower()
    if when not in ("today", "tomorrow", "next", "refresh"):
        when = "today"
    if not helper.exists():
        return {"ok": False, "error": "calendar helper not installed"}
    rc, out, err = _run([str(helper), when], timeout=12)
    return {"ok": rc == 0, "output": out or err, "when": when}


def tool_get_canvas(args):
    helper = HOME / "Work/bin/canvas-siri"
    if not helper.exists():
        helper = Path(_which("canvas-siri") or "")
    if not helper.exists():
        return {"ok": False, "error": "Canvas helper not available"}
    query = (args.get("query") or "what is due").strip()
    rc, out, err = _run([str(helper), "voice", query], timeout=20)
    return {"ok": rc == 0, "output": (out or err)[:2000]}


def tool_get_weather(args):
    # Delegate to local-voice-exec for consistent behavior (AccuWeather/wttr/etc.)
    q = (args.get("query") or "weather").strip()
    exe = BIN_DIR / "local-voice-exec"
    if exe.exists():
        rc, out, err = _run([str(exe), f"what's the {q}" if "weather" not in q.lower() else q], timeout=20)
        return {"ok": rc == 0, "output": out or err or "Requested weather."}
    return {"ok": False, "error": "weather unavailable"}


def tool_docker_status(args):
    if not _which("docker"):
        return {"ok": False, "error": "docker not installed"}
    rc, out, err = _run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"], timeout=8)
    rc2, out2, _ = _run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"], timeout=8)
    running = [l.split("\t", 1)[0] for l in out.splitlines() if l.strip()] if rc == 0 else []
    allc = [l.split("\t", 1)[0] for l in out2.splitlines() if l.strip()] if rc2 == 0 else []
    stopped = [n for n in allc if n not in running]
    return {"ok": rc == 0, "running": running, "stopped": stopped, "running_count": len(running), "stopped_count": len(stopped)}


def tool_docker_restart(args):
    containers = args.get("containers") or []
    if isinstance(containers, str):
        containers = [containers]
    containers = [str(c).strip() for c in containers if str(c).strip()]
    if not containers:
        return {"ok": False, "error": "no containers specified"}
    if not args.get("confirmed", False):
        return {"ok": False, "error": "confirmation required", "containers": containers}
    if not _which("docker"):
        return {"ok": False, "error": "docker not installed"}
    # Only restart containers that exist.
    rc, out, _ = _run(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=8)
    known = set(l.strip() for l in out.splitlines() if l.strip()) if rc == 0 else set()
    results = {}
    for c in containers[:10]:
        if known and c not in known:
            results[c] = "not found"
            continue
        rci, _, err = _run(["docker", "restart", c], timeout=30)
        results[c] = "restarted" if rci == 0 else (err or "failed")
    return {"ok": True, "results": results}


def tool_hyprland_dispatch(args):
    action = (args.get("action") or "").strip()
    if not action:
        return {"ok": False, "error": "missing action"}
    # Normalize named actions
    low = action.lower()
    expr = action
    if low == "close_window":
        expr = "hl.dsp.window.close()"
    elif low == "toggle_float":
        expr = 'hl.dsp.window.float({ action = "toggle" })'
    elif low == "fullscreen":
        expr = 'hl.dsp.window.fullscreen({ mode = "fullscreen" })'
    elif low.startswith("workspace:"):
        n = low.split(":", 1)[1].strip()
        expr = f'hl.dsp.focus({{ workspace = "{n}" }})'
    elif low.startswith("move_to_workspace:"):
        n = low.split(":", 1)[1].strip()
        expr = f'hl.dsp.window.move({{ workspace = "{n}", follow = true }})'
    elif low.startswith("focus:"):
        d = low.split(":", 1)[1].strip()
        expr = f'hl.dsp.focus({{ direction = "{d}" }})'
    # basic safety: only allow hl.dsp calls
    if not re.match(r'^hl\.dsp(?:\.[A-Za-z_][\w]*)+\s*\(.*\)\s*;?\s*$', expr, re.DOTALL):
        return {"ok": False, "error": "only hl.dsp.* dispatch expressions are allowed"}
    if "toggle_floating" in expr or "toggle_fullscreen" in expr:
        return {"ok": False, "error": "invalid dispatch"}
    rc, out, err = _run(["hyprctl", "dispatch", expr], timeout=6)
    msg = (out + "\n" + err).strip()
    if rc != 0 or msg.lower().startswith("error:"):
        return {"ok": False, "error": msg[:200] or "dispatch failed", "expr": expr}
    return {"ok": True, "expr": expr, "output": msg[:200]}


def tool_hypruse(args):
    if not _which("hypruse"):
        return {"ok": False, "error": "hypruse not installed"}
    action = (args.get("action") or "").strip().lower()
    target = str(args.get("target") or "")
    try:
        # hypruse refuses locked/password/auth contexts itself; we rely on it.
        if action == "describe":
            rc, out, err = _run(["hypruse", "describe"], timeout=10)
            return {"ok": rc == 0, "output": (out or err)[:3000]}
        if action == "click":
            rc, out, err = _run(["hypruse", "click", target], timeout=10)
            return {"ok": rc == 0, "output": (out or err)[:500]}
        if action == "scroll":
            direction = (target or "down").split()[0]
            amt = str(args.get("amount") or (target.split()[1] if len(target.split()) > 1 else "3"))
            rc, out, err = _run(["hypruse", "scroll", direction, amt], timeout=10)
            return {"ok": rc == 0, "output": (out or err)[:300]}
        if action == "key":
            keys = target.split()
            if not keys:
                return {"ok": False, "error": "missing key"}
            rc, out, err = _run(["hypruse", "key", *keys], timeout=10)
            return {"ok": rc == 0, "output": (out or err)[:300]}
        if action == "type":
            rc, out, err = _run(["hypruse", "type", target], timeout=10)
            return {"ok": rc == 0, "output": (out or err)[:300]}
        if action == "launch":
            ws = str(args.get("workspace") or "")
            cmd = ["hypruse", "launch", target] + ([ws] if ws else [])
            rc, out, err = _run(cmd, timeout=10)
            return {"ok": rc == 0, "output": (out or err)[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": False, "error": f"unknown hypruse action: {action}"}


def tool_clipboard(args):
    action = (args.get("action") or "").lower()
    if action == "read":
        if not _which("wl-paste"):
            return {"ok": False, "error": "wl-paste not available"}
        rc, out, err = _run(["wl-paste", "--no-newline"], timeout=5)
        return {"ok": rc == 0, "text": (out or "")[:4000], "error": err if rc != 0 else ""}
    if action == "write":
        if not _which("wl-copy"):
            return {"ok": False, "error": "wl-copy not available"}
        text = str(args.get("text") or "")
        try:
            p = subprocess.run(["wl-copy"], input=text, text=True, capture_output=True, timeout=5)
            return {"ok": p.returncode == 0, "error": (p.stderr or "").strip()[:200]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
    return {"ok": False, "error": "unknown clipboard action"}


def tool_system_control(args):
    action = (args.get("action") or "").lower().strip()
    value = str(args.get("value") or "").strip()
    # Best-effort mappings using omarchy/pactl/playerctl/etc.
    def pactl(*a): return _run(["pactl", *a], timeout=5)
    if action == "volume_set" and value:
        rc, _, err = pactl("set-sink-volume", "@DEFAULT_SINK@", f"{int(float(value))}%")
        return {"ok": rc == 0, "error": err}
    if action == "volume_up":
        rc, _, err = pactl("set-sink-volume", "@DEFAULT_SINK@", "+5%")
        return {"ok": rc == 0}
    if action == "volume_down":
        rc, _, err = pactl("set-sink-volume", "@DEFAULT_SINK@", "-5%")
        return {"ok": rc == 0}
    if action in ("mute_toggle", "mute", "unmute"):
        arg = "toggle" if action == "mute_toggle" else ("1" if action == "mute" else "0")
        rc, _, err = pactl("set-sink-mute", "@DEFAULT_SINK@", arg)
        return {"ok": rc == 0}
    if action in ("brightness_set", "brightness_up", "brightness_down"):
        if _which("brightnessctl"):
            if action == "brightness_set" and value:
                rc, _, err = _run(["brightnessctl", "set", f"{int(float(value))}%"], timeout=5)
            elif action == "brightness_up":
                rc, _, err = _run(["brightnessctl", "set", "+10%"], timeout=5)
            else:
                rc, _, err = _run(["brightnessctl", "set", "10%-"], timeout=5)
            return {"ok": rc == 0}
        if _which("omarchy-brightness"):
            arg = value or ("+10" if action == "brightness_up" else "-10")
            rc, _, err = _run(["omarchy-brightness", arg], timeout=5)
            return {"ok": rc == 0}
        return {"ok": False, "error": "brightness control unavailable"}
    if action == "nightlight_toggle":
        for cmd in (["omarchy-toggle-nightlight"], ["omarchy", "toggle-nightlight"]):
            if _which(cmd[0]):
                rc, _, err = _run(cmd, timeout=8)
                return {"ok": rc == 0}
        return {"ok": False, "error": "night light control unavailable"}
    if action in ("wifi_on", "wifi_off", "wifi_toggle", "wifi_status"):
        if action == "wifi_status":
            rc, out, err = _run(["nmcli", "-t", "-f", "WIFI,STATE", "general"], timeout=5)
            return {"ok": rc == 0, "output": out or err}
        if action == "wifi_toggle":
            rc, out, _ = _run(["nmcli", "radio", "wifi"], timeout=5)
            new = "off" if "enabled" in out else "on"
            rc, _, err = _run(["nmcli", "radio", "wifi", new], timeout=5)
            return {"ok": rc == 0, "state": new}
        rc, _, err = _run(["nmcli", "radio", "wifi", "on" if action == "wifi_on" else "off"], timeout=5)
        return {"ok": rc == 0}
    if action == "bluetooth_status":
        rc, out, err = _run(["bluetoothctl", "devices"], timeout=6)
        return {"ok": rc == 0, "output": out or err}
    if action in ("bluetooth_connect", "bluetooth_disconnect"):
        dev = value
        if not dev:
            return {"ok": False, "error": "device name required"}
        # best-effort: try omarchy-bluetooth if available, else bluetoothctl by name not trivial
        if _which("omarchy-bluetooth"):
            sub = "connect" if action == "bluetooth_connect" else "disconnect"
            rc, out, err = _run(["omarchy-bluetooth", sub, dev], timeout=15)
            return {"ok": rc == 0, "output": (out or err)[:300]}
        return {"ok": False, "error": "bluetooth control unavailable"}
    if action == "battery":
        rc, out, err = _run(["upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0"], timeout=5)
        if rc != 0:
            rc, out, err = _run(["acpi", "-b"], timeout=5)
        return {"ok": rc == 0, "output": (out or err)[:800]}
    if action == "disk":
        rc, out, err = _run(["df", "-h", str(HOME)], timeout=5)
        return {"ok": rc == 0, "output": (out or err)[:500]}
    if action == "stats":
        rc, out, err = _run(["omarchy-stats"] if _which("omarchy-stats") else ["bash", "-lc", "free -h; uptime"], timeout=8)
        return {"ok": rc == 0, "output": (out or err)[:1200]}
    if action in ("media_play_pause", "media_next", "media_prev", "media_status"):
        if not _which("playerctl"):
            return {"ok": False, "error": "playerctl unavailable"}
        cmd = {"media_play_pause": "play-pause", "media_next": "next", "media_prev": "previous", "media_status": "metadata"}[action]
        args2 = ["playerctl", cmd]
        if cmd == "metadata":
            args2 += ["--format", "{{artist}} - {{title}}"]
        rc, out, err = _run(args2, timeout=5)
        return {"ok": rc == 0, "output": (out or err)[:400]}
    if action == "lock":
        for cmd in (["omarchy-lock-screen"], ["hyprlock"], ["loginctl", "lock-session"]):
            if _which(cmd[0]):
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"ok": True}
        return {"ok": False, "error": "lock unavailable"}
    if action == "wake":
        if _which("hyprctl"):
            _run(["hyprctl", "dispatch", "dpms", "on"], timeout=5)
            return {"ok": True}
        return {"ok": False, "error": "wake unavailable"}
    return {"ok": False, "error": f"unsupported action: {action}"}


def tool_timer_reminder(args):
    # Delegate complex timer/reminder state handling to local-voice-exec by phrasing.
    action = (args.get("action") or "").lower()
    minutes = args.get("minutes")
    label = str(args.get("label") or "").strip()
    when = str(args.get("when") or "").strip()
    exe = BIN_DIR / "local-voice-exec"
    phrase = ""
    if action == "set_timer":
        m = float(minutes or 5)
        phrase = f"set a timer for {m:g} minutes" + (f" for {label}" if label else "")
    elif action == "list_timers":
        phrase = "timer status"
    elif action == "pause_timer":
        phrase = f"pause {label} timer" if label else "pause timer"
    elif action == "resume_timer":
        phrase = f"resume {label} timer" if label else "resume timer"
    elif action == "cancel_timer":
        phrase = f"cancel {label} timer" if label else "cancel timer"
    elif action == "set_reminder":
        if when:
            phrase = f"remind me at {when} to {label or 'something'}"
        else:
            m = float(minutes or 5)
            phrase = f"remind me in {m:g} minutes to {label or 'something'}"
    elif action == "list_reminders":
        phrase = "show my reminders"
    if phrase and exe.exists():
        rc, out, err = _run([str(exe), phrase], timeout=20)
        return {"ok": rc == 0, "output": out or err or "Done."}
    return {"ok": False, "error": "timer/reminder unavailable"}


def tool_web_lookup(args):
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "missing query"}
    # Reuse DDG logic from local-voice-exec by calling it in a lightweight way? No direct API.
    # Implement minimal DDG instant+snippets here.
    try:
        from urllib.parse import quote_plus
        import html as _html
        headers = {"User-Agent": "omarchy-siri/1.0"}
        parts = []
        # instant answer
        url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_redirect=1&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        text = (data.get("AbstractText") or data.get("Answer") or "").strip()
        if text:
            parts.append(text)
        # snippets
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 omarchy-siri/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            page = resp.read().decode("utf-8", "replace")
        bits = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.DOTALL) or re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|div|span)>', page, re.DOTALL)
        for b in bits[:3]:
            t = _html.unescape(re.sub(r"<[^>]+>", " ", b))
            t = re.sub(r"\s+", " ", t).strip()
            if len(t) > 30:
                parts.append(t)
        out = " ".join(parts)[:1800]
        return {"ok": True, "text": out}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}




def _json_type_ok(expected, value):
    if isinstance(expected, list):
        return any(_json_type_ok(e, value) for e in expected)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _tool_schema(name):
    for t in TOOLS:
        fn = t.get("function") or {}
        if fn.get("name") == name:
            return fn.get("parameters") or {}
    return None


def _validate_tool_call(name, args):
    """Return (ok, errors[]) for tool_call against TOOLS schema."""
    errors = []
    schema = _tool_schema(name)
    if schema is None:
        return False, [f"unknown tool: {name}"]
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return False, ["arguments must be an object"]
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    for req in required:
        if req not in args:
            errors.append(f"missing required argument: {req}")
    for k, v in args.items():
        if k not in props:
            continue
        spec = props.get(k) or {}
        if "enum" in spec and v not in spec.get("enum", []):
            errors.append(f"invalid value for {k}")
            continue
        t = spec.get("type")
        if t and v is not None and not _json_type_ok(t, v):
            errors.append(f"invalid type for {k}: expected {t}")
    return (len(errors) == 0), errors


TOOL_IMPL = {
    "get_calendar": tool_get_calendar,
    "get_canvas": tool_get_canvas,
    "get_weather": tool_get_weather,
    "docker_status": tool_docker_status,
    "docker_restart": tool_docker_restart,
    "hyprland_dispatch": tool_hyprland_dispatch,
    "hypruse": tool_hypruse,
    "clipboard": tool_clipboard,
    "system_control": tool_system_control,
    "timer_reminder": tool_timer_reminder,
    "web_lookup": tool_web_lookup,
}


def _exec_tool(name, args):
    fn = TOOL_IMPL.get(name)
    if not fn:
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        return fn(args or {})
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ---- Model calls with tool support ----

def _openai_chat(base_url, api_key, model, messages, tools, timeout=20, max_tokens=500):
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ollama_chat(model, messages, tools, timeout=30, max_tokens=500):
    url = "http://127.0.0.1:11434/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.3, "num_predict": max_tokens},
    }
    if tools:
        payload["tools"] = tools
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_agent(prompt: str, history=None, context: str = "", max_steps: int = 3):
    history = history or []
    system = (
        "You are Ruby, a helpful local-first voice assistant on Omarchy Linux. "
        "Use tools when needed to complete the user's request across windows/workspaces, "
        "UI control (hypruse), Docker, calendar, Canvas, weather, timers/reminders, clipboard, "
        "system/audio/Bluetooth controls, memory search, web lookup, vision, and car logs. "
        "You may chain multiple tool calls in steps. "
        "Be accurate and safe: never perform destructive power actions; docker_restart requires confirmed=true only if user explicitly confirmed; "
        "hypruse must not be used in unsafe contexts (it will refuse). "
        "After using tools, give a concise, clear spoken response (2-5 short sentences), no markdown, no URLs, no bullet lists."
    )
    if context:
        system += f"\nContext:\n{context[:2000]}"
    messages = [{"role": "system", "content": system}]
    for m in history[-10:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            messages.append({"role": m["role"], "content": str(m.get("content", ""))[:1200]})
    messages.append({"role": "user", "content": prompt})

    cloud = _cloud_conf()
    local_model = _local_model()
    actions = []

    def try_chat(use_cloud: bool):
        if use_cloud and cloud["enabled"] and cloud["key"]:
            return _openai_chat(cloud["base_url"], cloud["key"], cloud["model"], messages, TOOLS, timeout=20)
        return _ollama_chat(local_model, messages, TOOLS, timeout=35)

    used_cloud = bool(cloud["enabled"] and cloud["key"])
    invalid_retries = 0
    tools_disabled = False
    for step in range(max_steps):
        try:
            if tools_disabled:
                if used_cloud and cloud["enabled"] and cloud["key"]:
                    resp = _openai_chat(cloud["base_url"], cloud["key"], cloud["model"], messages, None, timeout=20)
                else:
                    resp = _ollama_chat(local_model, messages, None, timeout=35)
            else:
                resp = try_chat(used_cloud)
        except Exception as e:
            if used_cloud:
                used_cloud = False
                try:
                    if tools_disabled:
                        resp = _ollama_chat(local_model, messages, None, timeout=35)
                    else:
                        resp = try_chat(False)
                except Exception as e2:
                    return {"ok": False, "error": str(e2)[:200], "actions": actions}
            else:
                try:
                    if used_cloud and cloud["enabled"] and cloud["key"]:
                        resp = _openai_chat(cloud["base_url"], cloud["key"], cloud["model"], messages, None, timeout=20)
                    else:
                        resp = _ollama_chat(local_model, messages, None, timeout=30)
                except Exception as e2:
                    return {"ok": False, "error": str(e2)[:200], "actions": actions}

        # Normalize OpenAI vs Ollama response shapes
        msg = None
        tool_calls = []
        content = ""
        if isinstance(resp, dict) and "choices" in resp:
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            content = (msg.get("content") or "").strip()
        elif isinstance(resp, dict) and "message" in resp:
            msg = resp.get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            content = (msg.get("content") or "").strip()
        else:
            msg = {}

        if tool_calls and not tools_disabled:
            parsed_calls = []
            validation_errors = []
            for tc in tool_calls[:4]:
                fn = (tc.get("function") or {})
                name = fn.get("name") or tc.get("name") or ""
                raw_args = fn.get("arguments") if "function" in tc else tc.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args) if raw_args.strip() else {}
                    except Exception:
                        args = None
                elif isinstance(raw_args, dict):
                    args = raw_args
                elif raw_args is None:
                    args = {}
                else:
                    args = None
                if args is None:
                    ok, errs = False, ["arguments must be valid JSON object"]
                    parsed_calls.append((tc, name, {}, ok, errs))
                    validation_errors.extend([f"{name}: {e}" for e in errs])
                else:
                    ok, errs = _validate_tool_call(name, args)
                    parsed_calls.append((tc, name, args, ok, errs))
                    if not ok:
                        validation_errors.extend([f"{name}: {e}" for e in errs])

            if validation_errors:
                invalid_retries += 1
                messages.append(msg if msg else {"role": "assistant", "content": content, "tool_calls": tool_calls})
                err_text = "Invalid tool call arguments: " + "; ".join(validation_errors[:6])
                if invalid_retries <= 1:
                    # Retry once: ask model to fix args.
                    for tc, name, args, ok, errs in parsed_calls:
                        tool_call_id = tc.get("id") or f"call_{step}_invalid_{len(actions)}"
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "name": name or "unknown",
                            "content": json.dumps({"ok": False, "error": err_text, "hint": "Fix arguments and try again once."}),
                        })
                    continue
                # After one retry, answer without the tool.
                tools_disabled = True
                for tc, name, args, ok, errs in parsed_calls:
                    tool_call_id = tc.get("id") or f"call_{step}_invalid_{len(actions)}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": name or "unknown",
                        "content": json.dumps({"ok": False, "error": "Tool unavailable due to invalid arguments."}),
                    })
                messages.append({"role": "user", "content": "Tools are unavailable for this request now. Answer the user's request directly and briefly without calling tools."})
                continue

            # Execute validated tool calls.
            messages.append(msg if msg else {"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tc, name, args, ok, errs in parsed_calls:
                result = _exec_tool(name, args)
                actions.append({"tool": name, "args": args, "result": result})
                tool_call_id = tc.get("id") or f"call_{step}_{len(actions)}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False)[:6000],
                })
            continue

        # Final answer
        if content:
            ans = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
            ans = re.sub(r"`([^`]*)`", r"\1", ans)
            ans = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", ans)
            ans = re.sub(r"[*_#>•\-]+", " ", ans)
            ans = re.sub(r"\s+", " ", ans).strip()
            return {"ok": True, "answer": ans[:900], "actions": actions, "cloud": used_cloud}
        break

    return {"ok": False, "error": "no final answer", "actions": actions}


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--json":
        try:
            req = json.loads(sys.argv[2])
        except Exception:
            print(json.dumps({"ok": False, "error": "bad json"}))
            return 1
        res = run_agent(req.get("prompt", ""), history=req.get("history") or [], context=req.get("context") or "")
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok") else 1
    if len(sys.argv) >= 2 and sys.argv[1] == "--tools":
        print(json.dumps(TOOLS, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({"ok": False, "error": "usage: ruby_agent.py --json '{...}'"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
