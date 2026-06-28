"""Tests for fleet.status_data — presentation-agnostic derivation helpers."""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

import yaml  # noqa: E402

from fleet import state  # noqa: E402
from fleet import status_data  # noqa: E402
from tests._fleet_test_helpers import make_project  # noqa: E402


class StatusSeverityTests(unittest.TestCase):
    def test_ok_statuses(self) -> None:
        for s in ("done", "approved", "completed"):
            self.assertEqual(status_data.status_severity(s), "ok", s)

    def test_active_statuses(self) -> None:
        for s in ("running", "spawning"):
            self.assertEqual(status_data.status_severity(s), "active", s)

    def test_attention_statuses(self) -> None:
        for s in ("awaiting_orders", "failed", "changes-requested"):
            self.assertEqual(status_data.status_severity(s), "attention", s)

    def test_neutral_fallback(self) -> None:
        self.assertEqual(status_data.status_severity("pending"), "neutral")
        self.assertEqual(status_data.status_severity("unknown"), "neutral")
        self.assertEqual(status_data.status_severity(""), "neutral")


class CurrentStageIndexTests(unittest.TestCase):
    def test_no_stages(self) -> None:
        self.assertEqual(status_data.current_stage_index({}), -1)
        self.assertEqual(status_data.current_stage_index({"stages": []}), -1)

    def test_explicit_current_stage(self) -> None:
        task = {
            "stages": [{"role": "a"}, {"role": "b"}],
            "current_stage": 1,
        }
        self.assertEqual(status_data.current_stage_index(task), 1)

    def test_out_of_range_current_stage(self) -> None:
        task = {"stages": [{"role": "a"}], "current_stage": 5}
        self.assertEqual(status_data.current_stage_index(task), -1)

    def test_default_stage_zero(self) -> None:
        task = {"stages": [{"role": "a"}]}
        self.assertEqual(status_data.current_stage_index(task), 0)


class GateResultTests(unittest.TestCase):
    def test_no_stages(self) -> None:
        self.assertIsNone(status_data.gate_result({}))

    def test_explicit_result(self) -> None:
        task = {
            "current_stage": 0,
            "stages": [{"result": "changes-requested"}],
        }
        self.assertEqual(status_data.gate_result(task), "changes-requested")

    def test_user_approval(self) -> None:
        task = {
            "current_stage": 0,
            "stages": [{"user_approval": {"status": "approved"}}],
        }
        self.assertEqual(status_data.gate_result(task), "approved")

    def test_no_result(self) -> None:
        task = {"current_stage": 0, "stages": [{"role": "implementer"}]}
        self.assertIsNone(status_data.gate_result(task))


class StageDescriptorTests(unittest.TestCase):
    def test_no_stages(self) -> None:
        self.assertIsNone(status_data.stage_descriptor({}))

    def test_basic_descriptor(self) -> None:
        task = {
            "current_stage": 0,
            "stages": [
                {"role": "implementer", "agent": "claude:sonnet"},
                {"role": "designer"},
            ],
        }
        d = status_data.stage_descriptor(task)
        self.assertIsNotNone(d)
        assert d is not None
        self.assertEqual(d["index"], 0)
        self.assertEqual(d["count"], 2)
        self.assertEqual(d["role"], "implementer")
        self.assertEqual(d["agent"], "claude:sonnet")
        self.assertEqual(d["review"], "")

    def test_peer_review_label(self) -> None:
        task = {
            "current_stage": 0,
            "stages": [
                {
                    "role": "implementer",
                    "agent": "claude:sonnet",
                    "peer_review": {"phase": "reviewing", "iteration": 2},
                }
            ],
        }
        d = status_data.stage_descriptor(task)
        assert d is not None
        self.assertEqual(d["review"], "review 2/3")

    def test_peer_review_label_explicit_max(self) -> None:
        task = {
            "current_stage": 0,
            "stages": [
                {
                    "role": "implementer",
                    "agent": "claude:sonnet",
                    "peer_review": {
                        "phase": "reviewing",
                        "iteration": 2,
                        "max_iterations": 5,
                    },
                }
            ],
        }
        d = status_data.stage_descriptor(task)
        assert d is not None
        self.assertEqual(d["review"], "review 2/5")


class PeerReviewProgressTests(unittest.TestCase):
    def test_not_a_dict(self) -> None:
        self.assertEqual(status_data.peer_review_progress(None), "")

    def test_inactive_phase(self) -> None:
        self.assertEqual(
            status_data.peer_review_progress({"phase": "approved", "iteration": 2}),
            "",
        )

    def test_default_cap(self) -> None:
        self.assertEqual(
            status_data.peer_review_progress({"phase": "reviewing", "iteration": 2}),
            "review 2/3",
        )

    def test_max_from_peer_review_dict(self) -> None:
        self.assertEqual(
            status_data.peer_review_progress(
                {"phase": "implementing", "iteration": 1, "max_iterations": 4}
            ),
            "review 1/4",
        )

    def test_bare_review_when_no_iteration(self) -> None:
        self.assertEqual(
            status_data.peer_review_progress({"phase": "reviewing"}), "review"
        )

    def test_invalid_max_falls_back(self) -> None:
        self.assertEqual(
            status_data.peer_review_progress(
                {"phase": "reviewing", "iteration": 2, "max_iterations": "oops"}
            ),
            "review 2/3",
        )


