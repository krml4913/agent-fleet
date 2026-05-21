"""Tests for ``fleet cleanup``."""
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


class CleanupCmdTests(unittest.TestCase):
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

    def _save(self, task_id: str, status: str) -> None:
        state.save_task(self.state_dir, task_id, {
            "id": task_id, "title": f"t{task_id}", "status": status,
            "agent": "claude:sonnet", "workflow": "bare",
        })

    def test_refuses_non_terminal_without_force(self) -> None:
        self._save("1", "spawning")
        r = self._run("cleanup", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("refusing", r.stderr)

    def test_completed_cleans_up(self) -> None:
        self._save("1", "completed")
        r = self._run("cleanup", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        events_path = self.state_dir / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        self.assertTrue(any(e["type"] == "cleanup" for e in events))

    def test_force_overrides(self) -> None:
        self._save("2", "spawning")
        r = self._run("cleanup", "2", "--force")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_archive_moves_dir(self) -> None:
        self._save("3", "completed")
        r = self._run("cleanup", "3", "--archive")
        self.assertEqual(r.returncode, 0, r.stderr)
        sd = self.state_dir
        self.assertFalse((sd / "tasks" / "task-3").exists())
        self.assertTrue((sd / "tasks" / "_archive" / "task-3").is_dir())
        self.assertEqual(state.list_tasks(sd), [])

    def test_unknown_task(self) -> None:
        r = self._run("cleanup", "999")
        self.assertEqual(r.returncode, 1)
        self.assertIn("task.yaml missing", r.stderr)


if __name__ == "__main__":
    unittest.main()
