-- Siri of Omarchy push-to-talk (toggle): press INS to record, press again to send.
-- Drop this block into ~/.config/hypr/bindings.lua (requires local-voice on PATH).
o.bind("Insert", "Voice: talk / send", "local-voice toggle")

-- Panic key for computer-use (stops any in-flight click/type; safe to kill):
-- Hyprland conf syntax: bind = SUPER SHIFT, BackSpace, exec, pkill -f hypruse
