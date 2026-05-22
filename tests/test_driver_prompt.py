"""Tests for ``fleet.driver_prompt.render``."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet import driver_prompt  # noqa: E402
from fleet.plugins import git_worktree  # noqa: E402


class DriverPromptTests(unittest.TestCase):
    def test_includes_required_fields(self) -> None:
        text = driver_prompt.render(
            task_id="42",
            description="Implement the foo feature.",
            formation_name="solo",
            role="driver",
            agent="claude:sonnet",
            workflow_fragment="Workflow-specific instructions.",
        )
        self.assertIn("task id:   task-42", text)
        self.assertIn("formation:  solo", text)
        self.assertIn("role:      driver", text)
        self.assertIn("agent:     claude:sonnet", text)
        self.assertIn("Workflow-specific instructions.", text)
        self.assertIn("Implement the foo feature.", text)

    def test_base_prompt_does_not_include_git_workflow(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="solo",
            role="driver",
            agent="claude:sonnet",
        )
        self.assertNotIn("Git workflow", text)
        self.assertNotIn("gh pr create", text)

    def test_keeps_prompt_under_budget(self) -> None:
        """Guard against driver-prompt bloat regression (design doc §10.2)."""
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="solo",
            role="driver",
            agent="claude:sonnet",
        )
        line_count = text.count("\n")
        # Be generous; we just want a tripwire if BASE balloons.
        self.assertLess(line_count, 45, f"driver-prompt got fat: {line_count} lines")

    def test_mentions_fleet_agent_ask_rule(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="solo",
            role="driver",
            agent="claude:sonnet",
        )
        self.assertIn("fleet-agent ask", text)

    def test_git_worktree_fragment_adds_git_workflow(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="solo",
            role="driver",
            agent="claude:sonnet",
            workflow_fragment=git_worktree.DRIVER_PROMPT_FRAGMENT,
        )
        self.assertIn("Git workflow", text)
        self.assertIn("gh pr create", text)

    def test_includes_role_fragment_when_role_file_exists(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="pair_review",
            role="implementer",
            agent="claude:sonnet",
        )
        self.assertIn("あなたは実装者", text)
        self.assertIn("承認された設計", text)

    def test_skips_role_fragment_when_role_file_is_missing(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="solo",
            role="unknown-role",
            agent="claude:sonnet",
        )
        self.assertNotIn("あなたは実装者", text)
        self.assertNotIn("あなたは査読者", text)
        self.assertNotIn("あなたは設計者", text)

    def test_composes_base_workflow_role_then_description(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="Task description sentinel.",
            formation_name="pair_review",
            role="implementer",
            agent="claude:sonnet",
            workflow_fragment="Workflow sentinel.",
        )
        base_idx = text.index("You are a fleet driver")
        workflow_idx = text.index("Workflow sentinel.")
        role_idx = text.index("あなたは実装者")
        description_idx = text.index("Task description sentinel.")
        self.assertLess(base_idx, workflow_idx)
        self.assertLess(workflow_idx, role_idx)
        self.assertLess(role_idx, description_idx)


if __name__ == "__main__":
    unittest.main()
