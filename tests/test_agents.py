"""Tests for ``fleet.agents`` — parse_spec / cli_command."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet import agents  # noqa: E402


class ParseSpecTests(unittest.TestCase):
    def test_claude(self) -> None:
        self.assertEqual(agents.parse_spec("claude:sonnet"), ("claude", "sonnet"))

    def test_codex(self) -> None:
        self.assertEqual(agents.parse_spec("codex:o4-mini"), ("codex", "o4-mini"))

    def test_whitespace_tolerated(self) -> None:
        self.assertEqual(agents.parse_spec(" claude : sonnet "), ("claude", "sonnet"))

    def test_no_colon(self) -> None:
        with self.assertRaises(ValueError):
            agents.parse_spec("claude-sonnet")

    def test_unknown_vendor(self) -> None:
        with self.assertRaises(ValueError):
            agents.parse_spec("openai:gpt-5")

    def test_empty_model(self) -> None:
        with self.assertRaises(ValueError):
            agents.parse_spec("claude:")


class CliCommandTests(unittest.TestCase):
    def test_claude_cmd(self) -> None:
        self.assertEqual(
            agents.cli_command("claude:sonnet"),
            ["claude", "--dangerously-skip-permissions", "--model", "sonnet"],
        )

    def test_codex_cmd(self) -> None:
        self.assertEqual(
            agents.cli_command("codex:o4-mini"),
            ["codex", "--dangerously-bypass-approvals-and-sandbox", "-m", "o4-mini"],
        )


if __name__ == "__main__":
    unittest.main()
