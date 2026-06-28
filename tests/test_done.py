"""Tests for ``fleet done``."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet-agent"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402


def _solo_task_data(task_id: str = "1", status: str = "spawning") -> dict:
    return {
        "id": task_id, "title": "x", "status": status,
        "formation": "solo", "workspace": "none",
        "current_stage": 0,
        "stages": [{"role": "driver", "agent": "claude:sonnet", "status": "running"}],
    }


class DoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo", repo=self.project)
        (self.state_dir / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n"
        )
        state.save_task(self.state_dir, "1", _solo_task_data("1"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("FLEET_TASK_ID", None)
        env["FLEET_STATE_DIR"] = str(self.state_dir)
        return subprocess.run(
            [sys.executable, str(FLEET), *args],
            capture_output=True, text=True,
            cwd=str(cwd) if cwd else str(self.project),
            env=env,
        )

    def test_done_with_explicit_id(self) -> None:
        r = self._run("done", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["stages"][0]["status"], "done")
        events_path = self.state_dir / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        self.assertTrue(any(e["type"] == "done" for e in events))

    def test_done_from_cwd(self) -> None:
        task_dir = self.state_dir / "tasks" / "task-1"
        r = self._run("done", cwd=task_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["status"], "completed")

    def test_done_unknown_task(self) -> None:
        r = self._run("done", "999")
        self.assertEqual(r.returncode, 1)
        self.assertIn("task.yaml missing", r.stderr)

    def test_done_multi_stage_advances_current_stage(self) -> None:
        sd = self.state_dir
        state.save_task(sd, "2", {
            "id": "2", "title": "multi", "description": "pair review task",
            "status": "spawning", "formation": "pair_review", "workspace": "none",
            "current_stage": 0,
            "stages": [
                {"role": "implementer", "agent": "claude:sonnet", "status": "running"},
                {"role": "reviewer", "agent": "claude:opus", "status": "pending"},
            ],
        })
        task_dir = sd / "tasks" / "task-2"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "driver-prompt.md").write_text("test prompt")
        (task_dir / "inbox.md").write_text("")
        (task_dir / "outbox.md").write_text("")

        from fleet.commands import done as done_mod

        with (
            unittest.mock.patch("fleet.orchestrator._launch_driver_for_stage"),
            unittest.mock.patch(
                "fleet.commands.done.task_context.resolve",
                return_value=(sd, "2"),
            ),
        ):
            ret = done_mod.run(argparse.Namespace(task_id="2", result="approved"))

        self.assertEqual(ret, 0)
        task = state.load_task(sd, "2")
        self.assertEqual(task["stages"][0]["status"], "done")
        self.assertEqual(task["stages"][1]["status"], "running")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["current_stage"], 1)

    def test_done_with_result_approved(self) -> None:
        r = self._run("done", "--result", "approved", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["stages"][0]["status"], "done")

    def test_done_with_result_changes_requested(self) -> None:
        r = self._run("done", "--result", "changes-requested", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        task = state.load_task(self.state_dir, "1")
        self.assertNotEqual(task["status"], "completed")


class DoneNotifyTests(unittest.TestCase):
    """Verify notify.send picks the right title / message for each status."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo", repo=self.project)
        (self.state_dir / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_done(self, task_id: str, result: str = "approved") -> tuple[int, str, str]:
        """Call done.run (with notify.send mocked). Return the last send args done.py emitted."""
        from fleet.commands import done as done_mod

        sent: list[dict] = []

        def _fake_send(sd, *, title, message, level="info"):  # noqa: ANN001
            sent.append({"title": title, "message": message, "level": level})

        with (
            unittest.mock.patch("fleet.orchestrator._launch_driver_for_stage"),
            unittest.mock.patch(
                "fleet.commands.done.task_context.resolve",
                return_value=(self.state_dir, task_id),
            ),
            unittest.mock.patch("fleet.commands.done.notify.send", side_effect=_fake_send),
        ):
            ret = done_mod.run(argparse.Namespace(task_id=task_id, result=result))

        # done.py's notify.send is called last (after the send inside the orchestrator)
        self._last_level = sent[-1]["level"] if sent else ""
        return ret, sent[-1]["title"] if sent else "", sent[-1]["message"] if sent else ""

    def test_notify_completed(self) -> None:
        """A single-stage task that finishes sends the 'completed' wording."""
        state.save_task(self.state_dir, "c1", {
            "id": "c1", "title": "x", "status": "running",
            "formation": "solo", "workspace": "none",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:sonnet", "status": "running"}],
        })
        task_dir = self.state_dir / "tasks" / "task-c1"
        task_dir.mkdir(parents=True, exist_ok=True)

        ret, title, message = self._run_done("c1")

        self.assertEqual(ret, 0)
        self.assertIn("completed", message)
        self.assertIn("all stages finished", message)
        self.assertIn("c1", message)
        self.assertEqual(self._last_level, "success")

    def test_notify_awaiting_orders(self) -> None:
        """When stopped at the user_approval gate, the 'awaiting_orders' wording is sent."""
        state.save_task(self.state_dir, "ua1", {
            "id": "ua1", "title": "x", "status": "running",
            "formation": "solo", "workspace": "none",
            "current_stage": 0,
            "stages": [
                {
                    "role": "driver", "agent": "claude:sonnet", "status": "running",
                    "user_approval": {"required": True, "status": "pending"},
                },
            ],
        })
        task_dir = self.state_dir / "tasks" / "task-ua1"
        task_dir.mkdir(parents=True, exist_ok=True)

        ret, title, message = self._run_done("ua1")

        self.assertEqual(ret, 0)
        task = state.load_task(self.state_dir, "ua1")
        self.assertEqual(task["status"], "awaiting_orders")
        self.assertIn("awaiting approval", message)
        self.assertIn("ua1", message)
        self.assertIn("driver", message)
        # stage 1/1 format
        self.assertIn("1/1", message)
        self.assertEqual(self._last_level, "waiting")

    def test_notify_next_stage_running(self) -> None:
        """A multi-stage advance names the genuinely-next stage's role in the handoff wording."""
        state.save_task(self.state_dir, "ms1", {
            "id": "ms1", "title": "x", "status": "running",
            "formation": "pair_review", "workspace": "none",
            "current_stage": 0,
            "stages": [
                {"role": "implementer", "agent": "claude:sonnet", "status": "running"},
                {"role": "reviewer", "agent": "claude:opus", "status": "pending"},
            ],
        })
        task_dir = self.state_dir / "tasks" / "task-ms1"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "driver-prompt.md").write_text("test prompt")
        (task_dir / "inbox.md").write_text("")
        (task_dir / "outbox.md").write_text("")

        ret, title, message = self._run_done("ms1")

        self.assertEqual(ret, 0)
        task = state.load_task(self.state_dir, "ms1")
        self.assertEqual(task["status"], "running")
        self.assertIn("ms1", message)
        self.assertIn("stage 1 handed off", message)
        self.assertIn("reviewer", message)
        self.assertEqual(self._last_level, "progress")

    def test_notify_peer_review_handoff_names_reviewer(self) -> None:
        """An implementer→reviewer peer_review handoff names the reviewer, not the implementer.

        Regression: the peer_review handoff keeps the same stage and only flips
        the phase to 'reviewing', so reading stages[current_idx].role surfaced
        the stage's own implementer role instead of the actual next driver
        (the reviewer), and 'next stage starting' was wrong (same stage).
        """
        state.save_task(self.state_dir, "pr1", {
            "id": "pr1", "title": "x", "status": "running",
            "formation": "pair_review", "workspace": "none",
            "current_stage": 0,
            "stages": [
                {
                    "role": "implementer", "agent": "claude:sonnet", "status": "running",
                    "peer_review": {"role": "code-reviewer"},
                },
            ],
        })
        task_dir = self.state_dir / "tasks" / "task-pr1"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "driver-prompt.md").write_text("test prompt")
        (task_dir / "inbox.md").write_text("")
        (task_dir / "outbox.md").write_text("")

        ret, title, message = self._run_done("pr1")

        self.assertEqual(ret, 0)
        task = state.load_task(self.state_dir, "pr1")
        # Same stage, only the phase flipped — no stage advance.
        self.assertEqual(task["current_stage"], 0)
        self.assertEqual(task["stages"][0]["peer_review"]["phase"], "reviewing")
        # The desktop message must name the actual next driver (the reviewer),
        # not the stage's own implementer role.
        self.assertIn("code-reviewer", message)
        self.assertNotIn("implementer", message)
        self.assertNotIn("starting", message)
        self.assertEqual(self._last_level, "progress")

    def test_notify_bare_ask_peer_review_handoff_not_awaiting_approval(self) -> None:
        """A bare driver ask must not make the next done look like an approval gate."""
        state.save_task(self.state_dir, "prask", {
            "id": "prask", "title": "x", "status": "awaiting_orders",
            "formation": "pair_review", "workspace": "none",
            "current_stage": 0,
            "stages": [
                {
                    "role": "implementer", "agent": "claude:sonnet", "status": "running",
                    "peer_review": {"role": "code-reviewer"},
                },
            ],
        })
        task_dir = self.state_dir / "tasks" / "task-prask"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "driver-prompt.md").write_text("test prompt")
        (task_dir / "inbox.md").write_text("")
        (task_dir / "outbox.md").write_text("")

        ret, title, message = self._run_done("prask")

        self.assertEqual(ret, 0)
        task = state.load_task(self.state_dir, "prask")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["stages"][0]["peer_review"]["phase"], "reviewing")
        self.assertIn("code-reviewer", message)
        self.assertNotIn("awaiting approval", title)
        self.assertNotIn("awaiting approval", message)
        self.assertEqual(self._last_level, "progress")


if __name__ == "__main__":
    unittest.main()
