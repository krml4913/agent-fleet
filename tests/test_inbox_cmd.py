"""Tests for ``fleet-agent inbox`` (delivery + event)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet-agent"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from fleet.commands import inbox as inbox_mod  # noqa: E402


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

    def test_event_contains_inbox_ts(self) -> None:
        run_fleet("inbox", "1", "msg", "--project", str(self.project))
        events_path = self.project / ".fleet-state" / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        msg_events = [e for e in events if e["type"] == "inbox_message"]
        self.assertTrue(msg_events)
        self.assertIn("inbox_ts", msg_events[0])

    def test_unknown_task(self) -> None:
        r = run_fleet("inbox", "999", "msg", "--project", str(self.project))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no task dir", r.stderr)


class InboxDeliveryTests(unittest.TestCase):
    """Unit tests for _wake_driver_pane — tmux delivery without a real session."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name) / ".fleet-state"
        state.init_state(self.state_dir, name="testproj")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sends_keys_when_tmux_available(self) -> None:
        with (
            patch("fleet.commands.inbox.tmux_mod.available", return_value=True),
            patch("fleet.commands.inbox.tmux_mod.send_keys") as mock_send,
        ):
            inbox_mod._wake_driver_pane(self.state_dir, "42")
            mock_send.assert_called_once()
            call_args = mock_send.call_args
            self.assertEqual(call_args[0][0], "fleet-testproj")
            self.assertEqual(call_args[0][1], "task-42")
            self.assertIn("inbox-read", call_args[0][2])

    def test_skips_when_tmux_unavailable(self) -> None:
        with (
            patch("fleet.commands.inbox.tmux_mod.available", return_value=False),
            patch("fleet.commands.inbox.tmux_mod.send_keys") as mock_send,
        ):
            inbox_mod._wake_driver_pane(self.state_dir, "42")
            mock_send.assert_not_called()

    def test_warns_but_succeeds_when_pane_missing(self) -> None:
        from fleet.tmux import TmuxError

        with (
            patch("fleet.commands.inbox.tmux_mod.available", return_value=True),
            patch(
                "fleet.commands.inbox.tmux_mod.send_keys",
                side_effect=TmuxError("no pane"),
            ),
        ):
            import io
            import contextlib

            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                inbox_mod._wake_driver_pane(self.state_dir, "42")
            self.assertIn("warn", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
