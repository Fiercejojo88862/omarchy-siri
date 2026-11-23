#!/usr/bin/env python3
"""Offline tests for the OpenCode brain adapter and backend selection.

No OpenCode process, network, audio or desktop is touched.

    python3 tests/test_opencode_brain.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))

import ruby_delegate  # noqa: E402
import ruby_opencode  # noqa: E402


class ParseEventsTest(unittest.TestCase):
    def test_extracts_answer_and_tools(self):
        raw = "\n".join([
            json.dumps({"type": "step_start", "sessionID": "ses_1", "part": {"type": "step-start"}}),
            json.dumps({"type": "tool_use", "sessionID": "ses_1", "part": {
                "type": "tool", "tool": "shell",
                "state": {"status": "completed", "input": {"command": "hyprctl activewindow"},
                          "output": "class: ghostty\n"}}}),
            json.dumps({"type": "text", "sessionID": "ses_1", "part": {
                "type": "text", "partID": "p1", "text": "**Opening** the terminal now."}}),
            "garbage that is not json",
        ])
        parsed = ruby_opencode.parse_events(raw)
        self.assertEqual(parsed["answer"], "Opening the terminal now.")
        self.assertEqual(parsed["session"], "ses_1")
        self.assertEqual([a["tool"] for a in parsed["actions"]], ["shell"])
        self.assertEqual(parsed["actions"][0]["input"]["command"], "hyprctl activewindow")

    def test_streamed_part_keeps_latest_text(self):
        raw = "\n".join([
            json.dumps({"type": "text", "part": {"type": "text", "partID": "p", "text": "Hel"}}),
            json.dumps({"type": "text", "part": {"type": "text", "partID": "p", "text": "Hello."}}),
            json.dumps({"type": "text", "part": {"type": "text", "partID": "q", "text": "Done."}}),
        ])
        parsed = ruby_opencode.parse_events(raw)
        self.assertEqual(parsed["answer"], "Hello. Done.")
        self.assertEqual(parsed["answer"].count("Hel"), 1)

    def test_errors_are_collected(self):
        raw = json.dumps({"type": "error", "sessionID": "s", "part": {"error": "boom"}})
        parsed = ruby_opencode.parse_events(raw)
        self.assertEqual(parsed["errors"], ["boom"])
        self.assertEqual(parsed["answer"], "")


class MessageTest(unittest.TestCase):
    def test_history_and_context(self):
        msg = ruby_opencode.build_message(
            "open the terminal",
            history=[{"role": "user", "content": "hi"},
                     {"role": "assistant", "content": "hello"},
                     {"role": "tool", "content": "ignored"}],
            context="workspace 2",
        )
        self.assertIn("User: hi", msg)
        self.assertIn("Ruby: hello", msg)
        self.assertNotIn("ignored", msg)
        self.assertIn("Context: workspace 2", msg)
        self.assertIn("User: open the terminal", msg)
        self.assertIn("spoken voice assistant", msg)

    def test_markdown_is_stripped(self):
        cleaned = ruby_opencode.clean_answer("**Done** — [link](https://x.test)\n\n`ok`")
        self.assertEqual(cleaned, "Done — link ok")


class ConfigTest(unittest.TestCase):
    def test_missing_binary_fails_fast(self):
        with tempfile.TemporaryDirectory() as td:
            conf = {
                "bin": str(Path(td) / "no-such-opencode"),
                "agent": "ruby",
                "model": "",
                "cwd": Path(td),
                "timeout": 5,
                "auto": True,
            }
            res = ruby_opencode.ask("hello", conf=conf)
            self.assertFalse(res["ok"])
            self.assertIn("not installed", res["error"])

    def test_config_parses_backend_settings(self):
        with tempfile.TemporaryDirectory() as td:
            conf_file = Path(td) / "cloud.conf"
            conf_file.write_text(
                "CLOUD_ENABLED=1\n"
                "BRAIN_BACKEND=opencode\n"
                "OPENCODE_BIN=/opt/custom/opencode\n"
                "OPENCODE_AGENT=ruby-dev\n"
                "OPENCODE_MODEL=anthropic/claude-sonnet-5-5\n"
                "OPENCODE_TIMEOUT=33\n"
                "OPENCODE_AUTO=0\n"
            )
            old = ruby_opencode.CLOUD_CONF
            ruby_opencode.CLOUD_CONF = conf_file
            try:
                conf = ruby_opencode.config()
            finally:
                ruby_opencode.CLOUD_CONF = old
            self.assertTrue(conf["enabled"])
            self.assertEqual(conf["provider"], "opencode")
            self.assertEqual(conf["agent"], "ruby-dev")
            self.assertEqual(conf["timeout"], 33)
            self.assertFalse(conf["auto"])


class DelegateTest(unittest.TestCase):
    def test_argv_for_each_cli(self):
        conf = {"agent": "ruby-dev", "model": "", "unsafe": False}
        self.assertEqual(
            ruby_delegate.build_argv("opencode", "opencode", "hi", conf)[:7],
            ["opencode", "run", "--format", "json", "--auto", "--agent", "ruby-dev"])
        self.assertIn("-p", ruby_delegate.build_argv("claude", "claude", "hi", conf))
        self.assertIn("acceptEdits", ruby_delegate.build_argv("claude", "claude", "hi", conf))
        self.assertEqual(ruby_delegate.build_argv("codex", "codex", "hi", conf)[1], "exec")
        self.assertIn("-p", ruby_delegate.build_argv("cursor", "cursor-agent", "hi", conf))
        self.assertIn("--yes", ruby_delegate.build_argv("aider", "aider", "hi", conf))
        self.assertIn("-p", ruby_delegate.build_argv("gemini", "gemini", "hi", conf))

    def test_unsafe_flags_are_opt_in(self):
        conf = {"agent": "ruby-dev", "model": "", "unsafe": False}
        safe = ruby_delegate.build_argv("claude", "claude", "hi", conf)
        unsafe = ruby_delegate.build_argv("claude", "claude", "hi", {**conf, "unsafe": True})
        self.assertNotIn("--dangerously-skip-permissions", safe)
        self.assertIn("--dangerously-skip-permissions", unsafe)

    def test_empty_prompt_and_unknown_cli(self):
        self.assertFalse(ruby_delegate.run("")["ok"])
        self.assertFalse(ruby_delegate.run("hi", cli="nope")["ok"])

    def test_opencode_output_parsing(self):
        raw = json.dumps({"type": "text", "part": {
            "type": "text", "partID": "p", "text": "done"}})
        answer, actions = ruby_delegate._clean_output("opencode", raw, 0)
        self.assertEqual(answer, "done")


class BackendSelectionTest(unittest.TestCase):
    def test_provider_opencode_selects_agent(self):
        import ruby_agent

        with tempfile.TemporaryDirectory() as td:
            conf_dir = Path(td)
            (conf_dir / "cloud.conf").write_text("CLOUD_ENABLED=1\nCLOUD_PROVIDER=opencode\n")
            old = ruby_agent.CONFIG_DIR
            ruby_agent.CONFIG_DIR = conf_dir
            try:
                conf = ruby_agent._cloud_conf()
            finally:
                ruby_agent.CONFIG_DIR = old
            self.assertEqual(conf["backend"], "opencode")

    def test_explicit_cloud_backend_wins(self):
        import ruby_agent

        with tempfile.TemporaryDirectory() as td:
            conf_dir = Path(td)
            (conf_dir / "cloud.conf").write_text(
                "CLOUD_ENABLED=1\nCLOUD_PROVIDER=opencode\nBRAIN_BACKEND=cloud\n")
            old = ruby_agent.CONFIG_DIR
            ruby_agent.CONFIG_DIR = conf_dir
            try:
                conf = ruby_agent._cloud_conf()
            finally:
                ruby_agent.CONFIG_DIR = old
            self.assertEqual(conf["backend"], "cloud")


if __name__ == "__main__":
    unittest.main(verbosity=2)
