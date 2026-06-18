"""Tests for ``fleet.leader_prompt.render`` (project-agnostic since Issue #166)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import leader_prompt, state  # noqa: E402


class _FleetHomeBase(unittest.TestCase):
    """Isolate FLEET_HOME so the injected global index is controlled, not the
    operator's live store."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()


class LeaderPromptTests(_FleetHomeBase):
    def _render(self) -> str:
        return leader_prompt.render()

    def test_render_requires_no_project(self) -> None:
        # The headline of the decouple: render takes no project_name / state_dir.
        text = leader_prompt.render()
        self.assertIn("You are a fleet leader session", text)

    def test_includes_leader_role_marker(self) -> None:
        self.assertIn("You are a fleet leader session", self._render())

    def test_is_project_agnostic(self) -> None:
        text = self._render()
        self.assertIn("project-agnostic", text)
        # No startup-injected per-project SELECTION guide heading any more.
        self.assertNotIn("Formation selection guide (this project)", text)

    def test_mentions_first_touch_and_project_flag(self) -> None:
        text = self._render()
        self.assertIn("First-touch", text)
        self.assertIn("--project", text)

    def test_footer_points_at_global_memory(self) -> None:
        text = self._render()
        expected = str(state.global_leader_memory_dir() / "MEMORY.md")
        self.assertIn(f"global memory:  {expected}", text)
        self.assertIn("read this first", text)

    def test_footer_start_hint_has_project_placeholder(self) -> None:
        text = self._render()
        self.assertIn("start <id> --project <name> --formation <name>", text)
        self.assertIn("always pass --project", text)

    def test_mentions_fleet_agent_start(self) -> None:
        self.assertIn("fleet-agent start", self._render())

    def test_mentions_primary_maintainer(self) -> None:
        self.assertIn("PRIMARY maintainer", self._render())

    def test_keeps_prompt_under_budget(self) -> None:
        """Guard against leader-prompt bloat regression (symmetric with driver-prompt guard)."""
        text = self._render()
        line_count = text.count("\n")
        self.assertLess(line_count, 90, f"leader-prompt got fat: {line_count} lines")

    def test_leader_base_md_exists_and_nonempty(self) -> None:
        path = ROOT / "docs" / "prompts" / "leader-base.md"
        self.assertTrue(path.is_file(), "leader-base.md does not exist")
        self.assertGreater(path.stat().st_size, 0, "leader-base.md is empty")


class GlobalMemoryInjectionTests(_FleetHomeBase):
    """Issue #166: render injects the GLOBAL leader-memory index, mirroring the
    driver prompt's MEMORY.md injection (Issue #114)."""

    def _memory_dir(self) -> Path:
        d = state.global_leader_memory_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_injects_global_index_when_present(self) -> None:
        (self._memory_dir() / "MEMORY.md").write_text(
            "# Leader Memory Index (global)\n\n- [Tone](user_tone.md) — terse, hard-boiled\n",
            encoding="utf-8",
        )
        text = leader_prompt.render()
        self.assertIn("## Leader memory (global index)", text)
        self.assertIn("terse, hard-boiled", text)
        # Injected above the metadata footer, not after it.
        self.assertLess(
            text.index("Leader memory (global index)"),
            text.index("global memory:"),
        )

    def test_no_injection_when_index_absent(self) -> None:
        # global/leader-memory/ not scaffolded at all.
        text = leader_prompt.render()
        self.assertNotIn("## Leader memory (global index)", text)

    def test_no_injection_when_index_empty(self) -> None:
        (self._memory_dir() / "MEMORY.md").write_text("   \n", encoding="utf-8")
        text = leader_prompt.render()
        self.assertNotIn("## Leader memory (global index)", text)


class FleetAgentPathInjectionTests(_FleetHomeBase):
    """Issue #143: the leader prompt rewrites ``fleet-agent`` to an absolute path,
    mirroring the driver prompt (Issue #125), so a leader never needs to ``cd``
    into the agent-fleet clone — the ``cd`` is what made cwd-based resolution land
    tasks in the wrong project.
    """

    def test_fleet_agent_bin_is_absolute_and_exists(self) -> None:
        bin_path = leader_prompt.fleet_agent_bin()
        self.assertTrue(Path(bin_path).is_absolute(), bin_path)
        self.assertTrue(Path(bin_path).is_file(), bin_path)
        self.assertEqual(Path(bin_path).name, "fleet-agent")

    def test_render_rewrites_commands_to_absolute_path(self) -> None:
        bin_path = "/opt/agent-fleet/fleet-agent"
        text = leader_prompt.render(fleet_bin=bin_path)
        # The fleet-managed base text uses the absolute path...
        self.assertIn(f"{bin_path} start", text)
        # ...and no bare `fleet-agent` command survives (backtick then bare name).
        self.assertNotIn("`fleet-agent ", text)

    def test_render_default_uses_resolved_bin(self) -> None:
        text = leader_prompt.render()
        self.assertIn(f"{leader_prompt.fleet_agent_bin()} start", text)

    def test_render_quotes_path_with_spaces(self) -> None:
        text = leader_prompt.render(fleet_bin="/opt/agent fleet/fleet-agent")
        self.assertIn("'/opt/agent fleet/fleet-agent' start", text)

    def test_footer_surfaces_project_flag_in_start_hint(self) -> None:
        text = leader_prompt.render(fleet_bin="/opt/agent-fleet/fleet-agent")
        self.assertIn("--project <name>", text)

    def test_render_no_session_label_no_roster_section(self) -> None:
        text = leader_prompt.render()
        self.assertNotIn("Projects in scope", text)
        self.assertNotIn("Projects\n", text)

    def test_render_scoped_session_injects_roster(self) -> None:
        import json
        # Register a project
        proj = Path(self._tmp.name) / "proj"
        proj.mkdir()
        state.register_project("myproj", proj)
        # Create session with scope
        sdir = state.session_dir("main")
        sdir.mkdir(parents=True, exist_ok=True)
        state.session_record_path("main").write_text(
            json.dumps({"label": "main", "scope": ["myproj"]}), encoding="utf-8"
        )
        text = leader_prompt.render(session_label="main")
        self.assertIn("Projects in scope", text)
        self.assertIn("myproj", text)

    def test_render_unscoped_session_injects_unscoped_note(self) -> None:
        import json
        proj = Path(self._tmp.name) / "proj2"
        proj.mkdir()
        state.register_project("myproj2", proj)
        # Session with no scope field
        sdir = state.session_dir("main")
        sdir.mkdir(parents=True, exist_ok=True)
        state.session_record_path("main").write_text(
            json.dumps({"label": "main"}), encoding="utf-8"
        )
        text = leader_prompt.render(session_label="main")
        self.assertIn("unscoped", text)
        self.assertIn("myproj2", text)

    def test_render_empty_registry_omits_section(self) -> None:
        # No projects registered → section should be omitted
        text = leader_prompt.render(session_label="main")
        self.assertNotIn("Projects in scope", text)
        self.assertNotIn("unscoped (serves all", text)


if __name__ == "__main__":
    unittest.main()
