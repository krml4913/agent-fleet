"""Tests for ``fleet cost`` / ``fleet usage`` and the shared usage helpers.

Covers the on-demand aggregation (grouped by session / project / vendor /
stage), the cost-absent path (tokens render, cost shows ``—``, never a crash or a
fabricated number), and the CLI text + ``--json`` surfaces.
"""
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

import yaml  # noqa: E402

from fleet import state, status_data  # noqa: E402
from fleet.commands import cost  # noqa: E402
from tests._fleet_test_helpers import make_project, run_fleet  # noqa: E402


# ---------------------------------------------------------------------------
# status_data usage helpers (the single source of truth for every reader)
# ---------------------------------------------------------------------------


class TaskUsageTests(unittest.TestCase):
    def test_absent_block(self) -> None:
        self.assertIsNone(status_data.task_usage({}))
        self.assertIsNone(status_data.task_usage({"usage": None}))
        self.assertIsNone(status_data.task_usage({"usage": "nope"}))

    def test_zero_tokens_is_absent(self) -> None:
        self.assertIsNone(
            status_data.task_usage({"usage": {"input_tokens": 0, "output_tokens": 0}})
        )

    def test_tokens_only(self) -> None:
        u = status_data.task_usage(
            {"usage": {"input_tokens": 100, "output_tokens": 20}}
        )
        self.assertEqual(
            u, {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cost": None}
        )

    def test_tokens_and_cost(self) -> None:
        u = status_data.task_usage(
            {"usage": {"input_tokens": 100, "output_tokens": 20, "cost": 0.5}}
        )
        assert u is not None
        self.assertEqual(u["cost"], 0.5)
        self.assertEqual(u["total_tokens"], 120)

    def test_malformed_values_degrade(self) -> None:
        # Negative / non-numeric tokens coerce to 0; a bad cost degrades to None.
        u = status_data.task_usage(
            {"usage": {"input_tokens": "x", "output_tokens": 5, "cost": "free"}}
        )
        self.assertEqual(u, {"input_tokens": 0, "output_tokens": 5, "total_tokens": 5, "cost": None})


class UsageFormattingTests(unittest.TestCase):
    def test_humanize_tokens(self) -> None:
        self.assertEqual(status_data.humanize_tokens(0), "0")
        self.assertEqual(status_data.humanize_tokens(999), "999")
        self.assertEqual(status_data.humanize_tokens(1500), "1.5k")
        self.assertEqual(status_data.humanize_tokens(2_500_000), "2.50M")

    def test_format_cost(self) -> None:
        self.assertEqual(status_data.format_cost(None), "—")
        self.assertEqual(status_data.format_cost(0.5), "~$0.50")

    def test_format_usage_cell(self) -> None:
        self.assertEqual(status_data.format_usage_cell(None), "—")
        self.assertEqual(
            status_data.format_usage_cell(
                {"total_tokens": 1500, "cost": None}
            ),
            "1.5k · —",
        )
        self.assertEqual(
            status_data.format_usage_cell(
                {"total_tokens": 1500, "cost": 0.5}
            ),
            "1.5k · ~$0.50",
        )

    def test_task_usage_cell_from_task(self) -> None:
        self.assertEqual(status_data.task_usage_cell({}), "—")
        self.assertEqual(
            status_data.task_usage_cell(
                {"usage": {"input_tokens": 1000, "output_tokens": 500}}
            ),
            "1.5k · —",
        )

    def test_usage_tooltip(self) -> None:
        self.assertEqual(status_data.usage_tooltip(None), "")
        self.assertIn(
            "cost not reported",
            status_data.usage_tooltip({"input_tokens": 1, "output_tokens": 2, "cost": None}),
        )
        self.assertIn(
            "cost ~$0.50",
            status_data.usage_tooltip({"input_tokens": 1, "output_tokens": 2, "cost": 0.5}),
        )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class _FleetHomeCase(unittest.TestCase):
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

    def _archive(self, task_id: str, data: dict) -> None:
        adir = self.state_dir / "tasks" / "_archive" / f"task-{task_id}"
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "task.yaml").write_text(yaml.safe_dump({"id": task_id, **data}), "utf-8")


