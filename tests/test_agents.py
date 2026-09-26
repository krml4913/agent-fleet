"""Tests for ``fleet.agents`` — parse_spec / cli_command."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import tests._fleet_test_helpers  # noqa: E402,F401  (hermetic env: FLEET_NO_NOTIFY / FLEET_NO_MUX)

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
                # TOML literal string: Windows backslashes are not escapes.
                f"[projects.'{root.resolve()}']\ntrust_level = \"trusted\"\n",
                encoding="utf-8",
            )

            self.assertTrue(agents.codex_repo_trusted(root, config_path=config))

    def test_untrusted_level_is_false(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            config = Path(tmp) / "config.toml"
            config.write_text(
                f"[projects.'{root.resolve()}']\ntrust_level = \"untrusted\"\n",
                encoding="utf-8",
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
            config.write_text("[projects.\n", encoding="utf-8")

            self.assertFalse(agents.codex_repo_trusted(root, config_path=config))


class ClaudeRepoTrustedTests(unittest.TestCase):
    def _config(self, tmp: str, projects) -> Path:
        config = Path(tmp) / ".claude.json"
        config.write_text(json.dumps({"projects": projects}), encoding="utf-8")
        return config

    def _repo(self, tmp: str) -> Path:
        root = Path(tmp) / "repo"
        root.mkdir()
        return root.resolve()

    def test_trusted(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            config = self._config(tmp, {str(root): {"hasTrustDialogAccepted": True}})

            self.assertIs(agents.claude_repo_trusted(root, config_path=config), True)

    def test_dialog_declined_or_absent_is_false(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            declined = self._config(tmp, {str(root): {"hasTrustDialogAccepted": False}})
            self.assertIs(agents.claude_repo_trusted(root, config_path=declined), False)

            unrelated = self._config(tmp, {"/elsewhere": {"hasTrustDialogAccepted": True}})
            self.assertIs(agents.claude_repo_trusted(root, config_path=unrelated), False)

            no_flag = self._config(tmp, {str(root): {"allowedTools": []}})
            self.assertIs(agents.claude_repo_trusted(root, config_path=no_flag), False)

    def test_no_projects_key_is_false(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            config = Path(tmp) / ".claude.json"
            config.write_text("{}", encoding="utf-8")

            self.assertIs(agents.claude_repo_trusted(root, config_path=config), False)

    def test_trusted_ancestor_does_not_cover_a_subdirectory(self) -> None:
        # A trusted parent did not stop the dialog on a fresh worktree (#327),
        # so only the repo root's own key counts.
        with TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            config = self._config(tmp, {str(root.parent): {"hasTrustDialogAccepted": True}})

            self.assertIs(agents.claude_repo_trusted(root, config_path=config), False)

    def test_unreadable_config_is_unknown(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            self.assertIsNone(
                agents.claude_repo_trusted(root, config_path=Path(tmp) / "missing.json")
            )

            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertIsNone(agents.claude_repo_trusted(root, config_path=broken))

            not_object = Path(tmp) / "list.json"
            not_object.write_text("[]", encoding="utf-8")
            self.assertIsNone(agents.claude_repo_trusted(root, config_path=not_object))

    def test_config_path_honours_claude_config_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                self.assertEqual(agents.claude_config_path(), Path(tmp) / ".claude.json")

    def test_default_config_path_is_home_dotfile(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(agents.claude_config_path(), Path.home() / ".claude.json")

    def test_hint_names_the_repo_and_the_fix(self) -> None:
        hint = agents.claude_trust_hint("/repos/newproj")
        self.assertIn("/repos/newproj", hint)
        self.assertIn("Yes, I trust this folder", hint)
        self.assertIn("run `claude`", hint)


if __name__ == "__main__":
    unittest.main()