class ScanInflightTasksTests(unittest.TestCase):
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

    def test_no_tasks(self) -> None:
        result = status_data.scan_inflight_tasks()
        self.assertEqual(result, {})

    def test_non_terminal_task_appears(self) -> None:
        state.save_task(
            self.state_dir,
            "abc",
            {"id": "abc", "title": "do thing", "status": "running"},
        )
        result = status_data.scan_inflight_tasks()
        self.assertIn("main", result)
        ids = [t["id"] for t in result["main"]]
        self.assertIn("abc", ids)

    def test_terminal_task_excluded(self) -> None:
        for s in state.TERMINAL_STATUSES:
            state.save_task(
                self.state_dir,
                f"done-{s}",
                {"id": f"done-{s}", "title": "x", "status": s},
            )
        result = status_data.scan_inflight_tasks()
        self.assertEqual(result, {})


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class ListArchivedTasksTests(unittest.TestCase):
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

    def _archive(self, task_id: str, **fields: object) -> Path:
        adir = self.state_dir / "tasks" / "_archive" / f"task-{task_id}"
        adir.mkdir(parents=True, exist_ok=True)
        data = {"id": task_id, "title": "t", "status": "completed", **fields}
        (adir / "task.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        return adir

    def test_no_archive_dir(self) -> None:
        self.assertEqual(state.list_archived_tasks(self.state_dir), [])

    def test_reads_archived_yaml_and_injects_mtime(self) -> None:
        self._archive("abc", title="archived thing", formation="solo")
        out = state.list_archived_tasks(self.state_dir)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "abc")
        self.assertEqual(out[0]["title"], "archived thing")
        self.assertIsInstance(out[0]["archive_mtime"], float)

    def test_id_falls_back_to_dir_name(self) -> None:
        adir = self.state_dir / "tasks" / "_archive" / "task-noid"
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "task.yaml").write_text(
            yaml.safe_dump({"title": "x", "status": "completed"}), encoding="utf-8"
        )
        out = state.list_archived_tasks(self.state_dir)
        self.assertEqual(out[0]["id"], "noid")


class CollectCompletedTests(unittest.TestCase):
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

    def _archive(self, task_id: str, **fields: object) -> Path:
        adir = self.state_dir / "tasks" / "_archive" / f"task-{task_id}"
        adir.mkdir(parents=True, exist_ok=True)
        data = {
            "id": task_id,
            "title": f"task {task_id}",
            "status": "completed",
            "formation": "solo",
            **fields,
        }
        (adir / "task.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        return adir

    def _emit(self, task_id: str, etype: str, dt: datetime) -> None:
        line = json.dumps({"ts": _iso(dt), "type": etype, "task_id": task_id})
        with open(self.state_dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def test_no_archive_returns_empty(self) -> None:
        result = status_data.collect_completed()
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["within_days"], 30)
        self.assertEqual(result["truncated"], 0)

    def test_task_within_window_from_done_event(self) -> None:
        now = datetime.now(timezone.utc)
        self._archive("abc", title="recent task")
        self._emit("abc", "done", now - timedelta(hours=2))
        result = status_data.collect_completed(within_days=1)
        self.assertEqual(len(result["tasks"]), 1)
        row = result["tasks"][0]
        self.assertEqual(row["id"], "abc")
        self.assertEqual(row["project"], "demo")
        self.assertEqual(row["title"], "recent task")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["formation"], "solo")
        self.assertIsInstance(row["completed_epoch"], int)

    def test_completion_ts_uses_latest_terminal_event(self) -> None:
        now = datetime.now(timezone.utc)
        self._archive("abc")
        self._emit("abc", "done", now - timedelta(hours=5))
        self._emit("abc", "cleanup", now - timedelta(hours=1))
        result = status_data.collect_completed(within_days=1)
        expected = _iso(now - timedelta(hours=1))
        self.assertEqual(result["tasks"][0]["completed_ts"], expected)

    def test_outside_window_excluded(self) -> None:
        now = datetime.now(timezone.utc)
        self._archive("old")
        self._emit("old", "done", now - timedelta(days=10))
        self.assertEqual(status_data.collect_completed(within_days=1)["tasks"], [])
        # …but visible within a wider window.
        self.assertEqual(len(status_data.collect_completed(within_days=30)["tasks"]), 1)

    def test_mtime_fallback_when_no_terminal_event(self) -> None:
        # No events emitted → falls back to the freshly-created dir mtime.
        self._archive("nomtime")
        result = status_data.collect_completed(within_days=1)
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(result["tasks"][0]["id"], "nomtime")

    def test_sorted_most_recent_first(self) -> None:
        now = datetime.now(timezone.utc)
        self._archive("older")
        self._archive("newer")
        self._emit("older", "done", now - timedelta(hours=10))
        self._emit("newer", "done", now - timedelta(hours=1))
        ids = [r["id"] for r in status_data.collect_completed(within_days=1)["tasks"]]
        self.assertEqual(ids, ["newer", "older"])

    def test_row_cap_truncates_and_reports(self) -> None:
        now = datetime.now(timezone.utc)
        for i in range(3):
            self._archive(f"t{i}")
            self._emit(f"t{i}", "done", now - timedelta(hours=i + 1))
        result = status_data.collect_completed(within_days=1, row_cap=2)
        self.assertEqual(len(result["tasks"]), 2)
        self.assertEqual(result["truncated"], 1)

    def test_in_global_snapshot(self) -> None:
        now = datetime.now(timezone.utc)
        self._archive("abc")
        self._emit("abc", "merge", now - timedelta(hours=1))
        snap = status_data.collect_global_snapshot()
        self.assertIn("completed", snap)
        self.assertEqual(len(snap["completed"]["tasks"]), 1)
