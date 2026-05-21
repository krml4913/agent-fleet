"""Tests for ``fleet status`` CLI smoke."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from fleet.commands.status import _unread_tasks  # noqa: E402
from tests._fleet_test_helpers import run_fleet, make_project  # noqa: E402


class StatusCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_status_without_init(self) -> None:
        # cwd has no registered project → error
        result = run_fleet("status", "nonexistent",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no registered project", result.stderr)

    def test_status_after_init(self) -> None:
        make_project(self.fleet_home, "demo", self.project)
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("demo  ·  bare  ·  v0.0.1  ·  since ", result.stdout)
        self.assertIn("TASKS  0", result.stdout)
        self.assertIn("EVENTS  last 5 / 0", result.stdout)
        self.assertIn("  (none)", result.stdout)

    def test_status_lists_tasks(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "do thing", "status": "pending",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:sonnet", "status": "running"}],
        })
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TASKS  1", result.stdout)
        self.assertIn("● task-1  pending  seen —", result.stdout)
        self.assertIn(
            "      do thing  (topology -  agent claude:sonnet  workflow -)",
            result.stdout,
        )

    def test_status_task_without_stages_falls_back_to_dash_agent(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {"title": "legacy thing", "status": "pending"})
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "      legacy thing  (topology -  agent -  workflow -)",
            result.stdout,
        )

    def test_status_shows_topology_and_stage_progress(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "multi thing",
            "status": "running",
            "topology": "multi_stage",
            "workflow": "git_worktree",
            "current_stage": 1,
            "stages": [
                {"role": "plan", "agent": "codex:gpt-5.5", "status": "done"},
                {"role": "driver", "agent": "codex:gpt-5.5", "status": "running"},
                {"role": "review", "agent": "claude:sonnet", "status": "pending"},
            ],
        })
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("● task-1  running  stage 2/3  seen —", result.stdout)
        self.assertIn(
            "      multi thing  (topology multi_stage  agent codex:gpt-5.5  workflow git_worktree)",
            result.stdout,
        )

    def test_status_shows_peer_review_progress(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "review thing",
            "status": "running",
            "topology": "pair_review",
            "current_stage": 0,
            "stages": [{
                "role": "driver",
                "agent": "codex:gpt-5.5",
                "status": "running",
                "peer_review": {
                    "role": "code-reviewer",
                    "phase": "reviewing",
                    "iteration": 1,
                },
            }],
        })
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("● task-1  running  review ×1  seen —", result.stdout)

    def test_status_hides_solo_progress_token(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "solo thing",
            "status": "running",
            "topology": "solo",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": "codex:gpt-5.5", "status": "running"}],
        })
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("● task-1  running  seen —", result.stdout)
        self.assertNotIn("stage 1/1", result.stdout)
        self.assertNotIn("review ×", result.stdout)

    def test_status_shows_needs_input_section(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {"title": "answer question", "status": "needs_input", "agent": "codex"})
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("⚠ needs your input  1", result.stdout)
        self.assertIn("  task-1  answer question", result.stdout)

    def test_status_formats_recent_events(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        ev_path = sd / "events.jsonl"
        with open(ev_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-05-20T15:09:00Z", "type": "cleanup", "task_id": "one"}) + "\n")
            f.write(json.dumps({"ts": "2026-05-20T15:43:00Z", "type": "start", "task_id": "two"}) + "\n")
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EVENTS  last 5 / 2", result.stdout)
        self.assertIn("  15:09  cleanup  task-one", result.stdout)
        self.assertIn("  15:43  start    task-two", result.stdout)

    def test_status_all(self) -> None:
        proj2 = Path(self._tmp.name) / "proj2"
        proj2.mkdir()
        make_project(self.fleet_home, "alpha", self.project)
        make_project(self.fleet_home, "beta", proj2)
        result = run_fleet("status", "--all", fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alpha", result.stdout)
        self.assertIn("beta", result.stdout)

    def test_status_all_orphan_warning(self) -> None:
        missing = Path(self._tmp.name) / "gone"
        missing.mkdir()
        make_project(self.fleet_home, "orphan", missing)
        import shutil
        shutil.rmtree(missing)
        result = run_fleet("status", "--all", fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("repo missing", result.stdout)
        self.assertIn("fleet rm orphan", result.stdout)


class UnreadTasksTests(unittest.TestCase):
    def test_unread_when_no_ack(self) -> None:
        events = [{"ts": "2026-05-20T10:00:00Z", "type": "inbox_message", "task_id": "1"}]
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
            fleet_home = Path(tmp.name) / "fleet-state"
            fleet_home.mkdir()
            project = Path(tmp.name) / "proj"
            project.mkdir()
            sd = make_project(fleet_home, "demo", project)
            state.save_task(sd, "1", {
                "id": "1", "title": "t", "status": "in_progress",
                "current_stage": 0,
                "stages": [{"role": "driver", "agent": "x", "status": "running"}],
                "workflow": "bare",
            })
            ev_path = sd / "events.jsonl"
            with open(ev_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": "2026-05-20T10:00:00Z", "type": "inbox_message", "task_id": "1"}) + "\n")
            result = run_fleet("status", "demo", fleet_home=fleet_home, cwd=project)
            self.assertIn("[unread inbox]", result.stdout)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
