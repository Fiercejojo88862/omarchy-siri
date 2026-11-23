#!/usr/bin/env bash
# Ruby — all-in-one installer for Omarchy / Arch Linux + Hyprland.
#
# Installs system packages, the Python TTS engine, speech models, scripts,
# configs, desktop entries, the Hyprland keybinding, the wake-word autostart
# and the watcher service. Safe to re-run; never overwrites your configs,
# notes or recordings.
#
#   ./install.sh              install / repair everything
#   ./install.sh --doctor     only report what is installed / working
#   ./install.sh --no-deps    skip pacman/pip package installation
#   ./install.sh --no-model   skip the Ollama model download
#   ./install.sh --yes        assume yes for optional downloads
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
CONF="$HOME/.config/voice-assistant"
MODEL_DIR="$HOME/.local/share/whisper-models"
PIPER_DIR="$HOME/.local/share/piper"
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"
HYPR_CONF="$HOME/.config/hypr/bindings.lua"

MODE=install
SKIP_DEPS=0
PULL_MODEL=1
ASSUME_YES=0
WHISPER_FILE="${WHISPER_FILE:-ggml-base.en.bin}"
WHISPER_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$WHISPER_FILE"
PIPER_VOICES=(en_GB-southern_english_female-low en_GB-jenny_dioco-medium)

BEGIN_MARK="-- BEGIN RUBY VOICE (omarchy-siri)"
END_MARK="-- END RUBY VOICE (omarchy-siri)"

for arg in "$@"; do
    case "$arg" in
        --doctor)    MODE=doctor ;;
        --no-deps)   SKIP_DEPS=1 ;;
        --no-model)  PULL_MODEL=0 ;;
        --yes|-y)    ASSUME_YES=1 ;;
        -h|--help)
            sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
    esac
done

if [[ -t 1 ]]; then
    B=$'\033[1;34m'; G=$'\033[1;32m'; Y=$'\033[1;33m'; R=$'\033[1;31m'; Z=$'\033[0m'
else
    B=""; G=""; Y=""; R=""; Z=""
fi
info() { printf '%s==>%s %s\n' "$B" "$Z" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$G" "$Z" "$*"; }
warn() { printf '%s [!]%s %s\n' "$Y" "$Z" "$*"; }
fail() { printf '%s [x]%s %s\n' "$R" "$Z" "$*"; }
die()  { fail "$*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Run a privileged command. In a terminal we use sudo (it can prompt); when the
# installer is launched by an agent/GUI without a TTY we fall back to pkexec.
as_root() {
    if (( EUID == 0 )); then
        "$@"
    elif [[ -t 0 ]]; then
        sudo "$@"
    else
        pkexec "$@"
    fi
}

# Read KEY=VALUE from a config file without echoing secrets elsewhere.
conf_get() {
    local file="$1" key="$2" default="${3:-}"
    [[ -f "$file" ]] || { printf '%s' "$default"; return; }
    local val
    val=$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" 2>/dev/null \
        | tail -1 | cut -d= -f2- | sed 's/#.*//;s/^[[:space:]]*//;s/[[:space:]]*$//') || true
    printf '%s' "${val:-$default}"
}

truthy() {
    case "${1,,}" in 1|true|yes|on) return 0 ;; *) return 1 ;; esac
}

size_of() { stat -c%s "$1" 2>/dev/null || echo 0; }

# Deterministic routing probe: rehearsal mode, no audio, no side effects.
# (The golden-file selftest is environment-specific: it changes with which
# apps are installed, so the installer uses a stable probe instead.)
routing_ok() {
    local out
    out=$(VOICE_REHEARSAL=1 VOICE_DRYRUN=1 VOICE_ECHO=1 \
          "$REPO/bin/local-voice-exec" "what is 2 plus 2" 2>&1 || true)
    grep -q "That's 4" <<<"$out"
}

# ---------------------------------------------------------------- checks -----
check_bin() { have "$1" && ok "$1 ($(command -v "$1"))" || fail "$1 missing"; }
check_file() {
    local path="$1" label="$2" min="${3:-1}"
    if [[ -f "$path" ]] && (( $(size_of "$path") >= min )); then
        ok "$label"
    else
        fail "$label ($path)"
    fi
}

