"""Tests for ``fleet log``."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from fleet.events import append_event  # noqa: E402
from tests._fleet_test_helpers import run_fleet, make_project  # noqa: E402


class LogCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)
        ev = self.state_dir / "events.jsonl"
        append_event(ev, "spawn", task_id="1", agent="claude:sonnet")
        append_event(ev, "milestone", task_id="1", note="alpha")
        append_event(ev, "spawn", task_id="2", agent="codex:o4-mini")
        append_event(ev, "done", task_id="1")

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_default_shows_all(self) -> None:
        r = run_fleet("log", "--project", "demo", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        for needle in ("spawn", "milestone", "done", "task-1", "task-2"):
            self.assertIn(needle, r.stdout)

    def test_filter_by_task(self) -> None:
        r = run_fleet("log", "1", "--project", "demo", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("task-1", r.stdout)
        self.assertNotIn("task-2", r.stdout)

    def test_filter_by_type(self) -> None:
        r = run_fleet("log", "--type", "spawn", "--project", "demo",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("spawn", r.stdout)
        self.assertNotIn("milestone", r.stdout)

    def test_lines_limit(self) -> None:
        r = run_fleet("log", "-n", "1", "--project", "demo", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("\n"), 1)
        self.assertIn("done", r.stdout)

    def test_no_project_found(self) -> None:
        r = run_fleet("log", "--project", "nonexistent", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
