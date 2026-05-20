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
from fleet.commands.status import _unread_tasks  # noqa: E402


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


class UnreadTasksTests(unittest.TestCase):
    def test_unread_when_no_ack(self) -> None:
        events = [
            {"ts": "2026-05-20T10:00:00Z", "type": "inbox_message", "task_id": "1"},
        ]
        self.assertEqual(_unread_tasks(events), {"1"})

    def test_read_when_watermark_matches(self) -> None:
        events = [
            {"ts": "2026-05-20T10:00:00Z", "type": "inbox_message", "task_id": "1"},
            {"ts": "2026-05-20T10:01:00Z", "type": "inbox_seen", "task_id": "1", "watermark": "2026-05-20T10:00:00Z"},
        ]
        self.assertEqual(_unread_tasks(events), set())

    def test_unread_when_new_message_after_ack(self) -> None:
        events = [
            {"ts": "2026-05-20T10:00:00Z", "type": "inbox_message", "task_id": "1"},
            {"ts": "2026-05-20T10:01:00Z", "type": "inbox_seen", "task_id": "1", "watermark": "2026-05-20T10:00:00Z"},
            {"ts": "2026-05-20T11:00:00Z", "type": "inbox_message", "task_id": "1"},
        ]
        self.assertEqual(_unread_tasks(events), {"1"})

    def test_no_messages_no_unread(self) -> None:
        self.assertEqual(_unread_tasks([]), set())

    def test_multiple_tasks_independent(self) -> None:
        events = [
            {"ts": "2026-05-20T10:00:00Z", "type": "inbox_message", "task_id": "1"},
            {"ts": "2026-05-20T10:01:00Z", "type": "inbox_seen", "task_id": "1", "watermark": "2026-05-20T10:00:00Z"},
            {"ts": "2026-05-20T10:00:00Z", "type": "inbox_message", "task_id": "2"},
        ]
        self.assertEqual(_unread_tasks(events), {"2"})

    def test_status_output_shows_unread_flag(self) -> None:
        tmp = TemporaryDirectory()
        try:
            project = Path(tmp.name) / "proj"
            project.mkdir()
            sd = project / ".fleet-state"
            state.init_state(sd, name="demo")
            state.save_task(sd, "1", {"id": "1", "title": "t", "status": "in_progress", "agent": "x", "workflow": "bare"})
            # Write an inbox_message event manually
            ev_path = sd / "events.jsonl"
            import json as _json
            with open(ev_path, "a") as f:
                f.write(_json.dumps({"ts": "2026-05-20T10:00:00Z", "type": "inbox_message", "task_id": "1"}) + "\n")
            result = subprocess.run(
                [sys.executable, str(FLEET), "status", str(project)],
                capture_output=True, text=True,
            )
            self.assertIn("[unread inbox]", result.stdout)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
