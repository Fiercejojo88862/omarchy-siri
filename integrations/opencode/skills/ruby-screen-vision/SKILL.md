---
name: Ruby screen vision
description: Read or describe what is on screen when the UI has no accessible names — errors, images, PDFs, video, canvases, and anything visual.
---

# Screen vision

Use this when the user asks what is on screen, to read an error, or when
`hypruse ui --json` has no useful labels (canvas apps, images, videos, remote
desktops, custom-drawn UIs).

## Command

```bash
python3 ~/.local/bin/ruby_vision.py "What error is shown?"
python3 ~/.local/bin/ruby_vision.py "Describe the main content."
python3 ~/.local/bin/ruby_vision.py "What does the chart show?"
```

It prints JSON:

```json
{"ok": true, "answer": "..."}
```

Use the `answer` as evidence. Do not claim you saw something the tool did not
report.

## Order of preference

1. `hypruse ui --json` — semantic controls and labels.
2. `hypruse desktop --json` — window titles/classes, geometry.
3. `ruby_vision.py` — pixels, when 1 and 2 are insufficient.

## Safety and privacy

- The capture is temporary and deleted after inference; do not copy it.
- Capture is skipped when the session is locked or a password/auth field is
  focused. If it fails for that reason, stop and tell the user.
- Never point vision at a screen showing credentials and repeat them aloud.
  Summarise instead: "There is a password field with a token filled in."
- Vision is slower than semantic tools. Use it once, then act with hypruse.

## Spoken answer

Give the finding in one or two sentences. If the user asked you to fix what you
see, continue with the desktop-control skill and verify afterwards.
