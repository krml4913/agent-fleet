"""Tests for ``fleet log``."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from fleet.events import append_event  # noqa: E402


def run_fleet(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("FLEET_TASK_ID", None)
    return subprocess.run(
        [sys.executable, str(FLEET), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
    )


class LogCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        state.init_state(self.project / ".fleet-state", name="demo")
        ev = self.project / ".fleet-state" / "events.jsonl"
        append_event(ev, "spawn", task_id="1", agent="claude:sonnet")
        append_event(ev, "milestone", task_id="1", note="alpha")
        append_event(ev, "spawn", task_id="2", agent="codex:o4-mini")
        append_event(ev, "done", task_id="1")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_default_shows_all(self) -> None:
        r = run_fleet("log", "--project", str(self.project))
        self.assertEqual(r.returncode, 0, r.stderr)
        for needle in ("spawn", "milestone", "done", "task-1", "task-2"):
            self.assertIn(needle, r.stdout)

    def test_filter_by_task(self) -> None:
        r = run_fleet("log", "1", "--project", str(self.project))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("task-1", r.stdout)
        self.assertNotIn("task-2", r.stdout)

    def test_filter_by_type(self) -> None:
        r = run_fleet("log", "--type", "spawn", "--project", str(self.project))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("spawn", r.stdout)
        self.assertNotIn("milestone", r.stdout)

    def test_lines_limit(self) -> None:
        r = run_fleet("log", "-n", "1", "--project", str(self.project))
        self.assertEqual(r.returncode, 0, r.stderr)
        # only the latest event should be visible
        self.assertEqual(r.stdout.count("\n"), 1)
        self.assertIn("done", r.stdout)

    def test_no_state_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            r = run_fleet("log", "--project", tmp)
            self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
