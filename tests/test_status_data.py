"""Tests for fleet.status_data — presentation-agnostic derivation helpers."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

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
        self.assertEqual(d["review"], "review ×2")


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
