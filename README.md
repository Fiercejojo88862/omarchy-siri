# Ruby — Siri of Omarchy

<p align="center">
  <img src="assets/ruby-assistant.png" alt="Ruby, voice assistant for Omarchy" width="320" />
</p>

**Ruby** is a local-first voice assistant for Hyprland on Omarchy/Arch.  
Press **Insert** to talk (push-to-talk), or say **“Hey Ruby”** for hands-free wake-word mode. Ruby transcribes locally, routes commands through fast deterministic skills and/or an LLM (with native tool-calling), and acts on your desktop — then confirms with notifications and spoken replies.

> Topics: `hyprland` `voice-assistant` `offline` `whisper` `ollama` `piper` `arch-linux` `omarchy`

## Features

### Voice & interaction
- **Push-to-talk (PTT):** `Insert` toggle to record/send (`local-voice`).
- **Wake word:** optional “Hey Ruby” listener (`local-voice-wake`) with configurable phrases/timing, wake acknowledgment sound, and ~300ms listen gate after wake.
- **Local speech pipeline:** Whisper (`whisper-cli`) for STT and Piper for offline TTS (customizable voice/timing in `voice.conf` / Ruby Engine).
- **Shared audio state machine:** `idle`, `wake`, `listening`, `thinking`, `speaking`, `barging` across voice components.
- **Barge-in (optional):** interrupt TTS when you speak, flush playback queue (best-effort), and return to listening (`ruby-bargein-monitor`, `BARGE_IN` toggle).
- **PipeWire echo-cancellation helper:** `ruby-aec-setup` for improved barge-in behavior (best-effort runtime/persistent setup guidance).

### LLM routing & tools
- **Native tool-calling agent:** Groq/OpenAI-compatible or local Ollama (`bin/ruby_agent.py`) with structured tool schemas, schema validation (retry once on invalid args, then answer without tool), and **2–3 hop** tool-execution cap.
- **OpenCode agent brain:** choose **OpenCode local agent** in Ruby Engine → Cloud Brain. Ruby runs a bundled agent with desktop-control, screen-vision and memory skills, and falls back to local Ollama on error (`bin/ruby_opencode.py`).
- **Three agent postures:** `ruby` (allowlisted desktop tools, no shell or edits), `ruby-shell` (trusted full shell + coding CLIs, denylisted but **not sandboxed**), and `ruby-dev` (edits in `OPENCODE_CWD`, no push/sudo/packages).
- **Coding-agent delegation:** `bin/ruby_delegate.py` detects and runs any installed coding CLI — OpenCode, Claude Code, Codex, Cursor Agent, Aider, Gemini — in the foreground or detached with a completion notification.
- **Deterministic fast paths preserved:** existing regex/skill handlers remain first-class for quick, reliable commands.
- **Short in-progress filler:** brief spoken feedback while tool-calling chains run.
- **Desktop tools:** `desktop_context` (active window/workspaces/windows), notes, notifications, and updated `hypruse` verbs for semantic clicks, typing, keys and scrolling.

### Memory & notes
- **SQLite-backed notes/logs:** `~/.local/share/voice-assistant/memory.db` for notes and auto-tagged logs (`#gym`, `#car`, `#money`, `#school`).
- **Semantic + keyword search:** local Ollama embeddings with **FTS5 fallback**; embedding model stored per vector to avoid mismatched-model comparisons.
- **Legacy migration:** automatic migration from legacy `~/.local/state/voice-assistant/notes.md`.
- **Natural memory queries:** retrieve/summarize saved notes and logs via handlers and the `search_memory` tool.
- **Debug transcript log:** `~/.local/share/voice-assistant/transcript.jsonl` (heard text, intent/route, tool calls, response).

### Desktop control & assistance
- **Hyprland/system actions:** app/window/workspace/media/display/system controls through local skills/dispatch (environment-dependent capabilities).
- **Computer use (hypruse):** semantic screen/control interaction where available (e.g. describe, click controls, scroll, keys, type/launch workflows) with safety refusals in sensitive contexts.
- **Vision-enhanced screen inspection:** `ruby_vision.py` (`grim` + vision LLM, cloud or local Ollama vision model), **1280px downscale**, **delete-after-inference**, and safety guards that skip capture when session is locked or a password/auth field is focused.
- **Timers/reminders, weather, calendar, clipboard, translation, web lookups, and more:** local-first skills with optional network features only where configured/needed.

