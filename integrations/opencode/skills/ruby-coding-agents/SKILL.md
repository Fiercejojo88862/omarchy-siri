---
name: Ruby coding agents
description: Delegate a coding task to any installed coding-agent CLI (OpenCode, Claude Code, Codex, Cursor, Aider, Gemini) and summarise the result.
---

# Delegating coding work

Ruby can hand a coding task to a full coding agent CLI. Use this for multi-file
changes, debugging, test writing, or repository questions that need real tools.

## Preferred path

```bash
python3 ~/.local/bin/ruby_delegate.py --json \
  --cli auto \
  --cwd ~/projects/my-app \
  --prompt "Fix the failing tests in tests/test_api.py and run them."
```

It prints JSON:

```json
{"ok": true, "cli": "opencode", "answer": "...", "exit": 0, "elapsed": 42.1}
```

`--cli auto` tries the first installed CLI in this order: OpenCode, Claude Code,
Codex, Cursor Agent, Aider, Gemini. Override with
`--cli opencode|claude|codex|cursor|aider|gemini`.

`--list` shows what is installed without running anything.

## Direct invocations

Only use these when the helper cannot do what you need:

```bash
opencode run --agent ruby-dev "Fix the failing tests and run them."
claude -p "Fix the failing tests and run them." --output-format text
codex exec --skip-git-repo-check "Fix the failing tests and run them."
cursor-agent -p --output-format text "Fix the failing tests and run them."
aider --message "Fix the failing tests" --yes --no-auto-commits
gemini -p "Fix the failing tests and run them."
```

Check `command -v` first. Do not install a CLI. Do not use flags that bypass all
sandboxing unless the user explicitly asked for that.

## Rules

- Work in the directory the user named, or `OPENCODE_CWD`.
- Before delegating, capture `git status --short` so you can report what changed.
- After delegating, verify: `git diff --stat`, run the tests, and read the last
  part of the output. A zero exit code from the CLI does not guarantee the task
  succeeded.
- Never let a delegated agent `git push`, sudo, install packages, or delete
  unrelated files.
- Long runs: give the CLI a timeout (the helper defaults to 10 minutes). If it
  times out, say so and report the partial log path.
- Spoken reply: 2–4 sentences. Put the diff summary and test output in a
  notification.

## Failure handling

- Missing CLI: report which ones are installed (`--list`).
- Interactive prompt or auth failure: the CLI cannot run headless; report it and
  offer another installed CLI.
- Permission denied: do not try to bypass it with a different shell.
