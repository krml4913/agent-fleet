"""Tests for ``fleet status`` CLI smoke."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet"
sys.path.insert(0, str(ROOT / "src"))

from fleet import state  # noqa: E402


def run_fleet(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FLEET), *args],
        capture_output=True,
        text=True,
    )


class StatusCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_status_without_init(self) -> None:
        result = run_fleet("status", str(self.project))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no .fleet-state/", result.stderr)

    def test_status_after_init(self) -> None:
        state.init_state(self.project / ".fleet-state", name="demo")
        result = run_fleet("status", str(self.project))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project: demo", result.stdout)
        self.assertIn("tasks (0)", result.stdout)

    def test_status_lists_tasks(self) -> None:
        state.init_state(self.project / ".fleet-state", name="demo")
        state.save_task(
            self.project / ".fleet-state",
            "1",
            {"title": "do thing", "status": "pending", "agent": "claude:sonnet"},
        )
        result = run_fleet("status", str(self.project))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tasks (1)", result.stdout)
        self.assertIn("task-1", result.stdout)
        self.assertIn("do thing", result.stdout)


if __name__ == "__main__":
    unittest.main()
