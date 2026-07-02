"""Tests for :mod:`fleet.orchestrator`."""
from __future__ import annotations

import shlex
import subprocess
import sys
import unittest
import unittest.mock
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
    formation: str = "pair_review",
) -> dict:
    task_data = {
        "id": task_id,
        "title": "test task",
        "description": "test description",
        "status": "running",
        "formation": formation,
        "workspace": "none",
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
            formation="solo",
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
        task = _make_task(self.sd, "4", stages, formation="multi_stage")

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
            "formation": "solo",
            "workspace": "none",
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
        (self.sd / "roles" / "code-reviewer.md").write_text(
            "project reviewer role\n", encoding="utf-8"
        )
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
            formation="solo",
        )
        orchestrator.advance(self.sd, "11", task, result="changes-requested", dry_run=True)
        updated = state.load_task(self.sd, "11")
        self.assertNotEqual(updated["status"], "completed")


class PeerReviewLoopTests(unittest.TestCase):
    """Tests for the peer_review loop inside a stage."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.sd = self.project / ".fleet-state"
        state.init_state(self.sd, name="demo")
        (self.sd / "roles" / "code-reviewer.md").write_text(
            "project reviewer role\n", encoding="utf-8"
        )
        (self.sd / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_pr_stage(self, *, with_user_approval: bool = False) -> list[dict]:
        stage: dict = {
            "role": "implementer",
            "agent": "claude:sonnet",
            "status": "running",
            "peer_review": {"role": "code-reviewer"},
        }
        if with_user_approval:
            stage["user_approval"] = {"required": True, "status": "pending"}
        return [stage]

    # ── implementer done → launches reviewer ──────────────────────────────

    def test_implementer_done_transitions_to_reviewing(self) -> None:
        task = _make_task(self.sd, "20", self._make_pr_stage())
        orchestrator.advance(self.sd, "20", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "20")
        pr = updated["stages"][0]["peer_review"]
        self.assertEqual(pr["phase"], "reviewing")
        self.assertEqual(pr["iteration"], 1)
        # Stage is still running (not done yet)
        self.assertEqual(updated["stages"][0]["status"], "running")
        self.assertNotEqual(updated["status"], "completed")

    def test_bare_ask_awaiting_orders_does_not_block_review_handoff(self) -> None:
        task = _make_task(self.sd, "20a", self._make_pr_stage())
        task["status"] = "awaiting_orders"
        state.save_task(self.sd, "20a", task)

        with (
            unittest.mock.patch("fleet.tmux.available", return_value=True),
            unittest.mock.patch(
                "fleet.tmux.task_window_names",
                return_value=["20a·implementer"],
            ),
            unittest.mock.patch("fleet.commands.start.launch_stage_driver") as mock_launch,
        ):
            orchestrator.advance(self.sd, "20a", task, result="approved")

        updated = state.load_task(self.sd, "20a")
        pr = updated["stages"][0]["peer_review"]
        self.assertEqual(updated["status"], "running")
        self.assertEqual(pr["phase"], "reviewing")
        self.assertEqual(pr["iteration"], 1)
        mock_launch.assert_called_once()
        self.assertEqual(mock_launch.call_args.kwargs["stage"]["role"], "code-reviewer")

    # ── reviewer approved → peer_review passed ────────────────────────────

    def test_reviewer_approved_marks_peer_review_approved(self) -> None:
        stages = self._make_pr_stage()
        stages[0]["peer_review"] = {"role": "code-reviewer", "phase": "reviewing", "iteration": 1}
        task = _make_task(self.sd, "21", stages)
        orchestrator.advance(self.sd, "21", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "21")
        pr = updated["stages"][0]["peer_review"]
        self.assertEqual(pr["phase"], "approved")
        # No user_approval → stage should be done
        self.assertEqual(updated["stages"][0]["status"], "done")
        self.assertEqual(updated["status"], "completed")

    # ── reviewer changes-requested → re-launch implementer ───────────────

    def test_reviewer_changes_requested_relaunches_implementer(self) -> None:
        stages = self._make_pr_stage()
        stages[0]["peer_review"] = {"role": "code-reviewer", "phase": "reviewing", "iteration": 1}
        task = _make_task(self.sd, "22", stages)
        orchestrator.advance(self.sd, "22", task, result="changes-requested", dry_run=True)
        updated = state.load_task(self.sd, "22")
        pr = updated["stages"][0]["peer_review"]
        self.assertEqual(pr["phase"], "implementing")
        self.assertEqual(pr["iteration"], 2)
        # Stage still running
        self.assertEqual(updated["stages"][0]["status"], "running")
        self.assertNotEqual(updated["status"], "completed")

    def test_reviewer_changes_requested_iteration_increments(self) -> None:
        stages = self._make_pr_stage()
        stages[0]["peer_review"] = {"role": "code-reviewer", "phase": "reviewing", "iteration": 2}
        task = _make_task(self.sd, "23", stages)
        orchestrator.advance(self.sd, "23", task, result="changes-requested", dry_run=True)
        updated = state.load_task(self.sd, "23")
        pr = updated["stages"][0]["peer_review"]
        self.assertEqual(pr["iteration"], 3)
        self.assertEqual(pr["phase"], "implementing")

    # ── max iterations exceeded → user intervention ───────────────────────

    def test_reviewer_changes_requested_at_max_escalates(self) -> None:
        stages = self._make_pr_stage()
        stages[0]["peer_review"] = {"role": "code-reviewer", "phase": "reviewing", "iteration": 3}
        task = _make_task(self.sd, "24", stages)
        orchestrator.advance(self.sd, "24", task, result="changes-requested", dry_run=True)
        updated = state.load_task(self.sd, "24")
        # Must not advance to next stage
        self.assertNotEqual(updated["status"], "completed")
        self.assertEqual(updated["status"], "awaiting_orders")
        # Stage still running
        self.assertEqual(updated["stages"][0]["status"], "running")
        # questions.md should exist
        qpath = state.task_dir(self.sd, "24") / "questions.md"
        self.assertTrue(qpath.exists())
        self.assertIn("exceeded", qpath.read_text())

    # ── peer_review.max_iterations override ───────────────────────────────

    def test_max_iterations_override_escalates_early(self) -> None:
        # max_iterations=2 → escalate at iteration 2 instead of the default 3.
        stages = self._make_pr_stage()
        stages[0]["peer_review"] = {
            "role": "code-reviewer",
            "phase": "reviewing",
            "iteration": 2,
            "max_iterations": 2,
        }
        task = _make_task(self.sd, "24a", stages)
        orchestrator.advance(self.sd, "24a", task, result="changes-requested", dry_run=True)
        updated = state.load_task(self.sd, "24a")
        self.assertEqual(updated["status"], "awaiting_orders")
        qpath = state.task_dir(self.sd, "24a") / "questions.md"
        self.assertIn("maximum 2 iterations", qpath.read_text())

    def test_max_iterations_override_allows_more(self) -> None:
        # max_iterations=5 → iteration 3 does NOT escalate; it increments to 4.
        stages = self._make_pr_stage()
        stages[0]["peer_review"] = {
            "role": "code-reviewer",
            "phase": "reviewing",
            "iteration": 3,
            "max_iterations": 5,
        }
        task = _make_task(self.sd, "24b", stages)
        orchestrator.advance(self.sd, "24b", task, result="changes-requested", dry_run=True)
        updated = state.load_task(self.sd, "24b")
        pr = updated["stages"][0]["peer_review"]
        self.assertEqual(pr["phase"], "implementing")
        self.assertEqual(pr["iteration"], 4)
        self.assertNotEqual(updated["status"], "awaiting_orders")

    def test_approve_resolves_escalation_with_override_cap(self) -> None:
        # The resume-path escalation predicate honors the override cap, so an
        # iteration that has hit max_iterations=2 is a settleable escalation.
        stages = self._make_pr_stage(with_user_approval=True)
        stages[0]["peer_review"] = {
            "role": "code-reviewer",
            "phase": "reviewing",
            "iteration": 2,
            "max_iterations": 2,
        }
        task = _make_task(self.sd, "24c", stages)
        task["status"] = "awaiting_orders"
        state.save_task(self.sd, "24c", task)

        orchestrator.approve_user_approval(self.sd, "24c", task, dry_run=True)

        updated = state.load_task(self.sd, "24c")
        self.assertEqual(updated["stages"][0]["peer_review"]["phase"], "approved")
        self.assertEqual(updated["status"], "completed")

    def test_approve_resolves_peer_review_escalation(self) -> None:
        stages = self._make_pr_stage(with_user_approval=True)
        stages[0]["peer_review"] = {"role": "code-reviewer", "phase": "reviewing", "iteration": 3}
        task = _make_task(self.sd, "27", stages)
        task["status"] = "awaiting_orders"
        state.save_task(self.sd, "27", task)

        orchestrator.approve_user_approval(self.sd, "27", task, dry_run=True)

        updated = state.load_task(self.sd, "27")
        self.assertEqual(updated["stages"][0]["peer_review"]["phase"], "approved")
        self.assertEqual(updated["stages"][0]["user_approval"]["status"], "approved")
        self.assertEqual(updated["stages"][0]["status"], "done")
        self.assertEqual(updated["status"], "completed")

    def test_reject_resolves_peer_review_escalation(self) -> None:
        stages = self._make_pr_stage(with_user_approval=True)
        stages[0]["peer_review"] = {"role": "code-reviewer", "phase": "reviewing", "iteration": 3}
        task = _make_task(self.sd, "28", stages)
        task["status"] = "awaiting_orders"
        state.save_task(self.sd, "28", task)

        orchestrator.reject_user_approval(self.sd, "28", task, dry_run=True)

        updated = state.load_task(self.sd, "28")
        self.assertEqual(updated["stages"][0]["peer_review"]["phase"], "implementing")
        self.assertEqual(updated["stages"][0]["peer_review"]["iteration"], 4)
        self.assertEqual(updated["stages"][0]["user_approval"]["status"], "pending")
        self.assertEqual(updated["stages"][0]["status"], "running")
        self.assertEqual(updated["status"], "running")

    # ── full peer_review cycle (no user_approval) ─────────────────────────

    def test_full_peer_review_cycle_approved(self) -> None:
        task = _make_task(self.sd, "25", self._make_pr_stage())
        # Step 1: implementer done
        orchestrator.advance(self.sd, "25", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "25")
        self.assertEqual(task["stages"][0]["peer_review"]["phase"], "reviewing")
        # Step 2: reviewer approved
        orchestrator.advance(self.sd, "25", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "25")
        self.assertEqual(task["stages"][0]["peer_review"]["phase"], "approved")
        self.assertEqual(task["stages"][0]["status"], "done")
        self.assertEqual(task["status"], "completed")

    def test_full_peer_review_cycle_with_one_retry(self) -> None:
        task = _make_task(self.sd, "26", self._make_pr_stage())
        # Step 1: implementer done → phase=reviewing
        orchestrator.advance(self.sd, "26", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "26")
        # Step 2: reviewer changes-requested → phase=implementing, iteration=2
        orchestrator.advance(self.sd, "26", task, result="changes-requested", dry_run=True)
        task = state.load_task(self.sd, "26")
        self.assertEqual(task["stages"][0]["peer_review"]["iteration"], 2)
        self.assertEqual(task["stages"][0]["peer_review"]["phase"], "implementing")
        # Step 3: implementer done again → phase=reviewing
        orchestrator.advance(self.sd, "26", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "26")
        self.assertEqual(task["stages"][0]["peer_review"]["phase"], "reviewing")
        # Step 4: reviewer approved → stage done
        orchestrator.advance(self.sd, "26", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "26")
        self.assertEqual(task["stages"][0]["status"], "done")
        self.assertEqual(task["status"], "completed")

    def test_first_review_handoff_launches_reviewer_without_killing_implementer(self) -> None:
        task = _make_task(self.sd, "29", self._make_pr_stage())

        with (
            unittest.mock.patch("fleet.tmux.available", return_value=True),
            unittest.mock.patch(
                "fleet.tmux.task_window_names",
                return_value=["29·implementer"],
            ),
            unittest.mock.patch("fleet.commands.start.launch_stage_driver") as mock_launch,
        ):
            orchestrator.advance(self.sd, "29", task, result="approved")

        mock_launch.assert_called_once()
        kwargs = mock_launch.call_args.kwargs
        self.assertEqual(kwargs["stage"]["role"], "code-reviewer")
        self.assertFalse(kwargs["replace_task_windows"])

    def test_rework_handoff_wakes_existing_implementer_without_relaunch(self) -> None:
        stages = self._make_pr_stage()
        stages[0]["peer_review"] = {"role": "code-reviewer", "phase": "reviewing", "iteration": 1}
        task = _make_task(self.sd, "2a", stages)

        with (
            unittest.mock.patch("fleet.tmux.available", return_value=True),
            unittest.mock.patch(
                "fleet.tmux.task_window_names",
                return_value=["2a·implementer", "2a·code-reviewer"],
            ),
            unittest.mock.patch("fleet.tmux.send_keys") as mock_send,
            unittest.mock.patch("fleet.commands.start.launch_stage_driver") as mock_launch,
        ):
            orchestrator.advance(self.sd, "2a", task, result="changes-requested")

        mock_launch.assert_not_called()
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[1], "2a·implementer")
        inbox = state.task_dir(self.sd, "2a") / "inbox.md"
        self.assertIn("role=implementer", inbox.read_text(encoding="utf-8"))


class UserApprovalGateTests(unittest.TestCase):
    """Tests for the user_approval gate."""

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

    # ── solo stage with user_approval ─────────────────────────────────────

    def test_user_approval_pending_transitions_to_asked(self) -> None:
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "pending"},
            }
        ]
        task = _make_task(self.sd, "30", stages, formation="solo")
        orchestrator.advance(self.sd, "30", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "30")
        ua = updated["stages"][0]["user_approval"]
        self.assertEqual(ua["status"], "asked")
        self.assertEqual(updated["status"], "awaiting_orders")
        # Stage should NOT be done yet
        self.assertNotEqual(updated["stages"][0]["status"], "done")

    def test_bare_ask_awaiting_orders_allows_user_approval_gate_to_raise(self) -> None:
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "pending"},
            }
        ]
        task = _make_task(self.sd, "30a", stages, formation="solo")
        task["status"] = "awaiting_orders"
        state.save_task(self.sd, "30a", task)

        orchestrator.advance(self.sd, "30a", task, result="approved", dry_run=True)

        updated = state.load_task(self.sd, "30a")
        self.assertEqual(updated["status"], "awaiting_orders")
        self.assertEqual(updated["stages"][0]["user_approval"]["status"], "asked")
        self.assertNotEqual(updated["stages"][0]["status"], "done")

    def test_driver_done_cannot_self_approve_asked_gate(self) -> None:
        # Reproduce the two-call pattern observed in task-fleet-html-dashboard:
        # first advance raises the gate (pending→asked, awaiting_orders);
        # second advance must be a no-op — gate stays asked, task stays awaiting.
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "pending"},
            }
        ]
        task = _make_task(self.sd, "31", stages, formation="solo")
        # First call: pending → asked, task → awaiting_orders
        orchestrator.advance(self.sd, "31", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "31")
        self.assertEqual(task["status"], "awaiting_orders")
        # Second call: driver tries to self-approve — must be a no-op
        orchestrator.advance(self.sd, "31", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "31")
        ua = updated["stages"][0]["user_approval"]
        self.assertEqual(ua["status"], "asked")
        self.assertNotEqual(updated["stages"][0]["status"], "done")
        self.assertNotEqual(updated["status"], "completed")
        self.assertEqual(updated["status"], "awaiting_orders")

    def test_driver_done_changes_requested_cannot_self_reject_asked_gate(self) -> None:
        # A driver calling done --result changes-requested at the gate must not
        # settle (reject) it — awaiting_orders is the human's turn.
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "pending"},
            }
        ]
        task = _make_task(self.sd, "36", stages, formation="solo")
        # First call: raise the gate
        orchestrator.advance(self.sd, "36", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "36")
        self.assertEqual(task["status"], "awaiting_orders")
        # Second call with changes-requested: must be a no-op
        orchestrator.advance(
            self.sd, "36", task, result="changes-requested", dry_run=True
        )
        updated = state.load_task(self.sd, "36")
        ua = updated["stages"][0]["user_approval"]
        self.assertEqual(ua["status"], "asked")
        self.assertEqual(updated["status"], "awaiting_orders")

    def test_layer2_desynced_asked_running_does_not_settle(self) -> None:
        # Layer 2: if task.status is somehow "running" but the gate is "asked"
        # (desync), a driver's done must still not settle the gate — it must
        # re-assert awaiting_orders and leave the gate intact.
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "asked"},
            }
        ]
        # Build task with status="running" (desync: gate is asked but task is running)
        task = _make_task(self.sd, "37", stages, formation="solo")
        # task.status is "running" here; Layer 1 does NOT fire (it only guards
        # awaiting_orders). Layer 2 must catch this.
        orchestrator.advance(self.sd, "37", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "37")
        ua = updated["stages"][0]["user_approval"]
        self.assertEqual(ua["status"], "asked")
        self.assertNotEqual(updated["stages"][0]["status"], "done")
        self.assertNotEqual(updated["status"], "completed")
        self.assertEqual(updated["status"], "awaiting_orders")

    def test_driver_done_during_peer_review_escalation_is_noop(self) -> None:
        # After a peer_review escalation the task is awaiting_orders
        # (phase=reviewing, iteration>=3). A driver's done must not advance it.
        stages = [
            {
                "role": "implementer",
                "agent": "claude:sonnet",
                "status": "running",
                "peer_review": {
                    "role": "code-reviewer",
                    "phase": "reviewing",
                    "iteration": 3,
                },
            }
        ]
        task = _make_task(self.sd, "38", stages)
        task["status"] = "awaiting_orders"
        state.save_task(self.sd, "38", task)

        orchestrator.advance(self.sd, "38", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "38")
        self.assertEqual(updated["status"], "awaiting_orders")
        self.assertNotEqual(updated["stages"][0]["status"], "done")
        pr = updated["stages"][0]["peer_review"]
        self.assertEqual(pr["phase"], "reviewing")

    def test_user_approval_writes_questions_md(self) -> None:
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "pending"},
            }
        ]
        task = _make_task(self.sd, "32", stages, formation="solo")
        orchestrator.advance(self.sd, "32", task, result="approved", dry_run=True)
        qpath = state.task_dir(self.sd, "32") / "questions.md"
        self.assertTrue(qpath.exists())
        content = qpath.read_text()
        self.assertIn("approval", content)
        self.assertIn("Tell the leader", content)
        self.assertNotIn("fleet-agent done", content)

    # ── peer_review + user_approval combined ─────────────────────────────

    def test_peer_review_then_user_approval_full_cycle(self) -> None:
        stages = [
            {
                "role": "implementer",
                "agent": "claude:sonnet",
                "status": "running",
                "peer_review": {"role": "code-reviewer"},
                "user_approval": {"required": True, "status": "pending"},
            }
        ]
        task = _make_task(self.sd, "33", stages)
        # Step 1: implementer done → launch reviewer
        orchestrator.advance(self.sd, "33", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "33")
        self.assertEqual(task["stages"][0]["peer_review"]["phase"], "reviewing")

        # Step 2: reviewer approved → user_approval.status = "asked"
        orchestrator.advance(self.sd, "33", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "33")
        self.assertEqual(task["stages"][0]["peer_review"]["phase"], "approved")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "asked")
        self.assertEqual(task["status"], "awaiting_orders")
        self.assertNotEqual(task["stages"][0]["status"], "done")

        # Step 3: user approves → stage done → task completed
        orchestrator.approve_user_approval(self.sd, "33", task, dry_run=True)
        task = state.load_task(self.sd, "33")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "approved")
        self.assertEqual(task["stages"][0]["status"], "done")
        self.assertEqual(task["status"], "completed")

    def test_user_reject_resets_peer_review_to_implementation(self) -> None:
        stages = [
            {
                "role": "implementer",
                "agent": "claude:sonnet",
                "status": "running",
                "peer_review": {"role": "code-reviewer", "phase": "approved", "iteration": 1},
                "user_approval": {"required": True, "status": "asked"},
            }
        ]
        task = _make_task(self.sd, "35", stages)
        orchestrator.reject_user_approval(self.sd, "35", task, dry_run=True)
        task = state.load_task(self.sd, "35")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["stages"][0]["status"], "running")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "pending")
        self.assertEqual(task["stages"][0]["peer_review"]["phase"], "implementing")
        self.assertEqual(task["stages"][0]["peer_review"]["iteration"], 2)

    def test_user_reject_wakes_existing_implementer_without_relaunch(self) -> None:
        stages = [
            {
                "role": "implementer",
                "agent": "claude:sonnet",
                "status": "running",
                "peer_review": {"role": "code-reviewer", "phase": "approved", "iteration": 1},
                "user_approval": {"required": True, "status": "asked"},
            }
        ]
        task = _make_task(self.sd, "35a", stages)

        with (
            unittest.mock.patch("fleet.tmux.available", return_value=True),
            unittest.mock.patch(
                "fleet.tmux.task_window_names",
                return_value=["35a·implementer", "35a·code-reviewer"],
            ),
            unittest.mock.patch("fleet.tmux.send_keys") as mock_send,
            unittest.mock.patch("fleet.commands.start.launch_stage_driver") as mock_launch,
        ):
            orchestrator.reject_user_approval(self.sd, "35a", task)

        mock_launch.assert_not_called()
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[1], "35a·implementer")
        inbox = state.task_dir(self.sd, "35a") / "inbox.md"
        self.assertIn("role=implementer", inbox.read_text(encoding="utf-8"))

    # ── user_approval.status yaml fields updated correctly ────────────────

    def test_user_approval_status_field_in_yaml(self) -> None:
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "pending"},
            }
        ]
        task = _make_task(self.sd, "34", stages, formation="solo")
        # pending → asked
        orchestrator.advance(self.sd, "34", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "34")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "asked")
        # asked → approved (leader relays user approval)
        orchestrator.approve_user_approval(self.sd, "34", task, dry_run=True)
        task = state.load_task(self.sd, "34")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "approved")


class VerifyGateTests(unittest.TestCase):
    """Tests for the verify gate that runs before downstream stage gates."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.sd = self.project / ".fleet-state"
        state.init_state(self.sd, name="demo", repo=self.project)
        (self.sd / "roles" / "code-reviewer.md").write_text(
            "project reviewer role\n", encoding="utf-8"
        )
        (self.sd / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _python(self, code: str) -> str:
        return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"

    def test_verify_pass_advances_to_user_approval(self) -> None:
        command = self._python("from pathlib import Path; Path('verified.txt').write_text('ok')")
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "verify": {"command": command},
                "user_approval": {"required": True, "status": "pending"},
            }
        ]
        task = _make_task(self.sd, "v1", stages, formation="solo")

        orchestrator.advance(self.sd, "v1", task, result="approved", dry_run=True)

        updated = state.load_task(self.sd, "v1")
        self.assertTrue((self.project / "verified.txt").exists())
        self.assertTrue(updated["stages"][0]["verify"]["passed"])
        self.assertEqual(updated["stages"][0]["user_approval"]["status"], "asked")
        self.assertEqual(updated["status"], "awaiting_orders")

    def test_verify_failure_bounces_with_head_tail_output(self) -> None:
        command = self._python(
            "import sys; print('HEAD'); print('x' * 13000); print('TAIL'); sys.exit(7)"
        )
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "verify": {"command": command, "max_iterations": 3},
                "user_approval": {"required": True, "status": "pending"},
            }
        ]
        task = _make_task(self.sd, "v2", stages, formation="solo")

        orchestrator.advance(self.sd, "v2", task, result="approved", dry_run=True)

        updated = state.load_task(self.sd, "v2")
        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["stages"][0]["verify"]["iteration"], 2)
        self.assertEqual(updated["stages"][0]["user_approval"]["status"], "pending")
        inbox = (state.task_dir(self.sd, "v2") / "inbox.md").read_text(encoding="utf-8")
        self.assertIn("HEAD", inbox)
        self.assertIn("TAIL", inbox)
        self.assertIn("exited 7", inbox)
        self.assertIn("truncated", inbox)

    def test_peer_review_launches_only_after_verify_passes(self) -> None:
        marker = self.project / "allow"
        command = self._python(
            "from pathlib import Path; import sys; "
            "sys.exit(0 if Path('allow').exists() else 9)"
        )
        stages = [
            {
                "role": "implementer",
                "agent": "claude:sonnet",
                "status": "running",
                "verify": {"command": command},
                "peer_review": {"role": "code-reviewer"},
            }
        ]
        task = _make_task(self.sd, "v3", stages)

        orchestrator.advance(self.sd, "v3", task, result="approved", dry_run=True)
        failed = state.load_task(self.sd, "v3")
        self.assertNotIn("phase", failed["stages"][0]["peer_review"])

        marker.write_text("ok", encoding="utf-8")
        orchestrator.advance(self.sd, "v3", failed, result="approved", dry_run=True)

        updated = state.load_task(self.sd, "v3")
        self.assertEqual(updated["stages"][0]["peer_review"]["phase"], "reviewing")
        self.assertTrue(updated["stages"][0]["verify"]["passed"])

    def test_peer_review_rework_reruns_verify(self) -> None:
        command = self._python(
            "from pathlib import Path; "
            "p=Path('verify-count.txt'); "
            "n=int(p.read_text() or '0') if p.exists() else 0; "
            "p.write_text(str(n+1))"
        )
        stages = [
            {
                "role": "implementer",
                "agent": "claude:sonnet",
                "status": "running",
                "verify": {"command": command},
                "peer_review": {"role": "code-reviewer"},
            }
        ]
        task = _make_task(self.sd, "v4", stages)

        orchestrator.advance(self.sd, "v4", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "v4")
        orchestrator.advance(self.sd, "v4", task, result="changes-requested", dry_run=True)
        task = state.load_task(self.sd, "v4")
        self.assertFalse(task["stages"][0]["verify"]["passed"])
        orchestrator.advance(self.sd, "v4", task, result="approved", dry_run=True)

        self.assertEqual((self.project / "verify-count.txt").read_text(), "2")
        updated = state.load_task(self.sd, "v4")
        self.assertEqual(updated["stages"][0]["peer_review"]["phase"], "reviewing")

    def test_user_rejection_reruns_verify_before_next_review(self) -> None:
        command = self._python(
            "from pathlib import Path; "
            "p=Path('approval-count.txt'); "
            "n=int(p.read_text() or '0') if p.exists() else 0; "
            "p.write_text(str(n+1))"
        )
        stages = [
            {
                "role": "implementer",
                "agent": "claude:sonnet",
                "status": "running",
                "verify": {"command": command},
                "peer_review": {"role": "code-reviewer"},
                "user_approval": {"required": True, "status": "pending"},
            }
        ]
        task = _make_task(self.sd, "v5", stages)

        orchestrator.advance(self.sd, "v5", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "v5")
        orchestrator.advance(self.sd, "v5", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "v5")
        self.assertEqual(task["status"], "awaiting_orders")

        orchestrator.reject_user_approval(self.sd, "v5", task, dry_run=True)
        task = state.load_task(self.sd, "v5")
        self.assertFalse(task["stages"][0]["verify"]["passed"])
        orchestrator.advance(self.sd, "v5", task, result="approved", dry_run=True)

        self.assertEqual((self.project / "approval-count.txt").read_text(), "2")
        updated = state.load_task(self.sd, "v5")
        self.assertEqual(updated["stages"][0]["peer_review"]["phase"], "reviewing")

    def test_verify_timeout_uses_default_and_bounces(self) -> None:
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "verify": {"command": "slow-check"},
            }
        ]
        task = _make_task(self.sd, "v6", stages, formation="solo")

        with unittest.mock.patch(
            "fleet.orchestrator.subprocess.run",
            side_effect=subprocess.TimeoutExpired("slow-check", 600, output="partial"),
        ) as mock_run:
            orchestrator.advance(self.sd, "v6", task, result="approved", dry_run=True)

        self.assertEqual(mock_run.call_args.kwargs["timeout"], 600)
        updated = state.load_task(self.sd, "v6")
        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["stages"][0]["verify"]["iteration"], 2)
        inbox = (state.task_dir(self.sd, "v6") / "inbox.md").read_text(encoding="utf-8")
        self.assertIn("timed out", inbox)
        self.assertIn("partial", inbox)

    def test_verify_max_iterations_escalates(self) -> None:
        command = self._python("import sys; print('nope'); sys.exit(1)")
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "verify": {"command": command, "max_iterations": 2},
            }
        ]
        task = _make_task(self.sd, "v7", stages, formation="solo")

        orchestrator.advance(self.sd, "v7", task, result="approved", dry_run=True)
        task = state.load_task(self.sd, "v7")
        orchestrator.advance(self.sd, "v7", task, result="approved", dry_run=True)

        updated = state.load_task(self.sd, "v7")
        self.assertEqual(updated["status"], "awaiting_orders")
        self.assertTrue(updated["stages"][0]["verify"]["escalated"])
        qpath = state.task_dir(self.sd, "v7") / "questions.md"
        self.assertIn("verify", qpath.read_text())
        self.assertIn("maximum 2 iterations", qpath.read_text())

    def test_workspace_none_runs_verify_in_project_root(self) -> None:
        command = self._python("from pathlib import Path; Path('root-cwd.txt').write_text('root')")
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "verify": {"command": command},
            }
        ]
        task = _make_task(self.sd, "v8", stages, formation="solo")

        orchestrator.advance(self.sd, "v8", task, result="approved", dry_run=True)

        self.assertTrue((self.project / "root-cwd.txt").exists())

    def test_workspace_worktree_runs_verify_in_worktree(self) -> None:
        worktree = Path(self._tmp.name) / "task-worktree"
        worktree.mkdir()
        command = self._python("from pathlib import Path; Path('worktree-cwd.txt').write_text('wt')")
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "verify": {"command": command},
            }
        ]
        task = _make_task(self.sd, "v9", stages, formation="solo")
        task["workspace"] = "worktree"
        task["worktree"] = str(worktree)
        state.save_task(self.sd, "v9", task)

        orchestrator.advance(self.sd, "v9", task, result="approved", dry_run=True)

        self.assertTrue((worktree / "worktree-cwd.txt").exists())
        self.assertFalse((self.project / "worktree-cwd.txt").exists())


