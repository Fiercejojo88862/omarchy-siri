# 🎙️ Siri of Omarchy

Offline push-to-talk voice assistant for Hyprland (Omarchy/Arch). Press **INS** to talk, press again to send — she transcribes locally, routes intent through a local LLM, and acts: apps, workspaces, windows, weather, timers, reminders, calendar, system controls — all confirmed aloud in a British female voice.

> **Topics:** `hyprland` `voice-assistant` `offline` `whisper` `ollama` `piper` `arch-linux` `omarchy`

## 🧠 How it works

```text
INS ──▶ pw-record (16k mono) ──▶ whisper-cli (base.en, offline)
       ──▶ deterministic skills / Ollama qwen2.5:3b (intent + answers)
       ──▶ hyprctl dispatch (Lua) / apps / spoken answer
       ──▶ popup + Piper southern-English female speaks the response
```

Local-first and offline-capable. Core control, STT, TTS, translation, and Q&A run locally by default. Optional network features are used only when needed/enabled: weather/calendar/Canvas/Shazam as before, quick web lookups for “search … and summarize/tell me”, and an optional **cloud brain** (off by default, your API key) for faster open-ended answers with local fallback. No audio leaves the machine.

## 📦 What's included

* `bin/local-voice` — toggle recorder (start/stop, instant, detached fds)
* `bin/local-voice-exec` — Ollama intent router (apps, Hyprland 0.56 `hl.dsp.*`, tasks, gated power actions, conversational)
* `bin/local-voice-exec` — computer-use via hypruse: "what do you see", "click Save", "scroll down", "press Escape", "type X and press enter", "open firefox on workspace 3"
* Chitchat: rotating jokes ("tell me a joke"), fun facts, and friendly answers (compliments, favorites, "will you marry me") — all offline, all in her voice
* `bin/local-voice-tts` / `local-voice-say` — Siri voice: Piper `en_GB-southern_english_female-low` (offline, sub-second, mastered to 48k/24-bit)
* `bin/local-voice-calendar` — merged iCloud home+work feeds (today/tomorrow/next)
* `bin/local-voice-translate` — offline translation (local Ollama by default; LibreTranslate or argos opt-in)
* `bin/apple-calendar` — native GTK4 agenda viewer (no browser)
* `hypr/bindings-voice.lua` — push-to-talk and computer-use panic keys
* `config/*.example` — calendar, weather, translation, cloud, and persona templates (`ASSISTANT_NAME`, default Ruby)

## 🚀 Install

```bash
git clone https://github.com/Fiercejojo88862/omarchy-siri.git ~/omarchy-siri
cd ~/omarchy-siri
chmod +x install.sh
./install.sh
```

Needs: Hyprland 0.56+, `whisper-cli`, `ollama` (+`qwen2.5:3b` pulled separately), PipeWire, ffmpeg, Python 3.10+, Piper voices (~30MB, fetched by `install.sh`). Computer-use needs `hypruse` (`pip install --user hypruse`, handled by `install.sh`; check with `uvx hypruse doctor`).

## 🖱️ Computer use

Hyprland-native desktop control through hypruse (no root, no ydotool, no portals — same Wayland protocols as `wlrctl`). Semantic first: she reads the window tree + named controls, never guessed pixels.

| Say | Does |
|---|---|
| *"what do you see" / "what's open"* | lists windows + focused app's clickable controls |
| *"click Save" / "press Login"* | clicks the named control (`click_ui`) |
| *"scroll down / up"* | scrolls at the cursor |
| *"press Escape / Control S"* | key combo via `wtype` path |
| *"type hello and press enter"* | types, then Enter |
| *"open firefox on workspace 3"* | launches placed on that workspace |

Safety: locked sessions, password fields and system auth dialogs always refuse (`allow_auth` is never passed). Panic key: `SUPER+ALT+Backspace` → `pkill -f hypruse` (`SUPER+SHIFT+Backspace` is already Omarchy's "toggle window gaps"). Read-only rehearsal: `HYPRUSE_DRYRUN=1`; audit log: `HYPRUSE_JOURNAL=1`.

Add the keybinding to `~/.config/hypr/bindings.lua`, then `hyprctl reload`:

```lua
o.bind("Insert", "Ruby: talk / send", "local-voice toggle")
```

## 🔊 Voice

Siri speaks with Piper `en_GB-southern_english_female-low` — stock British female voice pitch-matched to your recording (median F0/range overlap beat jenny, cori and alba on identical text). Fully offline, sub-second replies, no warm-up, no background jobs. Other British female options already on disk: `en_GB-jenny_dioco-medium`, `en_GB-cori-high`, `en_GB-alba-medium` — point `VOICE_MODEL` at one in `bin/local-voice-tts` to switch.

## 📅 Calendar

Copy `config/calendar.conf.example` to `~/.config/voice-assistant/calendar.conf` (`chmod 600`) and add iCloud public share links (`ICS_URL_HOME`/`ICS_URL_WORK`). Ask *"what's on my calendar"*, *"work calendar"*, *"when is my next appointment"*. Bearer URLs stay on disk only.

## 🧠 AI answers & web

* **Questions:** ask anything (“what is photosynthesis?”, “who was Ada Lovelace?”) — Ruby answers in a short spoken response. By default this uses local Ollama (`qwen2.5:3b`); if you enable the **cloud brain**, she tries your OpenAI-compatible provider first (fast) and falls back to local on error/quota.
* **Web:** “search the web for X” opens DuckDuckGo results in your browser. “search the web for X and tell me / summarize” fetches quick web context and speaks a summary.
* **Cloud brain (optional, off by default):** configure `~/.config/voice-assistant/cloud.conf` (chmod 600) with your key/endpoint/model. Voice controls: “cloud brain on/off”, “how many cloud requests today”. Daily cap + cooldown; over limit she says “You've reached your generation limit.” once per cooldown and answers locally.
* **Speed tuning:** `VOICE_STOP_GRACE` (default 0.3s, capture tail of speech before stop) and `VOICE_SPEAK_DELAY` (default 0.15s, pause before playback) — set in your environment to trade latency vs cut-off/overlap.

## 🔒 Privacy

`.gitignore` blocks recordings, models, `calendar.conf`, `cloud.conf`, caches, tokens, and state. The router refuses shell/file-destructive output; shutdown/reboot/logout ask for a spoken `yes` first.

Translation is local (`translate.conf`), never Google Translate. Network use is explicit: weather/calendar/Canvas/Shazam as configured, web lookups when you ask to search-and-summarize, and cloud Q&A only when `CLOUD_ENABLED=1` with your key. No audio leaves the machine. (`local-voice-exec --doctor` is planned.)

## 🧪 Tests

`local-voice-exec --selftest` replays 71 phrases through the real router and compares the result to `tests/golden.txt`. It runs in a throwaway `HOME`, in rehearsal mode: no process is launched, no URL fetched, no audio synthesized, hypruse is never imported. Every case prints what would be said, popped up, launched or opened, plus a `HANDLER` line naming the skill that claimed it (or `LLM` when it fell through to the model).

```sh
local-voice-exec --selftest            # PASS/FAIL against the golden file
local-voice-exec --selftest --update   # accept the current output as the golden
```

Clock, date, free disk and note timestamps are masked; everything else must match byte-for-byte, which is how refactors are proven to change nothing.

## 📄 License

MIT (repo code). Piper voices MIT; whisper.cpp MIT.
