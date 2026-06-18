"""Tests for ``fleet sessions`` (Issue #166 Phase 5, read-only view)."""
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
from tests._fleet_test_helpers import make_project, run_fleet  # noqa: E402


class SessionsCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        base = Path(self._tmp.name)
        self.fleet_home = base / "fleet-state"
        self.fleet_home.mkdir()
        self.repo = base / "repo"
        self.repo.mkdir()
        self._old = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.repo)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old
        self._tmp.cleanup()

    def _write_session(
        self, label: str, agent: str = "claude:opus", scope: list[str] | None = None
    ) -> None:
        rec = state.session_record_path(label)
        rec.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {
            "label": label,
            "agent": agent,
            "started_at": "2026-06-18T09:00:00+00:00",
            "pane": f"fleet-{label}:leader",
        }
        if scope is not None:
            data["scope"] = scope
        rec.write_text(json.dumps(data), encoding="utf-8")

    def _save_task(self, task_id: str, status: str, owner_session: str | None = None) -> None:
        data = {
            "id": task_id, "title": f"t{task_id}", "status": status,
            "agent": "claude:sonnet", "workspace": "none",
        }
        if owner_session is not None:
            data["owner_session"] = owner_session
        state.save_task(self.state_dir, task_id, data)

    def _run(self):
        return run_fleet(
            "sessions",
            fleet_home=self.fleet_home,
            cwd=self.repo,
            env_extra={"FLEET_NO_TMUX": "1", "NO_COLOR": "1"},
        )

    def test_empty(self) -> None:
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(no leader sessions)", r.stdout)
        self.assertIn("SESSIONS  0", r.stdout)

    def test_lists_session_with_inflight_tasks(self) -> None:
        self._write_session("main")
        self._save_task("1", "running", owner_session="main")
        self._save_task("2", "completed", owner_session="main")  # terminal: excluded
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("main", r.stdout)
        self.assertIn("claude:opus", r.stdout)
        self.assertIn("fleet-main:leader", r.stdout)
        self.assertIn("task-1", r.stdout)
        self.assertIn("running", r.stdout)
        self.assertNotIn("task-2", r.stdout)

    def test_missing_owner_session_defaults_to_main(self) -> None:
        self._write_session("main")
        self._save_task("3", "spawning")  # no owner_session → main
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("task-3", r.stdout)

    def test_task_label_without_session_record_still_listed(self) -> None:
        # A label claimed by an in-flight task but with no session.json still
        # surfaces (its work must not be hidden), flagged as recordless.
        self._save_task("4", "running", owner_session="hotfix")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hotfix", r.stdout)
        self.assertIn("task-4", r.stdout)
        self.assertIn("no session record", r.stdout)

    def test_session_without_tasks_shows_none(self) -> None:
        self._write_session("idle")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("idle", r.stdout)
        self.assertIn("(no in-flight tasks)", r.stdout)

    def test_scope_line_shown_when_set(self) -> None:
        self._write_session("main", scope=["demo"])
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("scope:", r.stdout)
        self.assertIn("demo", r.stdout)

    def test_scope_all_projects_when_unset(self) -> None:
        self._write_session("main")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("all projects", r.stdout)


if __name__ == "__main__":
    unittest.main()