class AggregateTests(_FleetHomeCase):
    def _seed(self) -> None:
        state.save_task(self.state_dir, "a", {
            "title": "claude task", "status": "completed", "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:opus"}],
            "usage": {"input_tokens": 100, "output_tokens": 20},
        })
        state.save_task(self.state_dir, "b", {
            "title": "codex task", "status": "completed", "owner_session": "alpha",
            "current_stage": 0,
            "stages": [{"role": "reviewer", "agent": "codex:gpt-5.5"}],
            "usage": {"input_tokens": 200, "output_tokens": 30, "cost": 0.5},
        })
        # A task without a usage block contributes nothing.
        state.save_task(self.state_dir, "c", {
            "title": "no usage", "status": "running", "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:opus"}],
        })

    def test_totals_and_task_count(self) -> None:
        self._seed()
        agg = cost.aggregate()
        self.assertEqual(agg["task_count"], 2)
        self.assertEqual(agg["total"]["total_tokens"], 350)
        self.assertEqual(agg["total"]["input_tokens"], 300)
        self.assertTrue(agg["total"]["cost_known"])
        self.assertAlmostEqual(agg["total"]["cost"], 0.5)

    def test_grouped_by_all_dimensions(self) -> None:
        self._seed()
        agg = cost.aggregate()
        by = agg["groups"]
        self.assertEqual(set(by["session"]), {"main", "alpha"})
        self.assertEqual(by["session"]["main"]["total_tokens"], 120)
        self.assertEqual(by["session"]["alpha"]["total_tokens"], 230)
        self.assertEqual(by["project"]["demo"]["tasks"], 2)
        self.assertEqual(set(by["vendor"]), {"claude", "codex"})
        self.assertFalse(by["vendor"]["claude"]["cost_known"])
        self.assertTrue(by["vendor"]["codex"]["cost_known"])
        self.assertEqual(set(by["stage"]), {"driver", "reviewer"})

    def test_includes_archived_tasks(self) -> None:
        self._archive("z", {
            "title": "archived", "status": "completed", "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:opus"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })
        agg = cost.aggregate()
        self.assertEqual(agg["task_count"], 1)
        self.assertEqual(agg["total"]["total_tokens"], 15)

    def test_unresolvable_stage_falls_back(self) -> None:
        state.save_task(self.state_dir, "bad", {
            "title": "no stages", "status": "completed",
            "usage": {"input_tokens": 7, "output_tokens": 3},
        })
        agg = cost.aggregate()
        self.assertIn("unknown", agg["groups"]["vendor"])
        self.assertIn("-", agg["groups"]["stage"])


# ---------------------------------------------------------------------------
# CLI surfaces
# ---------------------------------------------------------------------------


class CostCommandTests(_FleetHomeCase):
    def _seed(self) -> None:
        state.save_task(self.state_dir, "a", {
            "title": "claude task", "status": "completed", "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:opus"}],
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        })
        state.save_task(self.state_dir, "b", {
            "title": "codex task", "status": "completed", "current_stage": 0,
            "stages": [{"role": "reviewer", "agent": "codex:gpt-5.5"}],
            "usage": {"input_tokens": 2000, "output_tokens": 300, "cost": 1.25},
        })

    def test_text_report(self) -> None:
        self._seed()
        r = run_fleet("cost", fleet_home=self.fleet_home, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("task(s) with recorded usage", r.stdout)
        for heading in ("By session", "By project", "By vendor", "By stage"):
            self.assertIn(heading, r.stdout)
        self.assertIn("claude", r.stdout)
        self.assertIn("codex", r.stdout)
        # Cost present on the codex bucket, absent ("—") on the claude bucket.
        self.assertIn("~$1.25", r.stdout)
        self.assertIn("—", r.stdout)

    def test_usage_alias(self) -> None:
        self._seed()
        r = run_fleet("usage", fleet_home=self.fleet_home, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("By vendor", r.stdout)

    def test_json_output(self) -> None:
        self._seed()
        r = run_fleet("cost", "--json", fleet_home=self.fleet_home, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        obj = json.loads(r.stdout)
        self.assertEqual(obj["task_count"], 2)
        self.assertEqual(obj["total"]["total_tokens"], 3800)
        self.assertEqual(obj["by"]["vendor"]["claude"]["cost"], None)
        self.assertEqual(obj["by"]["vendor"]["codex"]["cost"], 1.25)

    def test_empty_message(self) -> None:
        r = run_fleet("cost", fleet_home=self.fleet_home, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no recorded usage yet", r.stdout)


if __name__ == "__main__":
    unittest.main()