### Proactive & integrations
- **Watcher daemon (`ruby-watcher`):** proactive alerts/summaries (calendar event reminders, unexpected Docker container exits, Canvas/NJIT due-soon alerts when available, unlock brief after extended idle/lock) with quiet/DND respect and **persisted last-alerted timestamps** to avoid re-firing after restarts. The unlock brief can be written by the configured brain from live calendar/container facts (`BRIEF_BRAIN=1`) and falls back to the built-in template.
- **LubeLogger integration (optional):** send `#car` logs to LubeLogger when configured; **vehicle ID validated before any POST**, `integrations.conf` treated as sensitive (`0600`), and **local SQLite logging remains the fallback/source of truth** on failure/unconfigured/invalid ID.

### Apps & UI
- **Ruby Engine:** GTK4/libadwaita settings app (`bin/ruby-engine`) for voice, persona, wake word, cloud brain, and related controls (including Stage 4 audio/wake controls).
- **Desktop assets:** assistant/app icons and launcher integration (`applications/ruby-engine.desktop`, `assets/`, `icons/`).

## What’s included

- **Core:** `bin/local-voice`, `bin/local-voice-exec`, `bin/local-voice-wake`, `bin/local-voice-tts`, `bin/local-voice-say`, `bin/local-voice-translate`
- **LLM & memory:** `bin/ruby_agent.py`, `bin/ruby_opencode.py`, `bin/ruby_memory.py`
- **OpenCode brain assets:** `integrations/opencode/agents/{ruby,ruby-shell,ruby-dev}.md` and `ruby-{desktop-control,screen-vision,memory,coding-agents}` skills (installed by `install.sh`)
- **Coding delegation:** `bin/ruby_delegate.py`
- **Vision & audio helpers:** `bin/ruby_vision.py`, `bin/ruby-visual`, `bin/ruby-bargein-monitor`, `bin/ruby-aec-setup`
- **Proactive/integrations:** `bin/ruby_watcher.py`, `bin/ruby_integrations.py`, `systemd/user/ruby-watcher.service`
- **UI/apps:** `bin/ruby-engine`, `applications/ruby-engine.desktop`
- **Configs/templates:** `config/*.conf.example` (persona, voice, wake, cloud, weather, calendar, translate, watcher, integrations)
- **Bindings & install:** `hypr/bindings-voice.lua`, `install.sh`
- **Tests:** `tests/golden.txt`, `tests/smoke_test.py`

## Install

```bash
git clone https://github.com/Fiercejojo88862/omarchy-siri.git ~/omarchy-siri
cd ~/omarchy-siri
./install.sh
```

The installer is all-in-one and safe to re-run. It:

- installs the Arch packages Ruby needs (`whisper-cpp`, PipeWire, `ffmpeg`, `libnotify`, `grim`, `wl-clipboard`, GTK4/libadwaita, Ollama, …)
- installs Piper TTS and optionally `hypruse`
- downloads the Whisper model and both Piper voices
- links every script into `~/.local/bin` and creates the desktop entries
- creates any missing configs in `~/.config/voice-assistant/` (never overwrites yours)
- adds the `Insert` push-to-talk binding to `~/.config/hypr/bindings.lua` and reloads Hyprland
- installs and enables `ruby-watcher.service`
- enables wake-word autostart when `WAKE_AUTOSTART=1`
- pulls the local `qwen2.5:3b` model for Ollama (one-time ~2 GB) and verifies the install

`sudo` may ask for your password. Then just press **Insert** to talk.

**Check or repair an existing install at any time:**

```bash
./install.sh --doctor     # report exactly what is missing or broken
./install.sh              # repair it
./install.sh --no-deps    # skip package installs
./install.sh --no-model   # skip the Ollama model download
```

Requirements handled by the installer: Hyprland (0.56+), PipeWire, Python 3.10+, `whisper-cli`, Piper + voices, and Ollama.  
Optional depending on features: `grim`/vision model for screen inspection, `hypruse` for computer-use actions, `songrec` for Shazam fallback, Docker/Canvas/calendar integrations, PipeWire AEC components for best barge-in results.

## Configuration

Copy/edit configs in `~/.config/voice-assistant/` (or run `local-voice-exec --init-config` where supported). Key files:
- `persona.conf`, `voice.conf`, `wake.conf`, `cloud.conf`
- `weather.conf`, `calendar.conf`, `translate.conf`
- `watcher.conf`, `integrations.conf`

**Sensitive configs** (e.g. cloud/integrations) should remain local and permission-restricted (`chmod 600` where applicable).

### Choosing a brain

`cloud.conf` selects what answers open-ended requests:

```ini
CLOUD_ENABLED=1
BRAIN_BACKEND=cloud        # OpenAI-compatible API
# or
BRAIN_BACKEND=opencode     # local OpenCode agent
```

