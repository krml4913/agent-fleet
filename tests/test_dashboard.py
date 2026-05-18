"""Tests for ``fleet.dashboard.render`` — content structure."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import dashboard, state  # noqa: E402
from fleet.events import append_event  # noqa: E402


class DashboardRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        state.init_state(self.project / ".fleet-state", name="demo")
        self.sd = self.project / ".fleet-state"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_render_has_header_and_empty_tasks(self) -> None:
        text = dashboard.render(self.sd)
        self.assertIn("# fleet — demo", text)
        self.assertIn("## Tasks", text)
        self.assertIn("(no tasks yet)", text)
        self.assertIn("## Recent events", text)

    def test_needs_input_highlight(self) -> None:
        state.save_task(self.sd, "1", {
            "id": "1", "title": "T", "status": "needs_input",
            "agent": "claude:sonnet",
        })
        text = dashboard.render(self.sd)
        self.assertIn("## ⚠ Needs your input", text)
        self.assertIn("task-1", text)
        # Body has both the highlight block AND the standard table.
        self.assertIn("| 1 |", text)

    def test_workflow_column(self) -> None:
        state.save_task(self.sd, "1", {
            "id": "1", "title": "T", "status": "running",
            "agent": "claude:sonnet", "workflow": "git_worktree",
        })
        text = dashboard.render(self.sd)
        self.assertIn("git_worktree", text)

    def test_recent_events_shown(self) -> None:
        append_event(self.sd / "events.jsonl", "milestone", task_id="1", note="x")
        text = dashboard.render(self.sd)
        self.assertIn("milestone", text)
        self.assertIn("task-1", text)

    def test_last_seen_populated(self) -> None:
        state.save_task(self.sd, "1", {
            "id": "1", "title": "T", "status": "spawning",
            "agent": "claude:sonnet",
        })
        append_event(self.sd / "events.jsonl", "heartbeat", task_id="1")
        text = dashboard.render(self.sd)
        # Some "s ago" should appear in the table row for task-1.
        self.assertRegex(text, r"\| 1 \|[^\n]+ago")


if __name__ == "__main__":
    unittest.main()
