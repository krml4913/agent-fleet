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
        self.assertIn("demo  ·  worktree  ·  v0.0.1  ·  since ", result.stdout)
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
        self.assertIn(
            "● task-1  pending  -  stage 1/1 (driver, claude:sonnet)  —  do thing",
            result.stdout,
        )

    def test_status_task_without_stages_falls_back_to_dash_stage(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {"title": "legacy thing", "status": "pending"})
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("● task-1  pending  -  -  —  legacy thing", result.stdout)

    def test_status_shows_formation_and_stage_progress(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "multi thing",
            "status": "running",
            "formation": "multi_stage",
            "workspace": "worktree",
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
        self.assertIn(
            "● task-1  running  multi_stage  stage 2/3 (driver, codex:gpt-5.5)  —  multi thing",
            result.stdout,
        )

    def test_status_shows_peer_review_progress(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "review thing",
            "status": "running",
            "formation": "pair_review",
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
        self.assertIn(
            "● task-1  running  pair_review  stage 1/1 (driver, codex:gpt-5.5)  review 1/3",
            result.stdout,
        )

    def test_status_shows_solo_stage_cell(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "solo thing",
            "status": "running",
            "formation": "solo",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:opus", "status": "running"}],
        })
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "● task-1  running  solo  stage 1/1 (driver, claude:opus)  —  solo thing",
            result.stdout,
        )

    def test_status_highlights_awaiting_orders_row(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "needs you",
            "status": "awaiting_orders",
            "formation": "solo",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:opus", "status": "running"}],
        })
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        # awaiting_orders rows get a ▸ marker so they stand out in the list.
        self.assertIn("▸ ● task-1  awaiting_orders", result.stdout)

    def test_status_verbose_expands_stages_and_acks(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "multi thing",
            "status": "running",
            "formation": "multi_stage",
            "current_stage": 1,
            "stages": [
                {"role": "designer", "agent": "claude:opus", "status": "done"},
                {"role": "implementer", "agent": "codex:gpt-5.5", "status": "running"},
                {"role": "code-reviewer", "agent": "claude:opus", "status": "pending"},
            ],
        })
        ev_path = sd / "events.jsonl"
        with open(ev_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-05-20T14:03:00Z", "type": "inbox_seen", "task_id": "1", "watermark": "2026-05-20T14:00:00Z"}) + "\n")
            f.write(json.dumps({"ts": "2026-05-20T14:05:00Z", "type": "heartbeat", "task_id": "1"}) + "\n")
        result = run_fleet("status", "demo", "--verbose",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("      stage 1/3  done     designer       claude:opus", result.stdout)
        self.assertIn("      stage 2/3  current  implementer    codex:gpt-5.5", result.stdout)
        self.assertIn("      stage 3/3  pending  code-reviewer  claude:opus", result.stdout)
        self.assertIn("      acks: inbox_seen 14:03  ·  heartbeat 14:05", result.stdout)

    def test_status_verbose_acks_dash_when_none(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "solo thing",
            "status": "running",
            "formation": "solo",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:opus", "status": "running"}],
        })
        result = run_fleet("status", "demo", "-v",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("      acks: inbox_seen —  ·  heartbeat —", result.stdout)

    def test_status_shows_awaiting_orders_section(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {"title": "answer question", "status": "awaiting_orders", "agent": "codex"})
        result = run_fleet("status", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("⚠ awaiting orders  1", result.stdout)
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


class StatusJsonTests(unittest.TestCase):
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

    def test_json_emits_full_shape(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "feat", {
            "title": "build the thing",
            "status": "running",
            "formation": "multi_stage",
            "workspace": "worktree",
            "branch": "fleet/task/feat",
            "worktree": "/tmp/wt/feat",
            "current_stage": 1,
            "stages": [
                {"role": "plan", "agent": "claude:opus", "status": "done"},
                {"role": "driver", "agent": "codex:gpt-5.5", "status": "running"},
                {"role": "review", "agent": "claude:sonnet", "status": "pending"},
            ],
        })
        # PR URL lives in outbox.md; last_event is the most recent for this task.
        (state.task_dir(sd, "feat") / "outbox.md").write_text(
            "done\nPR: https://github.com/acme/repo/pull/42\n", encoding="utf-8"
        )
        ev_path = sd / "events.jsonl"
        with open(ev_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-05-20T14:00:00Z", "type": "start", "task_id": "feat"}) + "\n")
            f.write(json.dumps({"ts": "2026-05-20T14:05:00Z", "type": "heartbeat", "task_id": "other"}) + "\n")
            f.write(json.dumps({"ts": "2026-05-20T14:10:00Z", "type": "heartbeat", "task_id": "feat"}) + "\n")

        result = run_fleet("status", "feat", "--json", "--project", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        obj = json.loads(result.stdout)  # must parse — nothing else on stdout
        self.assertEqual(obj["task_id"], "feat")
        self.assertEqual(obj["title"], "build the thing")
        self.assertEqual(obj["status"], "running")
        self.assertEqual(obj["formation"], "multi_stage")
        self.assertEqual(obj["current_stage"], 1)
        self.assertEqual(len(obj["stages"]), 3)
        self.assertEqual(
            obj["stages"][1],
            {"role": "driver", "agent": "codex:gpt-5.5", "status": "running"},
        )
        self.assertEqual(obj["pr_url"], "https://github.com/acme/repo/pull/42")
        self.assertEqual(obj["branch"], "fleet/task/feat")
        self.assertEqual(obj["worktree"], "/tmp/wt/feat")
        self.assertEqual(obj["workspace"], "worktree")
        self.assertEqual(obj["last_event"], {"type": "heartbeat", "ts": "2026-05-20T14:10:00Z"})
        self.assertIsNone(obj["result"])

    def test_json_nulls_when_absent(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "bare", "status": "spawning", "formation": "solo",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:opus", "status": "running"}],
        })
        result = run_fleet("status", "1", "--json", "--project", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        obj = json.loads(result.stdout)
        self.assertIsNone(obj["pr_url"])
        self.assertIsNone(obj["branch"])
        self.assertIsNone(obj["worktree"])
        self.assertIsNone(obj["last_event"])
        self.assertIsNone(obj["result"])

    def test_json_reports_gate_result(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "rework", "status": "running", "formation": "pair_review",
            "current_stage": 0,
            "stages": [{
                "role": "driver", "agent": "codex:gpt-5.5", "status": "running",
                "result": "changes-requested",
            }],
        })
        result = run_fleet("status", "1", "--json", "--project", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        obj = json.loads(result.stdout)
        self.assertEqual(obj["result"], "changes-requested")

    def test_json_resolves_project_from_cwd(self) -> None:
        sd = make_project(self.fleet_home, "demo", self.project)
        state.save_task(sd, "1", {
            "title": "t", "status": "running", "formation": "solo",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:opus", "status": "running"}],
        })
        # No --project: resolve from cwd inside the registered repo.
        result = run_fleet("status", "1", "--json",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        obj = json.loads(result.stdout)
        self.assertEqual(obj["task_id"], "1")

    def test_json_nonexistent_task_errors(self) -> None:
        make_project(self.fleet_home, "demo", self.project)
        result = run_fleet("status", "ghost", "--json", "--project", "demo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")  # no half-JSON on stdout
        self.assertIn("no task-ghost", result.stderr)

    def test_json_unknown_project_errors(self) -> None:
        make_project(self.fleet_home, "demo", self.project)
        result = run_fleet("status", "1", "--json", "--project", "nope",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("no registered project", result.stderr)


class StatusProjectResolutionTests(unittest.TestCase):
    """Tests for --project / positional alias / leader-pane error in table mode."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        base = Path(self._tmp.name)
        self.fleet_home = base / "fleet-state"
        self.fleet_home.mkdir()
        self.project = base / "proj"
        self.project.mkdir()
        self._old = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "bmweb", self.project)
        self.session_dir = state.session_dir("main")
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old
        self._tmp.cleanup()

    def test_flag_project_works_in_table_mode(self) -> None:
        result = run_fleet(
            "status", "--project", "bmweb",
            fleet_home=self.fleet_home, cwd=self.project,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bmweb", result.stdout)

    def test_positional_project_still_works(self) -> None:
        result = run_fleet(
            "status", "bmweb",
            fleet_home=self.fleet_home, cwd=self.project,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bmweb", result.stdout)

    def test_flag_and_positional_same_value_ok(self) -> None:
        result = run_fleet(
            "status", "bmweb", "--project", "bmweb",
            fleet_home=self.fleet_home, cwd=self.project,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bmweb", result.stdout)

    def test_flag_and_positional_conflict_errors(self) -> None:
        make_project(self.fleet_home, "other", Path(self._tmp.name) / "other")
        result = run_fleet(
            "status", "bmweb", "--project", "other",
            fleet_home=self.fleet_home, cwd=self.project,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("differ", result.stderr)

    def test_leader_pane_no_project_errors(self) -> None:
        result = run_fleet(
            "status",
            fleet_home=self.fleet_home,
            cwd=self.project,
            env_extra={"FLEET_STATE_DIR": str(self.session_dir)},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("--project", result.stderr)
        self.assertIn("--all", result.stderr)

    def test_leader_pane_with_project_flag_works(self) -> None:
        result = run_fleet(
            "status", "--project", "bmweb",
            fleet_home=self.fleet_home,
            cwd=self.project,
            env_extra={"FLEET_STATE_DIR": str(self.session_dir)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bmweb", result.stdout)


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
                "workspace": "none",
            })
            ev_path = sd / "events.jsonl"
            with open(ev_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": "2026-05-20T10:00:00Z", "type": "inbox_message", "task_id": "1"}) + "\n")
            result = run_fleet("status", "demo", fleet_home=fleet_home, cwd=project)
            self.assertIn("[unread inbox]", result.stdout)
        finally:
            tmp.cleanup()


class ScopeFilterTests(unittest.TestCase):
    """Tests for ``fleet status --all`` scope filtering (Issue #172)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.proj_alpha = Path(self._tmp.name) / "alpha"
        self.proj_alpha.mkdir()
        self.proj_beta = Path(self._tmp.name) / "beta"
        self.proj_beta.mkdir()
        make_project(self.fleet_home, "alpha", self.proj_alpha)
        make_project(self.fleet_home, "beta", self.proj_beta)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_all(self, *extra_args: str, env_extra: dict | None = None):
        return run_fleet("status", "--all", *extra_args, fleet_home=self.fleet_home,
                         env_extra=env_extra)

    def test_no_scope_shows_all_projects(self) -> None:
        result = self._run_all(env_extra={"FLEET_SESSION": "main"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alpha", result.stdout)
        self.assertIn("beta", result.stdout)

    def test_scope_filters_to_session_scope(self) -> None:
        from fleet import state
        old = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        try:
            state.set_session_scope("main", ["alpha"], mode="set")
        finally:
            if old is None:
                os.environ.pop("FLEET_HOME", None)
            else:
                os.environ["FLEET_HOME"] = old
        result = self._run_all(env_extra={"FLEET_SESSION": "main"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alpha", result.stdout)
        self.assertNotIn("beta", result.stdout)

    def test_unscoped_flag_shows_all_despite_scope(self) -> None:
        from fleet import state
        old = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        try:
            state.set_session_scope("main", ["alpha"], mode="set")
        finally:
            if old is None:
                os.environ.pop("FLEET_HOME", None)
            else:
                os.environ["FLEET_HOME"] = old
        result = self._run_all("--unscoped", env_extra={"FLEET_SESSION": "main"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alpha", result.stdout)
        self.assertIn("beta", result.stdout)

    def test_no_fleet_session_env_shows_all(self) -> None:
        result = self._run_all()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alpha", result.stdout)
        self.assertIn("beta", result.stdout)

    def test_session_flag_overrides_env(self) -> None:
        from fleet import state
        old = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        try:
            state.set_session_scope("other", ["beta"], mode="set")
        finally:
            if old is None:
                os.environ.pop("FLEET_HOME", None)
            else:
                os.environ["FLEET_HOME"] = old
        result = self._run_all("--session", "other", env_extra={"FLEET_SESSION": "main"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("alpha", result.stdout)
        self.assertIn("beta", result.stdout)


if __name__ == "__main__":
    unittest.main()
