#!/usr/bin/env python3
"""Optional integrations for Ruby (e.g. LubeLogger)."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

CONFIG_DIR = Path.home() / ".config/voice-assistant"
CONF = CONFIG_DIR / "integrations.conf"


def _ensure_conf_permissions(path: Path = CONF) -> None:
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except OSError:
        pass


def _read_integrations():
    data = {"lubelogger": {}}
    if not CONF.exists():
        return data
    # Stage 6: integrations.conf must be chmod 600.
    _ensure_conf_permissions(CONF)
    section = None
    try:
        for line in CONF.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            m = re.match(r"^\[(.+)\]$", line)
            if m:
                section = m.group(1).strip().lower()
                data.setdefault(section, {})
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                k = k.strip().upper()
                v = v.strip().strip("'\"")
                if section:
                    data.setdefault(section, {})[k] = v
    except OSError:
        pass
    return data


def _parse_car_fields(text: str, norm: str):
    # Best-effort extraction for maintenance/fuel logs.
    t = " " + (text or "") + " "
    n = norm or text.lower()
    fields = {"type": "maintenance", "description": text.strip()[:240], "odometer": None, "cost": None, "notes": text.strip()[:500]}

    # odometer: 142,500 miles / 142500 mi / 142.5k miles
    m = re.search(r"\b(\d[\d,]*(?:\.\d+)?)\s*k\s*(?:miles|mi|km)?\b", t, re.I)
    if m:
        try:
            fields["odometer"] = int(float(m.group(1).replace(",", "")) * 1000)
        except Exception:
            pass
    if fields["odometer"] is None:
        m = re.search(r"\b(\d[\d,]{2,})\s*(?:miles|mi|km)\b", t, re.I)
        if m:
            try:
                fields["odometer"] = int(float(m.group(1).replace(",", "")))
            except Exception:
                pass
    # cost
    m = re.search(r"\$\s*(\d+(?:\.\d{1,2})?)", t)
    if not m:
        m = re.search(r"\b(\d+(?:\.\d{1,2})?)\s*(?:dollars|bucks)\b", t, re.I)
    if m:
        try:
            fields["cost"] = float(m.group(1))
        except Exception:
            pass

    if re.search(r"\b(gas|fuel|filled up|fill up|tank)\b", n):
        fields["type"] = "fuel"
    elif re.search(r"\b(oil change|oil|tire|tyre|brake|service|repair|inspection|alignment|maintenance)\b", n):
        fields["type"] = "maintenance"
    return fields


def _valid_vehicle_id(vehicle_id: str) -> bool:
    vid = (vehicle_id or "").strip()
    if not vid:
        return False
    # Stage 6: validate vehicle ID before any POST (conservative safe format).
    return re.fullmatch(r"[A-Za-z0-9_-]{1,64}", vid) is not None


def send_car_log(norm: str = "", text: str = "", parsed: dict | None = None) -> dict:
    """POST a car log to LubeLogger if configured. Returns result dict.

    Stage 6: validates vehicle ID before any POST; never blocks local fallback.
    """
    if os.environ.get("VOICE_REHEARSAL") == "1" or os.environ.get("VOICE_DRYRUN") == "1":
        return {"ok": False, "skipped": "rehearsal"}
    cfg = _read_integrations().get("lubelogger", {})
    url = (cfg.get("URL") or "").rstrip("/")
    api_key = cfg.get("API_KEY") or cfg.get("TOKEN") or ""
    vehicle_id = str(cfg.get("VEHICLE_ID") or cfg.get("DEFAULT_VEHICLE_ID") or "").strip()
    if not url:
        return {"ok": False, "skipped": "unconfigured"}
    # Stage 6 requirement: validate vehicle ID before any POST.
    if not _valid_vehicle_id(vehicle_id):
        return {"ok": False, "skipped": "invalid_vehicle_id"}
    fields = parsed or _parse_car_fields(text, (norm or text.lower()))
    payload = {
        "vehicleId": vehicle_id,
        "odometer": fields.get("odometer"),
        "cost": fields.get("cost"),
        "notes": fields.get("notes") or fields.get("description") or text,
        "description": fields.get("description") or text,
        "type": fields.get("type"),
    }
    payload = {k: v for k, v in payload.items() if v is not None and v != ""}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-Api-Key"] = api_key
    # Try a few likely endpoints (vehicle-scoped only, since vehicle ID is required/validated).
    endpoints = [
        f"{url}/api/vehicle/{vehicle_id}/odometerrecords",
        f"{url}/api/vehicle/{vehicle_id}/servicerecords",
        f"{url}/api/vehicle/{vehicle_id}/gasrecords",
    ]
    body = json.dumps(payload).encode("utf-8")
    last_err = None
    for ep in endpoints:
        try:
            req = urllib.request.Request(ep, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=8) as resp:
                status = getattr(resp, "status", 200)
                if 200 <= status < 300:
                    return {"ok": True, "endpoint": ep}
        except Exception as e:
            last_err = str(e)[:120]
            continue
    return {"ok": False, "error": last_err or "unreachable"}


if __name__ == "__main__":
    # CLI: ruby_integrations.py car "<text>"
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "car":
        print(json.dumps(send_car_log(text=" ".join(sys.argv[2:]), norm=" ".join(sys.argv[2:]).lower())))
    else:
        print(json.dumps(_read_integrations()))
