# 🎙️ Siri of Omarchy

Offline push-to-talk voice assistant for Hyprland (Omarchy/Arch). Press **INS** to talk, press again to send — she transcribes locally, routes intent through a local LLM, and acts: apps, workspaces, windows, weather, timers, reminders, calendar, system controls — all confirmed aloud in a British female voice.

> **Topics:** `hyprland` `voice-assistant` `offline` `whisper` `ollama` `piper` `arch-linux` `omarchy`

## 🧠 How it works

```text
INS ──▶ pw-record (16k mono) ──▶ whisper-cli (base.en, offline)
       ──▶ Ollama qwen2.5:3b (intent → JSON) ──▶ hyprctl dispatch (Lua) / apps
       ──▶ popup + Piper southern-English female speaks the confirmation
```

100% local, 100% offline. No audio leaves the machine.

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
* `config/*.example` — calendar + persona templates

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
o.bind("Insert", "Voice: talk / send", "local-voice toggle")
```

## 🔊 Voice

Siri speaks with Piper `en_GB-southern_english_female-low` — stock British female voice pitch-matched to your recording (median F0/range overlap beat jenny, cori and alba on identical text). Fully offline, sub-second replies, no warm-up, no background jobs. Other British female options already on disk: `en_GB-jenny_dioco-medium`, `en_GB-cori-high`, `en_GB-alba-medium` — point `VOICE_MODEL` at one in `bin/local-voice-tts` to switch.

## 📅 Calendar

Copy `config/calendar.conf.example` to `~/.config/voice-assistant/calendar.conf` (`chmod 600`) and add iCloud public share links (`ICS_URL_HOME`/`ICS_URL_WORK`). Ask *"what's on my calendar"*, *"work calendar"*, *"when is my next appointment"*. Bearer URLs stay on disk only.

## 🔒 Privacy

`.gitignore` blocks recordings, models, `calendar.conf`, caches, tokens, and state. The router refuses shell/file-destructive output; shutdown/reboot/logout ask for a spoken `yes` first.

Translation is local: "how do you say good morning in Spanish" runs through your own model (`translate.conf`), never Google Translate. Only weather (wttr.in/AccuWeather), your iCloud calendar feed, Canvas and Shazam's fingerprint touch the network — and you can see all of that in `local-voice-exec --doctor`.

## 🧪 Tests

`local-voice-exec --selftest` replays 68 phrases through the real router and compares the result to `tests/golden.txt`. It runs in a throwaway `HOME`, in rehearsal mode: no process is launched, no URL fetched, no audio synthesized, hypruse is never imported. Every case prints what would be said, popped up, launched or opened, plus a `HANDLER` line naming the skill that claimed it (or `LLM` when it fell through to the model).

```sh
local-voice-exec --selftest            # PASS/FAIL against the golden file
local-voice-exec --selftest --update   # accept the current output as the golden
```

Clock, date, free disk and note timestamps are masked; everything else must match byte-for-byte, which is how refactors are proven to change nothing.

## 📄 License

MIT (repo code). Piper voices MIT; whisper.cpp MIT.
