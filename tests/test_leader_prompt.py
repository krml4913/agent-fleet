"""Tests for ``fleet.leader_prompt.render``."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
        self.assertLess(line_count, 90, f"leader-prompt got fat: {line_count} lines")

    def test_leader_base_md_exists_and_nonempty(self) -> None:
        path = ROOT / "docs" / "prompts" / "leader-base.md"
        self.assertTrue(path.is_file(), "leader-base.md does not exist")
        self.assertGreater(path.stat().st_size, 0, "leader-base.md is empty")


class SelectionGuideInjectionTests(unittest.TestCase):
    """Issue #118: ``formations/SELECTION.md`` is injected so the leader consults
    the project's own formation-selection criteria at ``start``.

    Mirrors the driver prompt's MEMORY.md injection tests (Issue #114).
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)
        (self.state_dir / "formations").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _render(self) -> str:
        return leader_prompt.render(project_name="p", state_dir=self.state_dir)

    def test_injects_guide_when_present(self) -> None:
        (self.state_dir / "formations" / "SELECTION.md").write_text(
            "# Selection\n\n- tiny doc fix → solo\n- risky change → pair_review\n",
            encoding="utf-8",
        )
        text = self._render()
        self.assertIn("## Formation selection guide (this project)", text)
        self.assertIn("risky change → pair_review", text)
        # Injected above the metadata footer, not after it.
        self.assertLess(
            text.index("Formation selection guide (this project)"),
            text.index("project:    p"),
        )

    def test_no_injection_when_guide_absent(self) -> None:
        # formations/ exists but no SELECTION.md.
        text = self._render()
        self.assertNotIn("## Formation selection guide (this project)", text)

    def test_no_injection_when_guide_empty(self) -> None:
        (self.state_dir / "formations" / "SELECTION.md").write_text("   \n", encoding="utf-8")
        text = self._render()
        self.assertNotIn("## Formation selection guide (this project)", text)


if __name__ == "__main__":
    unittest.main()
