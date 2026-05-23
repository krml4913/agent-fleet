"""Tests for :mod:`fleet.orchestrator`."""
from __future__ import annotations

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

    def test_user_approval_asked_approves_and_completes(self) -> None:
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "asked"},
            }
        ]
        task = _make_task(self.sd, "31", stages, formation="solo")
        orchestrator.advance(self.sd, "31", task, result="approved", dry_run=True)
        updated = state.load_task(self.sd, "31")
        ua = updated["stages"][0]["user_approval"]
        self.assertEqual(ua["status"], "approved")
        self.assertEqual(updated["stages"][0]["status"], "done")
        self.assertEqual(updated["status"], "completed")

    def test_user_approval_asked_changes_requested_rejects(self) -> None:
        stages = [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "asked"},
            }
        ]
        task = _make_task(self.sd, "36", stages, formation="solo")
        orchestrator.advance(
            self.sd, "36", task, result="changes-requested", dry_run=True
        )
        updated = state.load_task(self.sd, "36")
        ua = updated["stages"][0]["user_approval"]
        self.assertEqual(ua["status"], "pending")
        self.assertEqual(updated["stages"][0]["status"], "running")
        self.assertEqual(updated["status"], "running")

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


class WindowCwdTests(unittest.TestCase):
    """_launch_driver_for_stage が worktree の有無で window_cwd を正しく渡すことを検証。"""

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
            "workflow": "git_worktree" if worktree else "bare",
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
        project["workflow"] = "git_worktree"
        state.save_project(self.sd, project)

        with (
            unittest.mock.patch("fleet.tmux.available", return_value=True),
            unittest.mock.patch("fleet.driver_prompt.render", return_value="mocked prompt") as mock_render,
            unittest.mock.patch("fleet.commands.start.launch_stage_driver") as mock_launch,
        ):
            orchestrator._launch_driver_for_stage(self.sd, "wt1", task, 0, stage)

        mock_launch.assert_called_once()
        kwargs = mock_launch.call_args.kwargs
        self.assertEqual(kwargs["window_cwd"], Path(worktree_path))
        self.assertIn("Git workflow", mock_render.call_args.kwargs["workflow_fragment"])

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
        self.assertEqual(mock_render.call_args.kwargs["workflow_fragment"], "")

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
