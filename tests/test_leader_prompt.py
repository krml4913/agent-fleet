"""Tests for ``fleet.leader_prompt.render``."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet import leader_prompt  # noqa: E402


class LeaderPromptTests(unittest.TestCase):
    def _render(self) -> str:
        return leader_prompt.render(
            project_name="test-project",
            state_dir=Path("/tmp/fleet-state/projects/test-project"),
        )

    def test_includes_leader_role_marker(self) -> None:
        text = self._render()
        self.assertIn("You are the leader of a fleet project", text)

    def test_footer_contains_project_name(self) -> None:
        text = self._render()
        self.assertIn("project:    test-project", text)

    def test_footer_contains_state_dir(self) -> None:
        text = self._render()
        self.assertIn("state dir:  /tmp/fleet-state/projects/test-project", text)

    def test_footer_contains_memory_path(self) -> None:
        text = self._render()
        self.assertIn("memory:     /tmp/fleet-state/projects/test-project/memory/MEMORY.md", text)
        self.assertIn("read this first", text)

    def test_mentions_fleet_agent_start(self) -> None:
        text = self._render()
        self.assertIn("fleet-agent start", text)

    def test_mentions_primary_maintainer(self) -> None:
        text = self._render()
        self.assertIn("PRIMARY maintainer", text)

    def test_keeps_prompt_under_budget(self) -> None:
        """Guard against leader-prompt bloat regression (symmetric with driver-prompt guard)."""
        text = self._render()
        line_count = text.count("\n")
        self.assertLess(line_count, 65, f"leader-prompt got fat: {line_count} lines")

    def test_leader_base_md_exists_and_nonempty(self) -> None:
        path = ROOT / "docs" / "prompts" / "leader-base.md"
        self.assertTrue(path.is_file(), "leader-base.md does not exist")
        self.assertGreater(path.stat().st_size, 0, "leader-base.md is empty")


if __name__ == "__main__":
    unittest.main()
