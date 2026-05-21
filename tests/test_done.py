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
        "topology": "solo", "workflow": "bare",
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
            "status": "spawning", "topology": "pair_review", "workflow": "bare",
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


if __name__ == "__main__":
    unittest.main()
