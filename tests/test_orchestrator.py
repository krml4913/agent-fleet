"""Tests for :mod:`fleet.orchestrator`."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import orchestrator, state  # noqa: E402


def _make_task(
    state_dir: Path,
    task_id: str,
    stages: list[dict],
    *,
    current_stage: int = 0,
    topology: str = "pair_review",
) -> dict:
    task_data = {
        "id": task_id,
        "title": "test task",
        "description": "test description",
        "status": "running",
        "topology": topology,
        "workflow": "bare",
        "current_stage": current_stage,
        "stages": stages,
    }
    state.save_task(state_dir, task_id, task_data)
    task_dir = state.task_dir(state_dir, task_id)
    (task_dir / "driver-prompt.md").write_text("test prompt", encoding="utf-8")
    (task_dir / "inbox.md").write_text("", encoding="utf-8")
    (task_dir / "outbox.md").write_text("", encoding="utf-8")
    return task_data


class AdvanceApprovedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.sd = self.project / ".fleet-state"
        state.init_state(self.sd, name="demo")
        (self.sd / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_solo_stage_approved_completes_task(self) -> None:
        task = _make_task(
            self.sd,
            "1",
            [{"role": "driver", "agent": "claude:sonnet", "status": "running"}],
            topology="solo",
        )
        orchestrator.advance(self.sd, "1", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "1")
        self.assertEqual(updated["stages"][0]["status"], "done")
        self.assertEqual(updated["status"], "completed")

    def test_multi_stage_approved_advances_to_next(self) -> None:
        task = _make_task(
            self.sd,
            "2",
            [
                {"role": "implementer", "agent": "claude:sonnet", "status": "running"},
                {"role": "reviewer", "agent": "claude:opus", "status": "pending"},
            ],
        )
        orchestrator.advance(self.sd, "2", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "2")
        self.assertEqual(updated["stages"][0]["status"], "done")
        self.assertEqual(updated["stages"][1]["status"], "running")
        self.assertEqual(updated["current_stage"], 1)
        self.assertEqual(updated["status"], "running")

    def test_last_stage_approved_completes_task(self) -> None:
        task = _make_task(
            self.sd,
            "3",
            [
                {"role": "implementer", "agent": "claude:sonnet", "status": "done"},
                {"role": "reviewer", "agent": "claude:opus", "status": "running"},
            ],
            current_stage=1,
        )
        orchestrator.advance(self.sd, "3", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "3")
        self.assertEqual(updated["stages"][0]["status"], "done")
        self.assertEqual(updated["stages"][1]["status"], "done")
        self.assertEqual(updated["status"], "completed")

    def test_three_stage_approved_advances_step_by_step(self) -> None:
        stages = [
            {"role": "designer", "agent": "claude:opus", "status": "running"},
            {"role": "implementer", "agent": "claude:sonnet", "status": "pending"},
            {"role": "reviewer", "agent": "claude:opus", "status": "pending"},
        ]
        task = _make_task(self.sd, "4", stages, topology="multi_stage")

        # Stage 0 → done, stage 1 → running
        orchestrator.advance(self.sd, "4", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "4")
        self.assertEqual(updated["current_stage"], 1)
        self.assertEqual(updated["stages"][1]["status"], "running")
        self.assertEqual(updated["status"], "running")

        # Stage 1 → done, stage 2 → running
        orchestrator.advance(self.sd, "4", updated, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "4")
        self.assertEqual(updated["current_stage"], 2)
        self.assertEqual(updated["stages"][2]["status"], "running")

        # Stage 2 → done → task completed
        orchestrator.advance(self.sd, "4", updated, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "4")
        self.assertEqual(updated["status"], "completed")

    def test_no_stages_approved_completes_task(self) -> None:
        task_data = {
            "id": "5",
            "title": "legacy",
            "status": "running",
            "topology": "solo",
            "workflow": "bare",
        }
        state.save_task(self.sd, "5", task_data)
        task = state.load_task(self.sd, "5")
        orchestrator.advance(self.sd, "5", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "5")
        self.assertEqual(updated["status"], "completed")


class AdvanceChangesRequestedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.sd = self.project / ".fleet-state"
        state.init_state(self.sd, name="demo")
        (self.sd / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_changes_requested_leaves_current_stage(self) -> None:
        task = _make_task(
            self.sd,
            "10",
            [
                {"role": "implementer", "agent": "claude:sonnet", "status": "running"},
                {"role": "reviewer", "agent": "claude:opus", "status": "pending"},
            ],
        )
        orchestrator.advance(self.sd, "10", task, result="changes-requested", dry_run=True)
        updated = state.load_task(self.sd, "10")
        # Stage stays current; next stage not launched
        self.assertEqual(updated["current_stage"], 0)
        self.assertEqual(updated["stages"][0]["result"], "changes-requested")
        self.assertEqual(updated["stages"][1]["status"], "pending")

    def test_changes_requested_does_not_complete_task(self) -> None:
        task = _make_task(
            self.sd,
            "11",
            [{"role": "driver", "agent": "claude:sonnet", "status": "running"}],
            topology="solo",
        )
        orchestrator.advance(self.sd, "11", task, result="changes-requested", dry_run=True)
        updated = state.load_task(self.sd, "11")
        self.assertNotEqual(updated["status"], "completed")


if __name__ == "__main__":
    unittest.main()
