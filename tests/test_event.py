"""Tests for ``fleet event emit``."""
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


class EventEmitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo", repo=self.project)
        state.save_task(self.state_dir, "1", {"id": "1", "title": "x", "status": "spawning"})

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

    def test_emit_basic(self) -> None:
        r = self._run("event", "emit", "milestone", "--task-id", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        events = self._events()
        msl = [e for e in events if e["type"] == "milestone"]
        self.assertEqual(len(msl), 1)
        self.assertEqual(msl[0]["task_id"], "1")

    def test_emit_with_fields(self) -> None:
        r = self._run(
            "event", "emit", "progress", "--task-id", "1",
            "--field", "done=3", "--field", "total=7", "--field", "note=halfway",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        events = self._events()
        prog = [e for e in events if e["type"] == "progress"]
        self.assertEqual(len(prog), 1)
        self.assertEqual(prog[0]["done"], "3")
        self.assertEqual(prog[0]["total"], "7")
        self.assertEqual(prog[0]["note"], "halfway")

    def test_emit_bad_field(self) -> None:
        r = self._run("event", "emit", "x", "--task-id", "1", "--field", "no-equals-sign")
        self.assertEqual(r.returncode, 1)
        self.assertIn("expects K=V", r.stderr)

    def _events(self) -> list[dict]:
        events_path = self.state_dir / "events.jsonl"
        return [json.loads(l) for l in events_path.read_text().splitlines() if l]


if __name__ == "__main__":
    unittest.main()
