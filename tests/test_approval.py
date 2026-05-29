"""Tests for ``fleet-agent approve`` and ``fleet-agent reject``."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet-agent"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402


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
        env = os.environ.copy()
        env.pop("FLEET_TASK_ID", None)
        env["FLEET_STATE_DIR"] = str(self.state_dir)
        return subprocess.run(
            [sys.executable, str(FLEET), *args],
            capture_output=True,
            text=True,
            cwd=str(self.project),
            env=env,
        )

    def test_approve_completes_waiting_user_approval_gate(self) -> None:
        r = self._run("approve", "1")
        self.assertEqual(r.returncode, 0, r.stderr)

        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["stages"][0]["status"], "done")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "approved")

        events = [
            json.loads(line)
            for line in (self.state_dir / "events.jsonl").read_text().splitlines()
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
            for line in (self.state_dir / "events.jsonl").read_text().splitlines()
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
