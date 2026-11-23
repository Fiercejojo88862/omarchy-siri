---
name: Ruby desktop control
description: Inspect and operate the Hyprland desktop — windows, workspaces, clicks, typing, keys, media, volume, brightness — safely and semantically.
---

# Desktop control on Omarchy

Use this skill whenever the user asks you to open, close, move, focus, click,
type, scroll, switch workspace, play/pause, change volume or brightness, or
otherwise touch the desktop.

## 1. Look first

```bash
hypruse desktop --json      # monitors, workspaces, windows, active window
hypruse ui --json           # accessible controls in the active window
hyprctl activewindow -j
hyprctl clients -j
```

`hypruse ui` names the controls you can click. Prefer it over guessing
coordinates. If `hypruse` is missing, fall back to `hyprctl` for state and
`hyprctl dispatch` for window/workspace actions.

## 2. Act with semantic targets

```bash
hypruse click_ui "Save"                    # click a named control
hypruse keyboard type "hello world"        # type into the focused field
hypruse keyboard key "ctrl+s"              # key combo
hypruse pointer scroll -3                  # negative = up, positive = down
hypruse launch --workspace 2 ghostty       # launch on a workspace
hypruse launch chromium -- --new-window    # app flags after --
```

Window and workspace verbs:

```bash
hypruse hypr workspace 3
hypruse hypr focus_window 0x55a1c2d3
hypruse hypr move_window 0x55a1c2d3 2     # move window to workspace 2
hypruse hypr close_window 0x55a1c2d3
hypruse hypr fullscreen
hypruse hypr toggle_floating
```

Addresses come from `hypruse desktop --json` / `hyprctl clients -j`.

## 3. Everyday controls

```bash
playerctl play-pause | next | previous | status
wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle
wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+
brightnessctl set 10%+
notify-send -t 4000 "Ruby" "message"
```

Omarchy conveniences (allowed, no sudo):

```bash
omarchy toggle nightlight
omarchy theme set <name>
omarchy reminder 15 "Pickup Jack"
omarchy capture screenshot
omarchy launch browser
```

## 4. Verify

After a click or key sequence, re-read state and confirm the expected change:

```bash
hypruse desktop --json
hypruse ui --json
```

If nothing changed, say so instead of repeating the action blindly.

## 5. Safety rules

- Never type passwords, tokens, or one-time codes.
- Do not interact with login, payment, or system authentication dialogs.
- Do not send messages, submit forms, or buy anything without the user saying
  "send it" or "confirm".
- Close/quit only the window the user named, and only after confirming its
  address/class.
- `hypruse` refuses locked sessions and password fields. Treat a refusal as a
  stop signal, not as something to work around.
- No sudo, package installs, file deletion, reboot, or shutdown.

## Failure handling

- `hypruse` exit 3 means refused (locked session/auth context). Report it.
- `hypruse` exit 4 means no matching control. Run `hypruse ui --json` and pick a
  real label.
- If a window cannot be found, list windows with `hyprctl clients -j` and ask
  which one the user meant.
