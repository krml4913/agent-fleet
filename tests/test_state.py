"""Tests for ``fleet.state`` — registry / init / resolve / project / task / dashboard."""
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
from fleet import simple_yaml  # noqa: E402


class _FleetHomeBase(unittest.TestCase):
    """Base: isolates FLEET_HOME to a tempdir for every test."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)

        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def _make_project(self, name: str = "demo", repo: Path | None = None) -> Path:
        repo = repo or self.project
        state_dir = state.project_state_dir(name)
        state.init_state(state_dir, name=name, repo=repo)
        state.register_project(name, repo)
        return state_dir


class RegistryTests(_FleetHomeBase):
    def test_empty_registry(self) -> None:
        reg = state.load_registry()
        self.assertEqual(reg["version"], 1)
        self.assertEqual(reg["projects"], {})

    def test_register_project(self) -> None:
        state.register_project("demo", self.project)
        reg = state.load_registry()
        self.assertIn("demo", reg["projects"])
        self.assertEqual(
            Path(reg["projects"]["demo"]["repo"]).resolve(),
            self.project.resolve(),
        )

    def test_register_duplicate_name_raises(self) -> None:
        state.register_project("demo", self.project)
        with self.assertRaises(ValueError):
            state.register_project("demo", self.project / "other")

    def test_register_duplicate_repo_raises(self) -> None:
        state.register_project("demo", self.project)
        with self.assertRaises(ValueError):
            state.register_project("other", self.project)

    def test_unregister_project(self) -> None:
        state.register_project("demo", self.project)
        state.unregister_project("demo")
        reg = state.load_registry()
        self.assertNotIn("demo", reg["projects"])

    def test_unregister_missing_raises(self) -> None:
        with self.assertRaises(KeyError):
            state.unregister_project("nope")


class InitTests(_FleetHomeBase):
    def test_init_creates_layout(self) -> None:
        state_dir = self._make_project()
        self.assertTrue(state_dir.is_dir())
        self.assertTrue((state_dir / "project.yaml").is_file())
        self.assertTrue((state_dir / "events.jsonl").is_file())
        self.assertTrue((state_dir / "tasks").is_dir())
        self.assertTrue((state_dir / "dashboard.md").is_file())

    def test_init_writes_project_with_repo(self) -> None:
        state_dir = self._make_project(name="alpha")
        loaded = state.load_project(state_dir)
        self.assertEqual(loaded["name"], "alpha")
        self.assertIn("created_at", loaded)
        self.assertEqual(loaded["version"], "0.0.1")
        self.assertEqual(
            Path(loaded["repo"]).resolve(), self.project.resolve()
        )


class ResolveTests(_FleetHomeBase):
    def test_resolve_by_name(self) -> None:
        state_dir = self._make_project("demo")
        found = state.resolve_state_dir(Path("/tmp"), project_name="demo")
        self.assertEqual(found, state_dir.resolve())

    def test_resolve_unknown_name_returns_none(self) -> None:
        self.assertIsNone(state.resolve_state_dir(Path("/tmp"), project_name="nope"))

    def test_resolve_cwd_under_repo(self) -> None:
        state_dir = self._make_project("demo")
        sub = self.project / "src" / "deep"
        sub.mkdir(parents=True)
        found = state.resolve_state_dir(sub)
        self.assertEqual(found, state_dir.resolve())

    def test_resolve_cwd_at_repo_root(self) -> None:
        state_dir = self._make_project("demo")
        found = state.resolve_state_dir(self.project)
        self.assertEqual(found, state_dir.resolve())

    def test_resolve_unregistered_cwd_returns_none(self) -> None:
        # No project registered → can't resolve.
        with TemporaryDirectory() as tmp:
            self.assertIsNone(state.resolve_state_dir(Path(tmp)))

    def test_resolve_from_state_tree(self) -> None:
        state_dir = self._make_project("demo")
        # cwd inside the state dir itself
        found = state.resolve_state_dir(state_dir / "tasks")
        self.assertEqual(found, state_dir.resolve())

    def test_resolve_monorepo_longest_match(self) -> None:
        mono = Path(self._tmp.name) / "mono"
        mono.mkdir()
        api = mono / "packages" / "api"
        api.mkdir(parents=True)

        sd_mono = state.project_state_dir("mono")
        state.init_state(sd_mono, name="mono", repo=mono)
        state.register_project("mono", mono)

        sd_api = state.project_state_dir("api")
        state.init_state(sd_api, name="api", repo=api)
        state.register_project("api", api)

        # cwd under api → longest match = api
        cwd_api = api / "src"
        cwd_api.mkdir()
        found = state.resolve_state_dir(cwd_api)
        self.assertEqual(found, sd_api.resolve())

        # cwd under mono but not api → mono
        cwd_mono = mono / "web"
        cwd_mono.mkdir()
        found2 = state.resolve_state_dir(cwd_mono)
        self.assertEqual(found2, sd_mono.resolve())


class TaskTests(_FleetHomeBase):
    def test_save_and_load_task(self) -> None:
        state_dir = self._make_project()
        state.save_task(
            state_dir, "1",
            {"title": "first task", "status": "pending", "agent": "claude:sonnet"},
        )
        loaded = state.load_task(state_dir, "1")
        self.assertEqual(loaded["title"], "first task")
        self.assertEqual(loaded["status"], "pending")
        self.assertEqual(loaded["id"], "1")

    def test_list_tasks_orders_and_filters(self) -> None:
        state_dir = self._make_project()
        state.save_task(state_dir, "2", {"title": "second", "status": "x"})
        state.save_task(state_dir, "1", {"title": "first", "status": "y"})
        (state_dir / "tasks" / "task-garbage").mkdir()
        tasks = state.list_tasks(state_dir)
        self.assertEqual([t["id"] for t in tasks], ["1", "2"])

    def test_save_and_load_task_with_stages(self) -> None:
        state_dir = self._make_project()
        stages = [{"role": "driver", "agent": "claude:sonnet", "status": "running"}]
        state.save_task(state_dir, "t1", {
            "title": "new schema task", "status": "spawning",
            "formation": "solo", "workflow": "bare",
            "current_stage": 0, "stages": stages,
        })
        loaded = state.load_task(state_dir, "t1")
        self.assertEqual(str(loaded["id"]), "t1")
        self.assertEqual(loaded["status"], "spawning")
        self.assertIsInstance(loaded["stages"], list)
        self.assertEqual(len(loaded["stages"]), 1)
        self.assertEqual(loaded["stages"][0]["role"], "driver")


class DeriveStatusTests(unittest.TestCase):
    def test_all_done(self) -> None:
        stages = [{"role": "a", "status": "done"}, {"role": "b", "status": "done"}]
        self.assertEqual(state.derive_task_status(stages), "completed")

    def test_any_running(self) -> None:
        stages = [{"role": "a", "status": "done"}, {"role": "b", "status": "running"}]
        self.assertEqual(state.derive_task_status(stages), "running")

    def test_pending_only(self) -> None:
        stages = [{"role": "a", "status": "pending"}]
        self.assertEqual(state.derive_task_status(stages), "spawning")

    def test_empty(self) -> None:
        self.assertEqual(state.derive_task_status([]), "completed")


class CurrentStageIndexTests(unittest.TestCase):
    def test_first_pending(self) -> None:
        stages = [{"role": "a", "status": "pending"}, {"role": "b", "status": "pending"}]
        self.assertEqual(state.get_current_stage_index(stages), 0)

    def test_skips_done(self) -> None:
        stages = [{"role": "a", "status": "done"}, {"role": "b", "status": "pending"}]
        self.assertEqual(state.get_current_stage_index(stages), 1)

    def test_all_done_returns_last(self) -> None:
        stages = [{"role": "a", "status": "done"}, {"role": "b", "status": "done"}]
        self.assertEqual(state.get_current_stage_index(stages), 1)


class DashboardTests(_FleetHomeBase):
    def test_dashboard_rebuilt_on_task_save(self) -> None:
        state_dir = self._make_project()
        state.save_task(state_dir, "1", {"title": "T", "status": "pending"})
        dash = (state_dir / "dashboard.md").read_text()
        self.assertIn("1", dash)
        self.assertIn("T", dash)
        self.assertIn("pending", dash)


if __name__ == "__main__":
    unittest.main()
