#!/usr/bin/env bash
# Siri of Omarchy installer: scripts + voice models + desktop entry + patches.
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
for t in curl ffmpeg pw-record whisper-cli ollama hyprctl notify-send; do
    command -v "$t" >/dev/null 2>&1 || warn "missing: $t (install it for full function)"
done

# --- 1. Scripts -----------------------------------------------------------------
mkdir -p "$BIN"
for f in local-voice local-voice-exec local-voice-tts local-voice-say \
         local-voice-clone local-voice-calendar apple-calendar apple-calendar-set-icon; do
    ln -sf "$REPO/bin/$f" "$BIN/$f"
    chmod +x "$REPO/bin/$f"
done
info "Scripts linked into $BIN."

# --- 2. Python voice stack ---------------------------------------------------------
# CPU torch first (XTTS runs on CPU; GPU wheels also work).
pip install --user --break-system-packages torch torchaudio \
    --index-url https://download.pytorch.org/whl/cpu 2>/dev/null || \
pip install --user --break-system-packages torch torchaudio
pip install --user --break-system-packages coqui-tts edge-tts piper-tts
python3 "$REPO/patches/apply-patches.py"

# --- 3. Speech-to-text model (whisper.cpp, 142MB) ------------------------------------
mkdir -p "$MODEL_DIR"
if [[ ! -f "$MODEL_DIR/ggml-base.en.bin" ]]; then
    info "Downloading whisper base.en model..."
    curl -fsSL -o "$MODEL_DIR/ggml-base.en.bin" \
        https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
else
    info "Whisper model present."
fi

# --- 4. Fallback British TTS voice (Piper, 61MB, offline) ----------------------------
mkdir -p "$PIPER_DIR"
if [[ ! -f "$PIPER_DIR/en_GB-jenny_dioco-medium.onnx" ]]; then
    info "Downloading Piper British voice..."
    python3 -m piper.download_voices en_GB-jenny_dioco-medium --download-dir "$PIPER_DIR"
else
    info "Piper voice present."
fi

# --- 5. Config templates (never overwrite yours) --------------------------------------
mkdir -p "$CONF"
[[ -f "$CONF/calendar.conf" ]] || cp "$REPO/config/calendar.conf.example" "$CONF/calendar.conf"
[[ -f "$CONF/persona.conf" ]] || cp "$REPO/config/persona.conf.example" "$CONF/persona.conf"
chmod 600 "$CONF/calendar.conf" 2>/dev/null || true
info "Config templates in $CONF (edit calendar.conf with your iCloud links)."

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
info "Done. Press INS to talk. First run downloads the XTTS-v2 model (~2GB)."
info "Clone your voice: record 25s of clean speech as ~/.local/share/voice-clone/siri_ref_25s.wav"
info "Note: XTTS-v2 is CPML-licensed (free for non-commercial use)."
