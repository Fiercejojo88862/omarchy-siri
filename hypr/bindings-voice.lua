-- Siri of Omarchy push-to-talk (toggle): press INS to record, press again to send.
-- Drop this block into ~/.config/hypr/bindings.lua (requires local-voice on PATH).
o.bind("Insert", "Voice: talk / send", "local-voice toggle")

-- Computer-use panic key: kills any in-flight hypruse click/type/scroll.
o.bind("SUPER + ALT + BackSpace", "Panic: stop computer-use", "pkill -f hypruse")
