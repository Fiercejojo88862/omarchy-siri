#!/usr/bin/env python3
"""Vision-enhanced screen inspection for Ruby.

Captures the active screen with grim, downscales to max width 1280, sends to a
vision-capable model (Groq cloud when enabled, else local Ollama), and returns
a concise spoken-friendly description/answer.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "voice-assistant"
CONFIG_DIR = Path.home() / ".config/voice-assistant"
CLOUD_CONF = CONFIG_DIR / "cloud.conf"
VOICE_CONF = CONFIG_DIR / "voice.conf"


def _read_kv(path: Path):
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
    c = _read_kv(CLOUD_CONF)
    return {
        "enabled": c.get("CLOUD_ENABLED", "0").lower() in ("1", "true", "yes", "on"),
        "key": c.get("CLOUD_API_KEY", ""),
        "base_url": (c.get("CLOUD_BASE_URL", "https://api.groq.com/openai/v1") or "").rstrip("/"),
        "model": c.get("CLOUD_VISION_MODEL") or c.get("CLOUD_MODEL") or "llama-3.2-11b-vision-preview",
    }


def _local_vision_model():
    c = _read_kv(VOICE_CONF)
    return c.get("VISION_MODEL", "qwen2.5vl:7b") or "qwen2.5vl:7b"


def _is_session_locked() -> bool:
    # Mirror hypruse guard: refuse capture when session is locked.
    try:
        sid = os.environ.get("XDG_SESSION_ID") or ""
        if sid:
            r = subprocess.run(
                ["loginctl", "show-session", sid, "-p", "LockedHint", "--value"],
                capture_output=True, text=True, timeout=2,
            )
            if (r.stdout or "").strip().lower() == "yes":
                return True
    except Exception:
        pass
    for name in ("hyprlock", "swaylock", "gtklock", "waylock"):
        try:
            r = subprocess.run(["pgrep", "-x", name], capture_output=True, timeout=2)
            if r.returncode == 0:
                return True
        except Exception:
            pass
    return False


def _password_field_focused() -> bool:
    # Best-effort mirror of hypruse guard for password/auth contexts.
    # 1) Ask hypruse describe if available (it refuses in unsafe contexts).
    try:
        if shutil.which("hypruse"):
            r = subprocess.run(["hypruse", "describe"], capture_output=True, text=True, timeout=6)
            out = ((r.stdout or "") + "\n" + (r.stderr or "")).lower()
            if r.returncode != 0 and any(s in out for s in ("password", "locked", "auth", "authentication", "secure")):
                return True
    except Exception:
        pass
    # 2) Common auth dialog/window heuristics via hyprctl.
    try:
        if shutil.which("hyprctl"):
            r = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=2)
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout)
                blob = " ".join(str(data.get(k, "")) for k in ("title", "class", "initialTitle", "initialClass")).lower()
                if any(s in blob for s in ("password", "authenticate", "authentication", "polkit", "pinentry", "unlock", "passphrase")):
                    return True
    except Exception:
        pass
    return False


def _vision_guard_error() -> str | None:
    if _is_session_locked():
        return "session is locked"
    if _password_field_focused():
        return "password field or secure auth prompt is focused"
    return None


def capture_screen(max_width=1280):
    guard = _vision_guard_error()
    if guard:
        return None, guard
    RUNTIME.mkdir(parents=True, exist_ok=True)
    try:
        RUNTIME.chmod(0o700)
    except Exception:
        pass
    out = RUNTIME / "screen.jpg"
    if out.exists():
        try:
            out.unlink()
        except Exception:
            pass
    if not shutil.which("grim"):
        return None, "grim is not installed"
    # Capture full screen (grim). Downscale via ffmpeg if available.
    tmp = RUNTIME / "screen_raw.png"
    try:
        if tmp.exists():
            tmp.unlink()
    except Exception:
        pass
    try:
        r = subprocess.run(["grim", str(tmp)], capture_output=True, text=True, timeout=8)
        if r.returncode != 0 or not tmp.exists():
            # try active output via slurp-like? grim without args captures all; fallback ways.
            return None, "screen capture failed"
        if shutil.which("ffmpeg"):
            cmd = [
                "ffmpeg", "-v", "error", "-y", "-i", str(tmp),
                "-vf", f"scale='min({max_width},iw)':-2",
                "-q:v", "4", str(out)
            ]
            r2 = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r2.returncode != 0 or not out.exists():
                # fallback: convert without scale
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-q:v", "4", str(out)],
                               capture_output=True, text=True, timeout=10)
        else:
            # no ffmpeg; use raw png as jpg path? just rename/copy - vision APIs may accept png but we say jpg
            try:
                shutil.copyfile(tmp, out)
            except Exception:
                return None, "could not prepare screenshot"
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        if not out.exists() or out.stat().st_size < 1000:
            return None, "screen capture was empty"
        return out, None
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return None, str(e)[:120]


def _speakable(text: str, limit: int = 700) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_#>•\-]+", " ", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text.rfind(". ", 0, limit)
        text = (text[:cut + 1] if cut > 120 else text[: limit - 1].rstrip() + "…")
    return text


def ask_vision(prompt: str, image_path: Path) -> tuple[str | None, str | None]:
    try:
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    except Exception as e:
        return None, f"could not read screenshot: {e}"
    data_url = "data:image/jpeg;base64," + b64

    cloud = _cloud_conf()
    if cloud["enabled"] and cloud["key"]:
        url = cloud["base_url"] + "/chat/completions"
        payload = {
            "model": cloud.get("model") or "llama-3.2-11b-vision-preview",
            "messages": [
                {"role": "system", "content": "You are Ruby, a helpful voice assistant. Describe what you see or answer the user's question about the screen clearly in 2-5 short sentences for speech. No markdown, no bullet lists, no URLs."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt[:1000]},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
            "temperature": 0.3,
            "max_tokens": 350,
        }
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cloud['key']}",
            })
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if text:
                return _speakable(text), None
        except Exception:
            pass
        # fall through to local

    model = _local_vision_model()
    # Try Ollama chat with images (common format)
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": "You are Ruby, a helpful voice assistant. Describe what you see or answer the user's question about the screen clearly in 2-5 short sentences for speech. No markdown, no bullet lists, no URLs."},
            {"role": "user", "content": prompt[:1000], "images": [b64]},
        ],
        "options": {"temperature": 0.3, "num_predict": 300},
    }
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (data.get("message", {}).get("content") or "").strip()
        if text:
            return _speakable(text), None
    except Exception as e:
        return None, str(e)[:120]
    return None, "no vision answer"


def main():
    prompt = " ".join(sys.argv[1:]).strip() or "What is on my screen? Describe the important visible content."
    img, err = capture_screen()
    if not img:
        print(json.dumps({"ok": False, "error": err or "capture failed"}))
        return 1
    try:
        ans, verr = ask_vision(prompt, img)
    finally:
        try:
            img.unlink(missing_ok=True)
        except Exception:
            pass
    if ans:
        print(json.dumps({"ok": True, "answer": ans}))
        return 0
    print(json.dumps({"ok": False, "error": verr or "vision failed"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
