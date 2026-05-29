"""Tests for build_parser_user() and build_parser_agent() in fleet.cli."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet.cli import build_parser_agent, build_parser_user  # noqa: E402

USER_COMMANDS = {"init", "preflight", "leader", "attach", "status", "log", "formation", "workspace", "rm"}
AGENT_COMMANDS = {
    "start", "inbox", "inbox-read", "send-prompt", "cleanup", "ask", "event",
    "approve", "reject", "done",
}


def _subcommand_names(parser) -> set[str]:
    names: set[str] = set()
    for action in parser._actions:
        if hasattr(action, "_name_parser_map"):
            names.update(action._name_parser_map.keys())
    return names


class TestBuildParserUser(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser_user()

    def test_prog_name(self) -> None:
        self.assertEqual(self.parser.prog, "fleet")

    def test_exposes_exactly_8_commands(self) -> None:
        names = _subcommand_names(self.parser)
        self.assertEqual(len(names), 9, f"expected 9 user commands, got {sorted(names)}")

    def test_exposes_all_user_commands(self) -> None:
        names = _subcommand_names(self.parser)
        self.assertEqual(names, USER_COMMANDS)

    def test_no_agent_commands_exposed(self) -> None:
        names = _subcommand_names(self.parser)
        leaked = names & AGENT_COMMANDS
        self.assertFalse(leaked, f"agent commands leaked into fleet parser: {leaked}")


class TestBuildParserAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser_agent()

    def test_prog_name(self) -> None:
        self.assertEqual(self.parser.prog, "fleet-agent")

    def test_exposes_exactly_10_commands(self) -> None:
        names = _subcommand_names(self.parser)
        self.assertEqual(len(names), 10, f"expected 10 agent commands, got {sorted(names)}")

    def test_exposes_all_agent_commands(self) -> None:
        names = _subcommand_names(self.parser)
        self.assertEqual(names, AGENT_COMMANDS)

    def test_no_user_commands_exposed(self) -> None:
        names = _subcommand_names(self.parser)
        leaked = names & USER_COMMANDS
        self.assertFalse(leaked, f"user commands leaked into fleet-agent parser: {leaked}")


if __name__ == "__main__":
    unittest.main()
