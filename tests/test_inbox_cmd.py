"""Tests for ``fleet inbox``."""
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


class InboxCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        state.init_state(self.project / ".fleet-state", name="demo")
        state.save_task(
            self.project / ".fleet-state",
            "1",
            {"id": "1", "title": "x", "status": "spawning"},
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_appends_message(self) -> None:
        r = run_fleet(
            "inbox", "1", "Hello", "from", "leader",
            "--project", str(self.project),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        inbox = self.project / ".fleet-state" / "tasks" / "task-1" / "inbox.md"
        self.assertIn("Hello from leader", inbox.read_text())

    def test_multiple_messages_accumulate(self) -> None:
        run_fleet("inbox", "1", "first", "--project", str(self.project))
        run_fleet("inbox", "1", "second", "--project", str(self.project))
        text = (
            self.project / ".fleet-state" / "tasks" / "task-1" / "inbox.md"
        ).read_text()
        self.assertIn("first", text)
        self.assertIn("second", text)

    def test_emits_event(self) -> None:
        run_fleet("inbox", "1", "msg", "--project", str(self.project))
        events_path = self.project / ".fleet-state" / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        self.assertTrue(any(e["type"] == "inbox_message" for e in events))

    def test_unknown_task(self) -> None:
        r = run_fleet("inbox", "999", "msg", "--project", str(self.project))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no task dir", r.stderr)


if __name__ == "__main__":
    unittest.main()
