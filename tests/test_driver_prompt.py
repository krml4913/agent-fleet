"""Tests for ``fleet.driver_prompt.render``."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet import driver_prompt  # noqa: E402


class DriverPromptTests(unittest.TestCase):
    def test_includes_required_fields(self) -> None:
        text = driver_prompt.render(
            task_id="42",
            description="Implement the foo feature.",
            formation_name="solo",
            role="driver",
            agent="claude:sonnet",
        )
        self.assertIn("task id:   task-42", text)
        self.assertIn("formation:  solo", text)
        self.assertIn("role:      driver", text)
        self.assertIn("agent:     claude:sonnet", text)
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
        self.assertLess(line_count, 60, f"driver-prompt got fat: {line_count} lines")

    def test_mentions_fleet_agent_ask_rule(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="solo",
            role="driver",
            agent="claude:sonnet",
        )
        self.assertIn("fleet-agent ask", text)

    def test_instructs_initial_inbox_read(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="solo",
            role="driver",
            agent="claude:sonnet",
            fleet_bin="fleet-agent",
        )
        self.assertIn("Before any other task work, run `fleet-agent inbox-read`", text)

    def test_includes_role_fragment_when_role_file_exists(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="pair_review",
            role="implementer",
            agent="claude:sonnet",
        )
        self.assertIn("You are the implementer", text)
        self.assertIn("an approved design exists", text)

    def test_skips_role_fragment_when_role_file_is_missing(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="solo",
            role="unknown-role",
            agent="claude:sonnet",
        )
        self.assertNotIn("You are the implementer", text)
        self.assertNotIn("You are the reviewer", text)
        self.assertNotIn("You are the designer", text)

    def test_composes_base_role_then_description(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="Task description sentinel.",
            formation_name="pair_review",
            role="implementer",
            agent="claude:sonnet",
        )
        base_idx = text.index("You are a fleet driver")
        role_idx = text.index("You are the implementer")
        description_idx = text.index("Task description sentinel.")
        self.assertLess(base_idx, role_idx)
        self.assertLess(role_idx, description_idx)


class MemoryIndexInjectionTests(unittest.TestCase):
    """Issue #114: the MEMORY.md index is injected so any vendor sees it at start."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)
        (self.state_dir / "memory").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _render(self, **over: object) -> str:
        kwargs = dict(
            task_id="1", description="x", formation_name="solo",
            role="driver", agent="claude:sonnet",
        )
        kwargs.update(over)
        return driver_prompt.render(**kwargs)

    def test_injects_index_when_present(self) -> None:
        (self.state_dir / "memory" / "MEMORY.md").write_text(
            "# Memory Index\n\n- [repo-authority](repo-authority.md) — main 直 push 禁止\n",
            encoding="utf-8",
        )
        text = self._render(state_dir=self.state_dir)
        self.assertIn("## Project memory (index)", text)
        self.assertIn("repo-authority", text)
        self.assertIn("main 直 push 禁止", text)
        # Injected above the task metadata block, not after it.
        self.assertLess(text.index("Project memory (index)"), text.index("task id:   task-1"))

    def test_no_injection_when_index_absent(self) -> None:
        # memory/ exists but no MEMORY.md.
        text = self._render(state_dir=self.state_dir)
        self.assertNotIn("## Project memory (index)", text)

    def test_no_injection_when_state_dir_omitted(self) -> None:
        (self.state_dir / "memory" / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
        text = self._render()
        self.assertNotIn("## Project memory (index)", text)


class FleetAgentPathInjectionTests(unittest.TestCase):
    """Issue #125: lifecycle commands must resolve without ``fleet-agent`` on PATH.

    Driver panes never get ``fleet-agent`` on PATH (macOS path_helper + shell rc
    rebuild PATH and drop the env injected by ``tmux new-window -e``), so the
    prompt rewrites every fleet-agent command to an absolute path that works for
    both claude and codex regardless of PATH.
    """

    def test_fleet_agent_bin_is_absolute_and_exists(self) -> None:
        bin_path = driver_prompt.fleet_agent_bin()
        self.assertTrue(Path(bin_path).is_absolute(), bin_path)
        self.assertTrue(Path(bin_path).is_file(), bin_path)
        self.assertEqual(Path(bin_path).name, "fleet-agent")

    def test_render_rewrites_commands_to_absolute_path(self) -> None:
        bin_path = "/opt/agent-fleet/fleet-agent"
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="solo",
            role="driver",
            agent="codex:gpt-5.5",
            fleet_bin=bin_path,
        )
        # Every fleet-managed command uses the absolute path...
        self.assertIn(f"`{bin_path} inbox-read`", text)
        self.assertIn(f"{bin_path} done --result approved", text)
        # ...and no bare `fleet-agent` command survives (backtick then bare name).
        self.assertNotIn("`fleet-agent ", text)

    def test_render_default_uses_resolved_bin(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="solo",
            role="driver",
            agent="codex:gpt-5.5",
        )
        self.assertIn(f"`{driver_prompt.fleet_agent_bin()} inbox-read`", text)

    def test_render_leaves_user_description_untouched(self) -> None:
        # A user description that mentions fleet-agent must not be rewritten.
        text = driver_prompt.render(
            task_id="1",
            description="See `fleet-agent` PATH bug.",
            formation_name="solo",
            role="driver",
            agent="claude:opus",
            fleet_bin="/opt/agent-fleet/fleet-agent",
        )
        self.assertIn("See `fleet-agent` PATH bug.", text)

    def test_render_quotes_path_with_spaces(self) -> None:
        text = driver_prompt.render(
            task_id="1",
            description="x",
            formation_name="solo",
            role="driver",
            agent="claude:opus",
            fleet_bin="/opt/agent fleet/fleet-agent",
        )
        self.assertIn("'/opt/agent fleet/fleet-agent' inbox-read", text)


if __name__ == "__main__":
    unittest.main()
