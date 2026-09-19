"""Tests for ``fleet-agent approve`` and ``fleet-agent reject``."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from tests._fleet_test_helpers import run_fleet_agent  # noqa: E402


def _approval_task(task_id: str = "1") -> dict:
    return {
        "id": task_id,
        "title": "x",
        "status": "awaiting_orders",
        "formation": "solo",
        "workspace": "none",
        "current_stage": 0,
        "stages": [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "asked"},
            }
        ],
    }


class ApprovalCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo", repo=self.project)
        state.save_task(self.state_dir, "1", _approval_task("1"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {}
        env["FLEET_STATE_DIR"] = str(self.state_dir)
        # ``reject`` relaunches the stage driver, which would otherwise spawn a
        # real ``fleet-demo`` tmux session that no test ever tears down (#130).
        # This test only asserts state transitions, so keep tmux out of it.
        env["FLEET_NO_MUX"] = "1"
        return run_fleet_agent(*args, cwd=self.project, env_extra=env)

    def test_approve_completes_waiting_user_approval_gate(self) -> None:
        r = self._run("approve", "1")
        self.assertEqual(r.returncode, 0, r.stderr)

        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["stages"][0]["status"], "done")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "approved")

        events = [
            json.loads(line)
            for line in (self.state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertTrue(any(e["type"] == "approve" for e in events))

    def test_reject_returns_gate_to_implementation(self) -> None:
        r = self._run("reject", "1")
        self.assertEqual(r.returncode, 0, r.stderr)

        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["stages"][0]["status"], "running")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "pending")

        events = [
            json.loads(line)
            for line in (self.state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertTrue(any(e["type"] == "reject" for e in events))

    def _events(self) -> list[dict]:
        return [
            json.loads(line)
            for line in (self.state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]

    def _inbox(self) -> str:
        return (state.task_dir(self.state_dir, "1") / "inbox.md").read_text(encoding="utf-8")

    def test_reject_without_reason_still_writes_reject_block(self) -> None:
        r = self._run("reject", "1")
        self.assertEqual(r.returncode, 0, r.stderr)

        inbox = self._inbox()
        self.assertIn("[fleet reject]", inbox)
        self.assertIn("Do not re-submit unchanged work", inbox)
        self.assertIn("No reason was given", inbox)
        self.assertIn("ask", inbox)
        reject = [e for e in self._events() if e["type"] == "reject"][0]
        self.assertNotIn("reason", reject)

    def test_reject_reason_reaches_inbox_and_event(self) -> None:
        r = self._run("reject", "1", "--reason", "テストが足りない: edge case を追加")
        self.assertEqual(r.returncode, 0, r.stderr)

        inbox = self._inbox()
        self.assertIn("[fleet reject]", inbox)
        self.assertIn("テストが足りない: edge case を追加", inbox)
        self.assertNotIn("No reason was given", inbox)
        reject = [e for e in self._events() if e["type"] == "reject"][0]
        self.assertEqual(reject["reason"], "テストが足りない: edge case を追加")

    def test_reject_reason_file_is_read_as_utf8(self) -> None:
        reason_file = self.project / "reason.md"
        # utf-8-sig tolerates the BOM Windows PowerShell 5.1 adds on redirection.
        reason_file.write_bytes("﻿日本語の理由\n2 行目\n".encode("utf-8"))

        r = self._run("reject", "1", "--reason-file", str(reason_file))
        self.assertEqual(r.returncode, 0, r.stderr)

        inbox = self._inbox()
        self.assertIn("日本語の理由\n2 行目", inbox)
        self.assertNotIn("﻿", inbox)
        reject = [e for e in self._events() if e["type"] == "reject"][0]
        self.assertEqual(reject["reason"], "日本語の理由\n2 行目")

    def test_reject_reason_and_reason_file_are_mutually_exclusive(self) -> None:
        reason_file = self.project / "reason.md"
        reason_file.write_text("x", encoding="utf-8")

        r = self._run("reject", "1", "--reason", "a", "--reason-file", str(reason_file))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not allowed with", r.stderr)
        # Nothing was settled.
        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["status"], "awaiting_orders")

    def test_reject_missing_reason_file_errors_without_settling_gate(self) -> None:
        r = self._run("reject", "1", "--reason-file", str(self.project / "nope.md"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("--reason-file", r.stderr)
        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["status"], "awaiting_orders")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "asked")

    def test_reject_reason_file_not_utf8_errors(self) -> None:
        reason_file = self.project / "reason.md"
        reason_file.write_bytes("理由".encode("cp932"))

        r = self._run("reject", "1", "--reason-file", str(reason_file))
        self.assertEqual(r.returncode, 1)
        self.assertIn("--reason-file", r.stderr)

    def test_reject_blank_reason_is_treated_as_no_reason(self) -> None:
        r = self._run("reject", "1", "--reason", "   ")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No reason was given", self._inbox())

    def test_approve_rejects_non_waiting_gate(self) -> None:
        task = state.load_task(self.state_dir, "1")
        task["stages"][0]["user_approval"]["status"] = "pending"
        state.save_task(self.state_dir, "1", task)

        r = self._run("approve", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not waiting for user approval", r.stderr)


if __name__ == "__main__":
    unittest.main()
