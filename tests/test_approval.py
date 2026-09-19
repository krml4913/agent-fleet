"""Tests for ``fleet-agent approve`` and ``fleet-agent reject``."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from tests._fleet_test_helpers import run_fleet_agent  # noqa: E402


def _approval_task(task_id: str = "1") -> dict:
    return {
        "id": task_id,
        "title": "x",
        "status": "awaiting_orders",
        "formation": "solo",
        "workspace": "none",
        "current_stage": 0,
        "stages": [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "asked"},
            }
        ],
    }


class ApprovalCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo", repo=self.project)
        state.save_task(self.state_dir, "1", _approval_task("1"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {}
        env["FLEET_STATE_DIR"] = str(self.state_dir)
        # ``reject`` relaunches the stage driver, which would otherwise spawn a
        # real ``fleet-demo`` tmux session that no test ever tears down (#130).
        # This test only asserts state transitions, so keep tmux out of it.
        env["FLEET_NO_MUX"] = "1"
        return run_fleet_agent(*args, cwd=self.project, env_extra=env)

    def test_approve_completes_waiting_user_approval_gate(self) -> None:
        r = self._run("approve", "1")
        self.assertEqual(r.returncode, 0, r.stderr)

        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["stages"][0]["status"], "done")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "approved")

        events = [
            json.loads(line)
            for line in (self.state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertTrue(any(e["type"] == "approve" for e in events))

    def test_reject_returns_gate_to_implementation(self) -> None:
        r = self._run("reject", "1")
        self.assertEqual(r.returncode, 0, r.stderr)

        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["stages"][0]["status"], "running")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "pending")

        events = [
            json.loads(line)
            for line in (self.state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertTrue(any(e["type"] == "reject" for e in events))

    def test_approve_rejects_non_waiting_gate(self) -> None:
        task = state.load_task(self.state_dir, "1")
        task["stages"][0]["user_approval"]["status"] = "pending"
        state.save_task(self.state_dir, "1", task)

        r = self._run("approve", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not waiting for user approval", r.stderr)


if __name__ == "__main__":
    unittest.main()
