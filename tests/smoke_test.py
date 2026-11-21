#!/usr/bin/env python3
"""Smoke-test regression net for Ruby (Stage 1b).

Runs ~20 canonical voice commands in rehearsal mode (no side effects) and
verifies expected routing/handler behavior. Every later stage must pass this
before its commit.

Usage:
  python3 tests/smoke_test.py
  python3 tests/smoke_test.py --verbose
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXEC = REPO / "bin" / "local-voice-exec"

# (command, expected_substrings_any_of_these_lists)
# Each inner tuple means: at least one of these substrings must appear in output.
CASES = [
    ("volume 30", [("HANDLER\tmedia", "HANDLER\tdevice", "LLM\tqwen2.5:3b", "volume")]),
    ("mute", [("HANDLER\tmedia", "HANDLER\tdevice", "mute")]),
    ("unmute", [("HANDLER\tmedia", "HANDLER\tdevice", "mute")]),
    ("stop talking", [("HANDLER\tmoods", "HANDLER\tconversational", "Silent", "Quiet")]),
    ("what time is it", [("HANDLER\ttime_date", "HANDLER\ttime")]),
    ("what's the date", [("HANDLER\ttime_date", "HANDLER\ttime")]),
    ("set a timer for 10 minutes for pizza", [("HANDLER\ttimers", "HANDLER\ttimer")]),
    ("how much time left on my timer", [("HANDLER\ttimers", "HANDLER\ttimer")]),
    ("what's the weather", [("HANDLER\tweather",)]),
    ("open firefox", [("HANDLER\tlaunch", "LAUNCH\tfirefox")]),
    ("open terminal", [("HANDLER\tlaunch", "LAUNCH\tkitty", "LAUNCH\tgnome-terminal", "LAUNCH\talacritty")]),
    ("lock the screen", [("HANDLER\tsystem", "lock")]),
    ("take a note: buy milk", [("HANDLER\tnotes", "Noted")]),
    ("show my notes", [("HANDLER\tnotes", "Notes")]),
    ("remind me in 5 minutes to check the oven", [("HANDLER\treminder", "HANDLER\ttimers")]),
    ("what's on my calendar", [("HANDLER\tcalendar",)]),
    ("check my containers", [("HANDLER\tdocker",)]),
    ("next song", [("HANDLER\tmedia",)]),
    ("brightness 40", [("HANDLER\tdisplay", "HANDLER\tdevice", "LLM\tqwen2.5:3b")]),
    ("hi", [("HANDLER\tconversational",)]),
]


def run_case(cmd: str) -> str:
    env = os.environ.copy()
    env["VOICE_REHEARSAL"] = "1"
    env["VOICE_DRYRUN"] = "1"
    # throwaway state/home-ish dirs to keep deterministic
    with tempfile.TemporaryDirectory(prefix="ruby-smoke-") as td:
        fake_home = os.path.join(td, "home")
        fake_run = os.path.join(td, "run")
        os.makedirs(fake_home, exist_ok=True)
        os.makedirs(fake_run, exist_ok=True)
        # Minimal HOME layout; symlink repo/bin and common config defaults.
        os.makedirs(os.path.join(fake_home, ".local", "bin"), exist_ok=True)
        os.makedirs(os.path.join(fake_home, ".local", "state", "voice-assistant"), exist_ok=True)
        os.makedirs(os.path.join(fake_home, ".config", "voice-assistant"), exist_ok=True)
        # Symlink executables used by resolution paths.
        src_bin = Path.home() / ".local" / "bin"
        dst_bin = Path(fake_home) / ".local" / "bin"
        if src_bin.is_dir():
            for f in src_bin.iterdir():
                tgt = dst_bin / f.name
                if not tgt.exists():
                    try:
                        tgt.symlink_to(f)
                    except Exception:
                        pass
        env["HOME"] = fake_home
        env["XDG_RUNTIME_DIR"] = fake_run
        env["XDG_STATE_HOME"] = os.path.join(fake_home, ".local", "state")
        p = subprocess.run(
            [sys.executable, str(EXEC), cmd],
            capture_output=True, text=True, env=env, timeout=25,
        )
        return (p.stdout or "") + "\n" + (p.stderr or "")


def main() -> int:
    verbose = "--verbose" in sys.argv
    if not EXEC.exists():
        print(f"FAIL: missing {EXEC}", file=sys.stderr)
        return 2
    failures = []
    for cmd, groups in CASES:
        out = run_case(cmd)
        ok = True
        for group in groups:
            if not any(tok in out for tok in group):
                ok = False
                break
        if ok:
            if verbose:
                print(f"PASS  {cmd}")
        else:
            failures.append((cmd, out))
            print(f"FAIL  {cmd}")
            if verbose:
                print(out[:500])
    print(f"\n{len(CASES)-len(failures)}/{len(CASES)} passed")
    if failures:
        for cmd, out in failures[:5]:
            print(f"--- {cmd} ---")
            print(out[:400])
        return 1
    print("smoke-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