With `opencode`, Ruby always keeps deterministic skills first, then asks the
agent (default 60s budget) and falls back to local Ollama if it fails. Settings:

```ini
OPENCODE_AGENT=ruby        # ruby | ruby-shell | ruby-dev
OPENCODE_MODEL=            # blank = OpenCode's default model
OPENCODE_DELEGATE_AGENT=ruby-dev
OPENCODE_CWD=~/.local/share/voice-assistant/agent
OPENCODE_TIMEOUT=60
OPENCODE_AUTO=1
```

The bundled `ruby` agent can inspect the desktop, click named controls, type,
press keys, open apps on workspaces, read the screen, and save/search notes. Its
permission allowlist denies sudo, package installs, file edits, shell commands
outside the allowlist, and reads of `.env`, SSH/GPG/AWS keys and
`cloud.conf`/`integrations.conf`.

Screen inspection needs a local vision model (or a cloud vision key):

```bash
ollama pull qwen2.5vl:3b
```

Then set `VISION_MODEL=qwen2.5vl:3b` in `voice.conf` (the template already does).

Agents and skills are installed by `install.sh`, and Ruby Engine installs them
when you save the OpenCode backend. To install or repair them manually:

```bash
./bin/ruby_opencode.py --install   # copies agents + skills, backs up edits
./bin/ruby_opencode.py --check     # verify binary, agent, model, cwd
```

### Shell and coding agents

Pick the posture in **Ruby Engine → Cloud Brain → Agent**:

| Agent | Shell | Edits | Use |
|---|---|---|---|
| `ruby` | allowlisted commands only | no | everyday desktop control (default) |
| `ruby-shell` | any command (denylisted basics) | yes | terminal work, delegating to coding CLIs |
| `ruby-dev` | allowlisted dev commands | inside `OPENCODE_CWD` | hands-off coding in one project |

`ruby-shell` is a **trusted mode, not a sandbox**. `--auto` means no prompt
appears, so it can do anything your user can, including deleting files. The
policy denies `sudo`, power actions, `pacman -S`, `mkfs`, disk `dd`, and
`git push`, but that is a guardrail, not a jail. Secret read rules only cover
the file-read tool; a shell command can still read those files. Run it only on a
machine and account you trust with voice commands.

For the native (Ollama/cloud) agent, set `ALLOW_SHELL=1` to expose a `run_shell`
tool. It is capped at 60 seconds and refuses the same destructive commands.

#### Delegating to another coding CLI

`ruby_delegate.py` detects installed agents and runs one non-interactively:

```bash
./bin/ruby_delegate.py --list
./bin/ruby_delegate.py --json --cli auto --cwd ~/proj --prompt "fix the failing tests"
./bin/ruby_delegate.py --json --background --cwd ~/proj --prompt "refactor the parser"
```

`--background` writes a log under `~/.local/state/voice-assistant/delegate/`,
writes a result JSON beside it, and sends a desktop notification on completion.
Ruby's `delegate_coding` tool uses the same helper, so a long coding job does not
hold up the voice request. Unsafe sandbox-bypass flags are opt-in with
`DELEGATE_UNSAFE=1`; leave it off unless the workspace is disposable.

## Usage highlights

- **PTT:** bind/toggle `local-voice toggle` (e.g. `Insert`) in Hyprland bindings.
- **Wake word:** start/enable `local-voice-wake` (and optional autostart) for “Hey Ruby”.
- **Vision screen inspection:** ask “what do you see”, “read what’s on my screen”, or “help me with this error” (requires `grim` and a configured vision model; capture is skipped when locked/password-focused).
- **Memory:** “take a note…”, “log car/gym/money/school…”, “show my notes/logs”, and natural recall/summary queries.
- **Watcher:** configure and enable `ruby-watcher.service` for proactive alerts/briefs.

## Testing

```bash
# Deterministic routing golden tests (rehearsal; no side effects)
./bin/local-voice-exec --selftest

# Stage 1b+ regression smoke tests (canonical voice commands, rehearsal)
python3 tests/smoke_test.py
```

## Privacy & safety notes

- Local-first by design: core STT/TTS/routing/memory can run locally.
- Optional network features are used only when configured/invoked (e.g. weather/calendar/Canvas/web lookups/cloud brain/provider integrations).
- No audio is intended to leave the machine by default; treat API keys/tokens and personal config as secrets (`.gitignore` excludes local state/config artifacts where applicable).
- Vision capture includes safety guards (locked session / password or secure auth focus) and temporary captures are deleted after inference.

## License

MIT (repo code, unless otherwise noted). Upstream components (e.g. Piper voices, whisper.cpp) retain their respective licenses.