doctor() {
    local bad=0
    info "System packages"
    for t in whisper-cli pw-record pw-play ffmpeg notify-send wl-copy grim \
             paplay canberra-gtk-play piper hyprctl python3; do
        if have "$t"; then ok "$t"; else fail "$t missing"; bad=$((bad+1)); fi
    done

    info "Models"
    local model="$MODEL_DIR/$WHISPER_FILE"
    check_file "$model" "whisper model $WHISPER_FILE" 10000000 || bad=$((bad+1))
    for v in "${PIPER_VOICES[@]}"; do
        check_file "$PIPER_DIR/$v.onnx" "Piper voice $v" 1000000 || bad=$((bad+1))
    done

    info "Ruby files"
    for f in local-voice local-voice-exec local-voice-tts local-voice-wake ruby-engine; do
        if [[ -x "$BIN/$f" ]]; then ok "$BIN/$f"; else fail "$BIN/$f missing"; bad=$((bad+1)); fi
    done
    for f in voice persona wake cloud calendar weather translate watcher integrations; do
        check_file "$CONF/$f.conf" "config $f.conf" || bad=$((bad+1))
    done

    info "Hyprland"
    if [[ -f "$HYPR_CONF" ]] && grep -qF -- "$BEGIN_MARK" "$HYPR_CONF"; then
        ok "Insert binding block present"
        if have hyprctl && hyprctl -j binds 2>/dev/null | grep -q '"key": *"Insert"'; then
            ok "Insert is live in Hyprland"
        else
            warn "binding written but not loaded yet (hyprctl reload)"
        fi
    else
        fail "Insert binding missing from $HYPR_CONF"; bad=$((bad+1))
    fi
    if [[ -f "$HOME/.config/autostart/ruby-wake.desktop" ]]; then ok "wake autostart enabled"; fi

    info "Services"
    if systemctl --user is-enabled ruby-watcher.service >/dev/null 2>&1; then
        ok "ruby-watcher.service enabled ($(systemctl --user is-active ruby-watcher.service 2>/dev/null || true))"
    else
        warn "ruby-watcher.service not enabled"
    fi
    if ! have ollama; then
        warn "ollama not installed (cloud brain or deterministic skills only)"
    elif ollama list >/dev/null 2>&1; then
        local omodel
        omodel=$(conf_get "$CONF/cloud.conf" OLLAMA_MODEL qwen2.5:3b)
        if ollama list | awk 'NR>1 {print $1}' | grep -qx "$omodel"; then
            ok "Ollama model $omodel"
        else
            warn "Ollama model $omodel not pulled"
        fi
    else
        warn "Ollama server not reachable"
    fi

    info "Routing"
    if routing_ok; then
        ok "routing probe (rehearsal)"
    else
        fail "routing probe (rehearsal)"; bad=$((bad+1))
    fi

    if (( bad > 0 )); then
        warn "$bad core check(s) failed"
        return 1
    fi
    ok "all core checks passed"
}

if [[ "$MODE" == doctor ]]; then
    doctor
    exit $?
fi

# --------------------------------------------------------- 1. packages ------
info "Installing system packages"
if ! have pacman; then
    warn "pacman not found — this installer targets Arch/Omarchy; skipping packages"
elif (( SKIP_DEPS )); then
    warn "--no-deps: skipping pacman"
