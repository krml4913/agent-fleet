"""Tests for ``fleet.agents`` — parse_spec / cli_command."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
            [
                "codex",
                "-c",
                "check_for_update_on_startup=false",
                "--dangerously-bypass-approvals-and-sandbox",
                "-m",
                "o4-mini",
            ],
        )


class CodexRepoTrustedTests(unittest.TestCase):
    def test_trusted(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            config = Path(tmp) / "config.toml"
            config.write_text(
                f'[projects."{root.resolve()}"]\ntrust_level = "trusted"\n'
            )

            self.assertTrue(agents.codex_repo_trusted(root, config_path=config))

    def test_untrusted_level_is_false(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            config = Path(tmp) / "config.toml"
            config.write_text(
                f'[projects."{root.resolve()}"]\ntrust_level = "untrusted"\n'
            )

            self.assertFalse(agents.codex_repo_trusted(root, config_path=config))

    def test_missing_config_is_false(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()

            self.assertFalse(
                agents.codex_repo_trusted(root, config_path=Path(tmp) / "missing.toml")
            )

    def test_invalid_toml_is_false(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            config = Path(tmp) / "config.toml"
            config.write_text("[projects.\n")

            self.assertFalse(agents.codex_repo_trusted(root, config_path=config))


if __name__ == "__main__":
    unittest.main()
