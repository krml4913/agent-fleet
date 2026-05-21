"""Tests for ``fleet-agent inbox`` (delivery + event)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet-agent"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from fleet.commands import inbox as inbox_mod  # noqa: E402
from tests._fleet_test_helpers import run_fleet_agent, make_project  # noqa: E402


class InboxCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)
        state.save_task(self.state_dir, "1", {"id": "1", "title": "x", "status": "spawning"})

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_fleet_agent(*args, fleet_home=self.fleet_home)

    def test_appends_message(self) -> None:
        r = self._run("inbox", "1", "Hello", "from", "leader", "--project", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        inbox = self.state_dir / "tasks" / "task-1" / "inbox.md"
        self.assertIn("Hello from leader", inbox.read_text())

    def test_multiple_messages_accumulate(self) -> None:
        self._run("inbox", "1", "first", "--project", "demo")
        self._run("inbox", "1", "second", "--project", "demo")
        text = (self.state_dir / "tasks" / "task-1" / "inbox.md").read_text()
        self.assertIn("first", text)
        self.assertIn("second", text)

    def test_emits_event(self) -> None:
        self._run("inbox", "1", "msg", "--project", "demo")
        events_path = self.state_dir / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        self.assertTrue(any(e["type"] == "inbox_message" for e in events))

    def test_event_contains_inbox_ts(self) -> None:
        self._run("inbox", "1", "msg", "--project", "demo")
        events_path = self.state_dir / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        msg_events = [e for e in events if e["type"] == "inbox_message"]
        self.assertTrue(msg_events)
        self.assertIn("inbox_ts", msg_events[0])

    def test_unknown_task(self) -> None:
        r = self._run("inbox", "999", "msg", "--project", "demo")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no task dir", r.stderr)


class InboxDeliveryTests(unittest.TestCase):
    """Unit tests for _wake_driver_pane — tmux delivery without a real session."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name) / "state"
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
        import io
        import contextlib

        with (
            patch("fleet.commands.inbox.tmux_mod.available", return_value=True),
            patch(
                "fleet.commands.inbox.tmux_mod.send_keys",
                side_effect=TmuxError("no pane"),
            ),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                inbox_mod._wake_driver_pane(self.state_dir, "42")
            self.assertIn("warn", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