else
    REQUIRED_PKGS=(
        whisper-cpp ffmpeg pipewire libnotify libpulse libcanberra
        wl-clipboard grim curl python python-pip
        python-gobject gtk4 libadwaita
    )
    have ollama || REQUIRED_PKGS+=(ollama)
    MISSING=()
    for p in "${REQUIRED_PKGS[@]}"; do
        pacman -Q "$p" >/dev/null 2>&1 || MISSING+=("$p")
    done
    if (( ${#MISSING[@]} )); then
        info "Installing: ${MISSING[*]}"
        as_root pacman -S --needed --noconfirm -- "${MISSING[@]}" \
            || warn "some packages failed to install; run --doctor to see what is missing"
    else
        ok "system packages already present"
    fi
fi

# Song/Shazam fallback is optional and not required for core voice commands.
have songrec || warn "optional: songrec missing (Shazam song ID fallback)"

# --------------------------------------------------------- 2. Python --------
info "Installing Piper TTS"
if have piper; then
    ok "piper already installed"
elif (( SKIP_DEPS )); then
    warn "--no-deps: skipping pip"
else
    python3 -m pip install --user --break-system-packages --upgrade piper-tts \
        || die "piper-tts install failed"
    hash -r
    have piper || warn "piper is not on PATH yet (add ~/.local/bin to PATH)"
fi

# Optional: Hyprland-native computer use.
if ! python3 -c 'import hypruse' >/dev/null 2>&1; then
    if (( ! SKIP_DEPS )); then
        python3 -m pip install --user --break-system-packages hypruse >/dev/null 2>&1 \
            || warn "hypruse install failed (computer-use actions will be unavailable)"
    fi
fi

# --------------------------------------------------------- 3. models --------
info "Installing speech models"
mkdir -p "$MODEL_DIR"
MODEL_PATH="$MODEL_DIR/$WHISPER_FILE"
if [[ -f "$MODEL_PATH" ]] && (( $(size_of "$MODEL_PATH") > 10000000 )); then
    ok "whisper model present ($WHISPER_FILE)"
else
    info "Downloading $WHISPER_FILE (~150MB)..."
    curl -fL --retry 3 --connect-timeout 20 -o "$MODEL_PATH.part" "$WHISPER_URL" \
        || die "could not download $WHISPER_URL"
    mv -f "$MODEL_PATH.part" "$MODEL_PATH"
    ok "whisper model downloaded"
fi

mkdir -p "$PIPER_DIR"
for v in "${PIPER_VOICES[@]}"; do
    if [[ -f "$PIPER_DIR/$v.onnx" ]]; then
        ok "Piper voice $v present"
        continue
    fi
    info "Downloading Piper voice $v..."
    python3 -m piper.download_voices "$v" --download-dir "$PIPER_DIR" \
        || warn "could not download Piper voice $v"
done

# --------------------------------------------------------- 4. scripts -------
info "Linking scripts into $BIN"
mkdir -p "$BIN"
for src in "$REPO"/bin/*; do
    [[ -f "$src" ]] || continue
    chmod +x "$src" 2>/dev/null || true
    ln -sf "$src" "$BIN/$(basename "$src")"
done
ok "scripts linked"

# Install the watcher unit even when it is not started yet.
mkdir -p "$UNIT_DIR"
cp -f "$REPO/systemd/user/ruby-watcher.service" "$UNIT_DIR/ruby-watcher.service"

# --------------------------------------------------------- 5. configs -------
info "Installing config templates in $CONF"
mkdir -p "$CONF"
for f in calendar persona weather translate cloud voice wake watcher integrations; do
    if [[ -f "$CONF/$f.conf" ]]; then
        ok "$f.conf kept"
    else
        cp "$REPO/config/$f.conf.example" "$CONF/$f.conf"
        ok "$f.conf created"
    fi
done
chmod 600 "$CONF"/*.conf 2>/dev/null || true

# --------------------------------------------------------- 6. desktop -------
info "Installing desktop entries and icons"
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"
cat > "$APP_DIR/apple-calendar.desktop" <<EOF
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
cat > "$APP_DIR/ruby-engine.desktop" <<EOF
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
have gtk-update-icon-cache && gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
have update-desktop-database && update-desktop-database "$APP_DIR" 2>/dev/null || true
ok "desktop entries installed"

# --------------------------------------------------------- 7. keybinding ----
info "Installing Hyprland keybinding"
if [[ ! -f "$HYPR_CONF" ]]; then
    warn "no $HYPR_CONF (not an Omarchy/Hyprland Lua config?)"
    warn "add manually: o.bind(\"Insert\", \"Ruby: talk / send\", \"local-voice toggle\")"
elif grep -qF -- "$BEGIN_MARK" "$HYPR_CONF"; then
    ok "Ruby binding block already present"
elif grep -Eq 'o\.(bind|rebind)\("Insert"' "$HYPR_CONF"; then
    warn "Insert is already bound manually; leaving it untouched"
    warn "Ruby needs: o.bind(\"Insert\", \"Ruby: talk / send\", \"local-voice toggle\")"
else
    cp "$HYPR_CONF" "$HYPR_CONF.bak.$(date +%s)"
    cat >> "$HYPR_CONF" <<EOF

$BEGIN_MARK
-- Push-to-talk (toggle): press INS to record, press again to send.
o.bind("Insert", "Ruby: talk / send", "local-voice toggle")
-- Computer-use panic key: kills any in-flight hypruse click/type/scroll.
o.bind("SUPER + ALT + BackSpace", "Panic: stop computer-use", "pkill -f hypruse")
$END_MARK
EOF
    ok "binding added (backup: $HYPR_CONF.bak.*)"
fi

if have hyprctl && [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
    hyprctl reload >/dev/null 2>&1 || true
    errors=$(hyprctl configerrors 2>/dev/null || true)
    if [[ -n "${errors//[[:space:]]/}" ]]; then
        fail "Hyprland config errors:"
        printf '%s\n' "$errors"
    else
        ok "Hyprland reloaded with no config errors"
    fi
else
    warn "Hyprland not running in this session; binding applies on next login/start"
fi

# --------------------------------------------------- 8. wake autostart ------
if truthy "$(conf_get "$CONF/wake.conf" WAKE_AUTOSTART 0)"; then
    info "Enabling wake-word autostart"
    mkdir -p "$AUTOSTART_DIR"
    cat > "$AUTOSTART_DIR/ruby-wake.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Ruby Wake Word
Comment=Start Ruby wake-word listener (Hey Ruby)
Exec=local-voice-wake start
X-GNOME-Autostart-enabled=true
EOF
    ok "wake-word listener will start on login"
else
    ok "wake-word autostart off (enable it in Ruby Engine or wake.conf)"
fi

# --------------------------------------------------- 9. watcher service -----
info "Installing watcher service"
if have systemctl && systemctl --user show-environment >/dev/null 2>&1; then
    systemctl --user daemon-reload
    systemctl --user enable ruby-watcher.service >/dev/null 2>&1 \
        || warn "could not enable ruby-watcher.service"
    if truthy "$(conf_get "$CONF/watcher.conf" WATCHER_ENABLED 0)"; then
        systemctl --user restart ruby-watcher.service >/dev/null 2>&1 \
            && ok "ruby-watcher.service running" \
            || warn "could not start ruby-watcher.service"
    else
        ok "ruby-watcher.service enabled (proactive alerts off until WATCHER_ENABLED=1)"
    fi
else
    warn "no user systemd session; start ruby_watcher.py yourself if you want alerts"
fi

# --------------------------------------------------------- 10. LLM ----------
if have ollama; then
    info "Checking the local LLM"
    OLLAMA_MODEL=$(conf_get "$CONF/cloud.conf" OLLAMA_MODEL qwen2.5:3b)
    if ! ollama list >/dev/null 2>&1; then
        warn "Ollama server not running."
        warn "start it once with: systemctl enable --now ollama   (or: ollama serve)"
    elif ollama list | awk 'NR>1 {print $1}' | grep -qx "$OLLAMA_MODEL"; then
        ok "Ollama model $OLLAMA_MODEL ready"
    elif (( ! PULL_MODEL )); then
        warn "--no-model: skipping pull of $OLLAMA_MODEL"
    else
        info "Pulling $OLLAMA_MODEL (~2GB, one time)..."
        if ollama pull "$OLLAMA_MODEL"; then
            ok "Ollama model $OLLAMA_MODEL ready"
        else
            warn "pull failed; deterministic skills still work without it"
        fi
    fi
else
    warn "ollama missing: tool-calling agent disabled (deterministic skills still work)"
fi

# --------------------------------------------------------- 11. verify -------
info "Verifying installation"
SELFTEST_FAILED=0
if routing_ok; then
    ok "routing probe passed"
else
    fail "routing probe failed (run: $BIN/local-voice-exec \"what time is it\")"
    SELFTEST_FAILED=1
fi
have whisper-cli || { fail "whisper-cli still missing"; SELFTEST_FAILED=1; }
have piper       || { fail "piper still missing"; SELFTEST_FAILED=1; }

echo
if (( SELFTEST_FAILED )); then
    fail "Install finished with problems. Run: ./install.sh --doctor"
    exit 1
fi
ok "Ruby is installed."
echo
echo "  Press Insert to talk, press it again to send."
echo "  Say \"Hey Ruby\" after enabling the wake listener in Ruby Engine."
echo "  Diagnose any time with: ./install.sh --doctor"
