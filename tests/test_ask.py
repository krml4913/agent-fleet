"""Tests for ``fleet ask``."""
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
from tests._fleet_test_helpers import run_fleet_agent, make_project  # noqa: E402


class AskTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)
        (self.state_dir / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n"
        )
        # Spawn one task to ask about.
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run", "1", "Implement foo",
            fleet_home=self.fleet_home,
        )
        assert result.returncode == 0, result.stderr

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_records_awaiting_orders(self) -> None:
        env = os.environ.copy()
        env["FLEET_STATE_DIR"] = str(self.state_dir)
        result = subprocess.run(
            [sys.executable, str(FLEET), "ask", "--task-id", "1",
             "Should I use approach A or B?"],
            capture_output=True, text=True,
            cwd=str(self.project),
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["status"], "awaiting_orders")
        questions = (self.state_dir / "tasks" / "task-1" / "questions.md").read_text()
        self.assertIn("approach A or B", questions)
        events = [
            json.loads(l) for l in (self.state_dir / "events.jsonl").read_text().splitlines() if l
        ]
        awaiting_orders_events = [e for e in events if e["type"] == "awaiting_orders"]
        self.assertEqual(len(awaiting_orders_events), 1)
        self.assertEqual(awaiting_orders_events[0]["task_id"], "1")

    def test_resolves_from_cwd(self) -> None:
        task_cwd = self.state_dir / "tasks" / "task-1"
        env = os.environ.copy()
        env.pop("FLEET_TASK_ID", None)
        env["FLEET_STATE_DIR"] = str(self.state_dir)
        result = subprocess.run(
            [sys.executable, str(FLEET), "ask", "from cwd"],
            capture_output=True, text=True,
            cwd=str(task_cwd),
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_task(self) -> None:
        env = os.environ.copy()
        env["FLEET_STATE_DIR"] = str(self.state_dir)
        result = subprocess.run(
            [sys.executable, str(FLEET), "ask", "--task-id", "999", "?"],
            capture_output=True, text=True,
            cwd=str(self.project),
            env=env,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("task.yaml missing", result.stderr)


if __name__ == "__main__":
    unittest.main()
