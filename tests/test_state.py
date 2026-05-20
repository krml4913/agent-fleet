"""Tests for ``fleet.state`` — init / discover / project / task / dashboard."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from fleet import simple_yaml  # noqa: E402


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = self.project / ".fleet-state"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _init(self, name: str = "demo") -> None:
        state.init_state(self.state_dir, name=name)

    # ---- init ----

    def test_init_creates_layout(self) -> None:
        self._init()
        self.assertTrue(self.state_dir.is_dir())
        self.assertTrue((self.state_dir / "project.yaml").is_file())
        self.assertTrue((self.state_dir / "events.jsonl").is_file())
        self.assertTrue((self.state_dir / "tasks").is_dir())
        # dashboard is rebuilt by save_project inside init
        self.assertTrue((self.state_dir / "dashboard.md").is_file())

    def test_init_writes_project(self) -> None:
        self._init(name="alpha")
        loaded = state.load_project(self.state_dir)
        self.assertEqual(loaded["name"], "alpha")
        self.assertIn("created_at", loaded)
        self.assertEqual(loaded["version"], "0.0.1")

    # ---- discover ----

    def test_discover_returns_state_dir(self) -> None:
        self._init()
        found = state.discover_state_dir(self.project)
        # Compare via resolve() — on macOS /var/folders is a symlink to /private/var/folders.
        self.assertEqual(found, self.state_dir.resolve())

    def test_discover_finds_from_subdir(self) -> None:
        self._init()
        sub = self.project / "a" / "b"
        sub.mkdir(parents=True)
        found = state.discover_state_dir(sub)
        self.assertEqual(found, self.state_dir.resolve())

    def test_discover_none_when_absent(self) -> None:
        # No init. Use TemporaryDirectory which has no .fleet-state ancestor.
        with TemporaryDirectory() as tmp:
            self.assertIsNone(state.discover_state_dir(Path(tmp)))

    # ---- tasks ----

    def test_save_and_load_task(self) -> None:
        self._init()
        state.save_task(
            self.state_dir,
            "1",
            {"title": "first task", "status": "pending", "agent": "claude:sonnet"},
        )
        loaded = state.load_task(self.state_dir, "1")
        self.assertEqual(loaded["title"], "first task")
        self.assertEqual(loaded["status"], "pending")
        self.assertEqual(loaded["id"], "1")

    def test_list_tasks_orders_and_filters(self) -> None:
        self._init()
        state.save_task(self.state_dir, "2", {"title": "second", "status": "x"})
        state.save_task(self.state_dir, "1", {"title": "first", "status": "y"})
        # Garbage dir without task.yaml should be ignored.
        (self.state_dir / "tasks" / "task-garbage").mkdir()
        tasks = state.list_tasks(self.state_dir)
        self.assertEqual([t["id"] for t in tasks], ["1", "2"])

    def test_save_and_load_task_with_stages(self) -> None:
        self._init()
        stages = [
            {"role": "driver", "agent": "claude:sonnet", "status": "running"},
        ]
        state.save_task(
            self.state_dir,
            "t1",
            {
                "title": "new schema task",
                "status": "spawning",
                "topology": "solo",
                "workflow": "bare",
                "current_stage": 0,
                "stages": stages,
            },
        )
        loaded = state.load_task(self.state_dir, "t1")
        self.assertEqual(str(loaded["id"]), "t1")
        self.assertEqual(loaded["status"], "spawning")
        self.assertIsInstance(loaded["stages"], list)
        self.assertEqual(len(loaded["stages"]), 1)
        self.assertEqual(loaded["stages"][0]["role"], "driver")
        self.assertEqual(loaded["current_stage"], 0)

    # ---- derive_task_status ----

    def test_derive_task_status_all_done(self) -> None:
        stages = [
            {"role": "a", "status": "done"},
            {"role": "b", "status": "done"},
        ]
        self.assertEqual(state.derive_task_status(stages), "completed")

    def test_derive_task_status_any_running(self) -> None:
        stages = [
            {"role": "a", "status": "done"},
            {"role": "b", "status": "running"},
        ]
        self.assertEqual(state.derive_task_status(stages), "running")

    def test_derive_task_status_pending_only(self) -> None:
        stages = [
            {"role": "a", "status": "pending"},
        ]
        self.assertEqual(state.derive_task_status(stages), "spawning")

    def test_derive_task_status_empty(self) -> None:
        self.assertEqual(state.derive_task_status([]), "completed")

    # ---- get_current_stage_index ----

    def test_get_current_stage_index_first_pending(self) -> None:
        stages = [
            {"role": "a", "status": "pending"},
            {"role": "b", "status": "pending"},
        ]
        self.assertEqual(state.get_current_stage_index(stages), 0)

    def test_get_current_stage_index_skips_done(self) -> None:
        stages = [
            {"role": "a", "status": "done"},
            {"role": "b", "status": "pending"},
        ]
        self.assertEqual(state.get_current_stage_index(stages), 1)

    def test_get_current_stage_index_all_done(self) -> None:
        stages = [
            {"role": "a", "status": "done"},
            {"role": "b", "status": "done"},
        ]
        # All done → returns last index
        self.assertEqual(state.get_current_stage_index(stages), 1)

    # ---- dashboard ----

    def test_dashboard_rebuilt_on_task_save(self) -> None:
        self._init()
        state.save_task(self.state_dir, "1", {"title": "T", "status": "pending"})
        dash = (self.state_dir / "dashboard.md").read_text()
        self.assertIn("task-1" if False else "1", dash)  # id column shows the bare id
        self.assertIn("T", dash)
        self.assertIn("pending", dash)


if __name__ == "__main__":
    unittest.main()
