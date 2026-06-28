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

    def test_awaiting_orders_highlight(self) -> None:
        state.save_task(self.sd, "1", {
            "id": "1", "title": "T", "status": "awaiting_orders",
            "agent": "claude:sonnet",
        })
        text = dashboard.render(self.sd)
        self.assertIn("## ⚠ Awaiting orders", text)
        self.assertIn("task-1", text)
        # Body has both the highlight block AND the standard table.
        self.assertIn("| 1 |", text)

    def test_workspace_column(self) -> None:
        state.save_task(self.sd, "1", {
            "id": "1", "title": "T", "status": "running",
            "agent": "claude:sonnet", "workspace": "worktree",
        })
        text = dashboard.render(self.sd)
        self.assertIn("worktree", text)

    def test_tokens_column_present(self) -> None:
        state.save_task(self.sd, "1", {
            "id": "1", "title": "T", "status": "completed",
            "agent": "claude:opus",
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        })
        text = dashboard.render(self.sd)
        self.assertIn("| Tokens |", text)
        self.assertIn("1.5k · —", text)

    def test_tokens_column_dash_when_absent(self) -> None:
        state.save_task(self.sd, "1", {
            "id": "1", "title": "T", "status": "running", "agent": "claude:opus",
        })
        text = dashboard.render(self.sd)
        # Header always present; the cell is "—" with no recorded usage.
        self.assertIn("| Tokens |", text)

    def test_review_loop_position_in_status_cell(self) -> None:
        state.save_task(self.sd, "1", {
            "id": "1", "title": "T", "status": "running",
            "formation": "pair_review", "current_stage": 0,
            "stages": [{
                "role": "driver", "agent": "codex:gpt-5.5", "status": "running",
                "peer_review": {"phase": "reviewing", "iteration": 2},
            }],
        })
        text = dashboard.render(self.sd)
        # n/max position is surfaced (not the bare iteration count).
        self.assertIn("review 2/3", text)
        self.assertNotIn("review ×2", text)

    def test_review_loop_position_honors_explicit_max(self) -> None:
        state.save_task(self.sd, "1", {
            "id": "1", "title": "T", "status": "running",
            "formation": "pair_review", "current_stage": 0,
            "stages": [{
                "role": "driver", "agent": "codex:gpt-5.5", "status": "running",
                "peer_review": {
                    "phase": "reviewing", "iteration": 2, "max_iterations": 5,
                },
            }],
        })
        text = dashboard.render(self.sd)
        self.assertIn("review 2/5", text)

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
