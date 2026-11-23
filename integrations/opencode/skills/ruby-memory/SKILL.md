---
name: Ruby memory
description: Save and search Ruby's local notes and auto-tagged logs (#gym, #car, #money, #school) in SQLite.
---

# Local memory

Ruby keeps notes and tagged logs in
`~/.local/share/voice-assistant/memory.db` with optional Ollama embeddings and
an FTS5 keyword fallback. Everything stays on the machine.

## Save

```bash
python3 ~/.local/bin/ruby_memory.py add "buy milk"
python3 ~/.local/bin/ruby_memory.py add "bench 80kg 3x5" --tag "#gym"
python3 ~/.local/bin/ruby_memory.py add "oil change at 142000 miles" --tag "#car"
```

Tags are `#gym`, `#car`, `#money`, `#school` when they fit; otherwise omit the
tag. Keep the user's wording; do not rewrite it into a summary.

## Read

```bash
python3 ~/.local/bin/ruby_memory.py recent --limit 5
python3 ~/.local/bin/ruby_memory.py recent --tag "#gym" --limit 5
python3 ~/.local/bin/ruby_memory.py search "when did I last change the oil" --limit 5
```

## Answering

- Quote the relevant entry and its date, briefly.
- If search returns nothing, say so; do not invent history.
- For "take a note", save it and confirm with the text you saved.
- For car logs, prefer the dedicated car-log tool when available so LubeLogger
  can receive it too; local memory remains the source of truth.

Never read or write `cloud.conf`, `integrations.conf`, `.env` files, or anything
under `~/.ssh`, `~/.gnupg`, or `~/.aws`.