class WindowCwdTests(unittest.TestCase):
    """Verify _launch_driver_for_stage passes window_cwd correctly depending on whether a worktree exists."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.sd = self.project / ".fleet-state"
        state.init_state(self.sd, name="demo")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_task(self, task_id: str, worktree: str | None) -> dict:
        task_data: dict = {
            "id": task_id,
            "title": "test task",
            "description": "test description",
            "status": "running",
            "formation": "pair_review",
            "workspace": "worktree" if worktree else "none",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:sonnet", "status": "running"}],
        }
        if worktree is not None:
            task_data["worktree"] = worktree
        state.save_task(self.sd, task_id, task_data)
        task_dir = state.task_dir(self.sd, task_id)
        (task_dir / "driver-prompt.md").write_text("test prompt", encoding="utf-8")
        (task_dir / "inbox.md").write_text("", encoding="utf-8")
        return task_data

    def test_worktree_task_passes_window_cwd_as_worktree_path(self) -> None:
        worktree_path = "/tmp/fake-worktree-wt1"
        task = self._make_task("wt1", worktree=worktree_path)
        stage = task["stages"][0]
        project = state.load_project(self.sd)
        with (
            unittest.mock.patch("fleet.tmux.available", return_value=True),
            unittest.mock.patch("fleet.driver_prompt.render", return_value="mocked prompt") as mock_render,
            unittest.mock.patch("fleet.commands.start.launch_stage_driver") as mock_launch,
        ):
            orchestrator._launch_driver_for_stage(self.sd, "wt1", task, 0, stage)

        mock_launch.assert_called_once()
        kwargs = mock_launch.call_args.kwargs
        self.assertEqual(kwargs["window_cwd"], Path(worktree_path))
        mock_render.assert_called_once()

    def test_no_worktree_task_passes_window_cwd_none(self) -> None:
        task = self._make_task("wt2", worktree=None)
        stage = task["stages"][0]

        with (
            unittest.mock.patch("fleet.tmux.available", return_value=True),
            unittest.mock.patch("fleet.driver_prompt.render", return_value="mocked prompt") as mock_render,
            unittest.mock.patch("fleet.commands.start.launch_stage_driver") as mock_launch,
        ):
            orchestrator._launch_driver_for_stage(self.sd, "wt2", task, 0, stage)

        mock_launch.assert_called_once()
        kwargs = mock_launch.call_args.kwargs
        self.assertIsNone(kwargs.get("window_cwd"))
        mock_render.assert_called_once()

    def test_tmux_unavailable_skips_launch(self) -> None:
        task = self._make_task("wt3", worktree="/tmp/fake-worktree-wt3")
        stage = task["stages"][0]

        with (
            unittest.mock.patch("fleet.tmux.available", return_value=False),
            unittest.mock.patch("fleet.commands.start.launch_stage_driver") as mock_launch,
        ):
            orchestrator._launch_driver_for_stage(self.sd, "wt3", task, 0, stage)

        mock_launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
