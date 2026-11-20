#!/usr/bin/env bash
# Siri of Omarchy installer: scripts + voice models + desktop entry.
# Safe to re-run. Never touches your recordings; models download on demand.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
CONF="$HOME/.config/voice-assistant"
MODEL_DIR="$HOME/.local/share/whisper-models"
PIPER_DIR="$HOME/.local/share/piper"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m [!]\033[0m %s\n' "$*"; }

# --- 0. OS tools ---------------------------------------------------------------
for t in curl ffmpeg pw-record whisper-cli ollama hyprctl notify-send songrec; do
    command -v "$t" >/dev/null 2>&1 || warn "missing: $t (install it for full function; songrec = Shazam song ID via sudo pacman -S songrec)"
done

# --- 1. Scripts -----------------------------------------------------------------
mkdir -p "$BIN"
for f in local-voice local-voice-exec local-voice-tts local-voice-say \
         local-voice-translate local-voice-calendar apple-calendar apple-calendar-set-icon ruby-engine ruby-visual; do
    ln -sf "$REPO/bin/$f" "$BIN/$f"
    chmod +x "$REPO/bin/$f"
done
info "Scripts linked into $BIN."

# --- 2. Python voice stack ---------------------------------------------------------
# Piper TTS only (offline, sub-second). No torch/coqui chain.
pip install --user --break-system-packages piper-tts

# --- 2b. Computer use (Hyprland-native desktop control via hypruse) -----------
# No sudo needed: user-site install. Doctor via `uvx hypruse doctor`.
pip install --user --break-system-packages hypruse 2>/dev/null || \
warn "hypruse install failed (computer-use clicks/keys need it)"

# --- 3. Speech-to-text model (whisper.cpp, 142MB) ------------------------------------
mkdir -p "$MODEL_DIR"
if [[ ! -f "$MODEL_DIR/ggml-base.en.bin" ]]; then
    info "Downloading whisper base.en model..."
    curl -fsSL -o "$MODEL_DIR/ggml-base.en.bin" \
        https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
else
    info "Whisper model present."
fi

# --- 4. Siri voice (Piper en_GB-southern_english_female-low, offline) ---------
# Pitch-matched to the owner's recording against jenny/cori/alba on
# identical text. Jenny kept as well (previous fallback).
mkdir -p "$PIPER_DIR"
for v in en_GB-southern_english_female-low en_GB-jenny_dioco-medium; do
    if [[ ! -f "$PIPER_DIR/$v.onnx" ]]; then
        info "Downloading Piper voice $v..."
        python3 -m piper.download_voices "$v" --download-dir "$PIPER_DIR"
    fi
done
info "Piper voices present."

# --- 5. Config templates (never overwrite yours) --------------------------------------
mkdir -p "$CONF"
for f in calendar persona weather translate cloud voice; do
    [[ -f "$CONF/$f.conf" ]] || cp "$REPO/config/$f.conf.example" "$CONF/$f.conf"
done
chmod 600 "$CONF"/*.conf 2>/dev/null || true
info "Config templates in $CONF (calendar, persona, weather, translation, cloud, voice)."

# --- 6. Desktop entries + icons ---------------------------------------------------
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/apple-calendar.desktop" <<EOF
[Desktop Entry]
Name=Apple Calendar
Comment=Local viewer for iCloud home and work calendars
Exec=$BIN/apple-calendar
Icon=x-office-calendar
Terminal=false
Type=Application
Categories=Office;Calendar;
StartupNotify=true
EOF
cat > "$HOME/.local/share/applications/ruby-engine.desktop" <<EOF
[Desktop Entry]
Name=Ruby Engine
Comment=Customize Ruby — voice, personality, cloud brain, and more on Omarchy
Exec=$BIN/ruby-engine
Icon=ruby-engine
Terminal=false
Type=Application
Categories=Settings;
Keywords=ruby;voice;assistant;omarchy;siri;tts;ai;personality;
StartupNotify=true
EOF
for sz in 512 256 128 64; do
    src="$REPO/icons/hicolor/${sz}x${sz}/apps/ruby-engine.png"
    dst="$HOME/.local/share/icons/hicolor/${sz}x${sz}/apps"
    if [[ -f "$src" ]]; then
        mkdir -p "$dst"
        cp -f "$src" "$dst/"
    fi
done
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
info "Desktop entries and Ruby icons installed (Apple Calendar, Ruby Engine)."

# --- 7. Hyprland keybinding --------------------------------------------------------------
info "Add this line to ~/.config/hypr/bindings.lua, then: hyprctl reload"
echo '  o.bind("Insert", "Ruby: talk / send", "local-voice toggle")'
echo '  (see hypr/bindings-voice.lua)'
echo
info "Computer-use panic key (kills hypruse mid-action, safe): SUPER+ALT+Backspace -> pkill -f hypruse"
echo
info "Done. Press INS to talk. Voice is Piper southern-English female (offline, instant)."
