"""Tests for ``fleet done``."""
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


class DoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        state.init_state(self.project / ".fleet-state", name="demo")
        # Silence notifications.
        (self.project / ".fleet-state" / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n"
        )
        state.save_task(
            self.project / ".fleet-state",
            "1",
            {"id": "1", "title": "x", "status": "spawning"},
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("FLEET_TASK_ID", None)
        return subprocess.run(
            [sys.executable, str(FLEET), *args],
            capture_output=True, text=True,
            cwd=str(cwd) if cwd else str(self.project),
            env=env,
        )

    def test_done_with_explicit_id(self) -> None:
        r = self._run("done", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        task = state.load_task(self.project / ".fleet-state", "1")
        self.assertEqual(task["status"], "completed")
        events_path = self.project / ".fleet-state" / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        self.assertTrue(any(e["type"] == "done" for e in events))

    def test_done_from_cwd(self) -> None:
        task_dir = self.project / ".fleet-state" / "tasks" / "task-1"
        r = self._run("done", cwd=task_dir)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_done_unknown_task(self) -> None:
        r = self._run("done", "999")
        self.assertEqual(r.returncode, 1)
        self.assertIn("task.yaml missing", r.stderr)


if __name__ == "__main__":
    unittest.main()
