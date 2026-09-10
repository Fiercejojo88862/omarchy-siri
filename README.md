# 🎙️ Siri of Omarchy

Offline push-to-talk voice assistant for Hyprland (Omarchy/Arch). Press **INS** to talk, press again to send — she transcribes locally, routes intent through a local LLM, and acts: apps, workspaces, windows, weather, timers, reminders, calendar, system controls — all confirmed aloud in a cloned British voice.

> **Topics:** `hyprland` `voice-assistant` `offline` `whisper` `ollama` `xtts` `arch-linux` `omarchy`

## 🧠 How it works

```text
INS ──▶ pw-record (16k mono) ──▶ whisper-cli (base.en, offline)
       ──▶ Ollama qwen2.5:3b (intent → JSON) ──▶ hyprctl dispatch (Lua) / apps
       ──▶ popup + XTTS-v2 clone speaks the confirmation
```

100% local except the optional Edge-TTS fallback voice (online). No audio leaves the machine on the default path.

## 📦 What's included

* `bin/local-voice` — toggle recorder (start/stop, instant, detached fds)
* `bin/local-voice-exec` — Ollama intent router (apps, Hyprland 0.56 `hl.dsp.*`, tasks, gated power actions, conversational)
* `bin/local-voice-tts` / `local-voice-say` / `local-voice-clone` — cloned-voice speech with disk cache (repeats play instantly)
* `bin/local-voice-calendar` — merged iCloud home+work feeds (today/tomorrow/next)
* `bin/apple-calendar` — native GTK4 agenda viewer (no browser)
* `hypr/bindings-voice.lua` — one-line keybinding
* `patches/apply-patches.py` — CPU-only fixes for stock coqui-tts
* `config/*.example` — calendar + persona templates

## 🚀 Install

```bash
git clone https://github.com/Fiercejojo88862/omarchy-siri.git ~/omarchy-siri
cd ~/omarchy-siri
chmod +x install.sh
./install.sh
```

Needs: Hyprland 0.56+, `whisper-cli`, `ollama` (+`qwen2.5:3b` pulled separately), PipeWire, ffmpeg, Python 3.10+. First run downloads XTTS-v2 (~2GB, CPML non-commercial license).

Add the keybinding to `~/.config/hypr/bindings.lua`, then `hyprctl reload`:

```lua
o.bind("Insert", "Voice: talk / send", "local-voice toggle")
```

## 🗣️ Clone your voice

Record ~25 seconds of clean speech (mono WAV), save as:

```text
~/.local/share/voice-clone/siri_ref_25s.wav
```

First hearing of each phrase takes ~25s on CPU (backgrounded, never blocks); repeats play instantly from `~/.local/share/voice-clone/cache/`. **Never commit recordings** — voice is biometric (`.gitignore` blocks `*.wav/*.mp3`).

## 📅 Calendar

Copy `config/calendar.conf.example` to `~/.config/voice-assistant/calendar.conf` (`chmod 600`) and add iCloud public share links (`ICS_URL_HOME`/`ICS_URL_WORK`). Ask *"what's on my calendar"*, *"work calendar"*, *"when is my next appointment"*. Bearer URLs stay on disk only.

## 🔒 Privacy

`.gitignore` blocks recordings, models, `calendar.conf`, caches, tokens, and state. The router refuses shell/file-destructive output; shutdown/reboot/logout ask for a spoken `yes` first.

## 📄 License

MIT (repo code). XTTS-v2 model is CPML (free non-commercial); whisper.cpp MIT; Piper MIT.
