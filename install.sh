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
         local-voice-calendar apple-calendar apple-calendar-set-icon; do
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
for f in calendar persona weather; do
    [[ -f "$CONF/$f.conf" ]] || cp "$REPO/config/$f.conf.example" "$CONF/$f.conf"
done
chmod 600 "$CONF"/*.conf 2>/dev/null || true
info "Config templates in $CONF (calendar links, persona, optional AccuWeather key)."

# --- 6. Apple Calendar desktop entry ---------------------------------------------------
DESKTOP="$HOME/.local/share/applications/apple-calendar.desktop"
cat > "$DESKTOP" <<EOF
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
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
info "Desktop entry installed."

# --- 7. Hyprland keybinding --------------------------------------------------------------
info "Add this line to ~/.config/hypr/bindings.lua, then: hyprctl reload"
echo '  o.bind("Insert", "Voice: talk / send", "local-voice toggle")'
echo '  (see hypr/bindings-voice.lua)'
echo
info "Computer-use panic key (kills hypruse mid-action, safe): bind SUPER+SHIFT+Backspace to: pkill -f hypruse"
echo
info "Done. Press INS to talk. Voice is Piper southern-English female (offline, instant)."
