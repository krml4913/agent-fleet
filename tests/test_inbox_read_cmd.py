"""Tests for ``fleet-agent inbox-read`` command."""
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
from fleet.commands.inbox_read import extract_watermark  # noqa: E402


def run_fleet_agent(
    *args: str,
    env: dict | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    base_env = os.environ.copy()
    base_env.pop("FLEET_TASK_ID", None)
    base_env.pop("FLEET_STATE_DIR", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, str(FLEET), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=base_env,
    )


class ExtractWatermarkTests(unittest.TestCase):
    def test_returns_last_timestamp(self) -> None:
        text = "### 2026-05-20T10:00:00Z\n\nhello\n\n### 2026-05-20T11:00:00Z\n\nworld\n\n"
        self.assertEqual(extract_watermark(text), "2026-05-20T11:00:00Z")

    def test_single_message(self) -> None:
        text = "### 2026-05-20T09:30:00Z\n\nmsg\n\n"
        self.assertEqual(extract_watermark(text), "2026-05-20T09:30:00Z")

    def test_empty_inbox(self) -> None:
        self.assertIsNone(extract_watermark(""))

    def test_no_headers(self) -> None:
        self.assertIsNone(extract_watermark("just some text\nno headers\n"))


class InboxReadCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = self.project / ".fleet-state"
        state.init_state(self.state_dir, name="demo")
        state.save_task(
            self.state_dir,
            "1",
            {"id": "1", "title": "x", "status": "in_progress"},
        )
        self.task_dir = self.state_dir / "tasks" / "task-1"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_inbox(self, content: str) -> None:
        (self.task_dir / "inbox.md").write_text(content, encoding="utf-8")

    def _events(self) -> list[dict]:
        path = self.state_dir / "events.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines() if l]

    def test_prints_inbox_content(self) -> None:
        self._write_inbox("### 2026-05-20T10:00:00Z\n\nhello world\n\n")
        r = run_fleet_agent(
            "inbox-read",
            env={"FLEET_TASK_ID": "1"},
            cwd=self.project,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hello world", r.stdout)

    def test_emits_inbox_seen_event(self) -> None:
        self._write_inbox("### 2026-05-20T10:00:00Z\n\nhello\n\n")
        run_fleet_agent(
            "inbox-read",
            env={"FLEET_TASK_ID": "1"},
            cwd=self.project,
        )
        events = self._events()
        seen = [e for e in events if e["type"] == "inbox_seen"]
        self.assertTrue(seen, "inbox_seen event not emitted")
        self.assertEqual(seen[-1]["watermark"], "2026-05-20T10:00:00Z")
        self.assertEqual(seen[-1]["task_id"], "1")

    def test_watermark_is_last_timestamp(self) -> None:
        self._write_inbox(
            "### 2026-05-20T10:00:00Z\n\nfirst\n\n"
            "### 2026-05-20T11:00:00Z\n\nsecond\n\n"
        )
        run_fleet_agent(
            "inbox-read",
            env={"FLEET_TASK_ID": "1"},
            cwd=self.project,
        )
        events = self._events()
        seen = [e for e in events if e["type"] == "inbox_seen"]
        self.assertEqual(seen[-1]["watermark"], "2026-05-20T11:00:00Z")

    def test_empty_inbox_watermark_is_none(self) -> None:
        self._write_inbox("")
        r = run_fleet_agent(
            "inbox-read",
            env={"FLEET_TASK_ID": "1"},
            cwd=self.project,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        events = self._events()
        seen = [e for e in events if e["type"] == "inbox_seen"]
        self.assertIsNone(seen[-1]["watermark"])

    def test_missing_inbox_returns_error(self) -> None:
        (self.task_dir / "inbox.md").unlink(missing_ok=True)
        r = run_fleet_agent(
            "inbox-read",
            env={"FLEET_TASK_ID": "1"},
            cwd=self.project,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)

    def test_no_task_id_returns_error(self) -> None:
        # No FLEET_TASK_ID and no cwd that resolves a task
        r = run_fleet_agent(
            "inbox-read",
            cwd=Path(self._tmp.name),
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("error", r.stderr)


if __name__ == "__main__":
    unittest.main()
