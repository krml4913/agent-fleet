"""Tests for ``fleet.task_context.resolve``."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state, task_context  # noqa: E402
from tests._fleet_test_helpers import make_project  # noqa: E402


class TaskContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)
        # Create a task dir so cwd-detection has something to find.
        (self.state_dir / "tasks" / "task-42").mkdir()
        self._saved_task_id = os.environ.pop("FLEET_TASK_ID", None)
        self._saved_state_dir = os.environ.pop("FLEET_STATE_DIR", None)

    def tearDown(self) -> None:
        for key, saved in [("FLEET_TASK_ID", self._saved_task_id),
                            ("FLEET_STATE_DIR", self._saved_state_dir)]:
            if saved is not None:
                os.environ[key] = saved
            else:
                os.environ.pop(key, None)
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_explicit_id_wins(self) -> None:
        os.environ["FLEET_TASK_ID"] = "env-id"
        os.environ["FLEET_STATE_DIR"] = str(self.state_dir)
        sd, tid = task_context.resolve(explicit_id="9", cwd=self.project)
        self.assertEqual(sd, self.state_dir.resolve())
        self.assertEqual(tid, "9")

    def test_env_var(self) -> None:
        os.environ["FLEET_TASK_ID"] = "5"
        os.environ["FLEET_STATE_DIR"] = str(self.state_dir)
        sd, tid = task_context.resolve(cwd=self.project)
        self.assertEqual(tid, "5")

    def test_fleet_state_dir_env_wins_over_cwd(self) -> None:
        os.environ["FLEET_STATE_DIR"] = str(self.state_dir)
        os.environ["FLEET_TASK_ID"] = "77"
        sd, tid = task_context.resolve(cwd=Path("/tmp"))
        self.assertEqual(sd, self.state_dir.resolve())
        self.assertEqual(tid, "77")

    def test_cwd_inside_task_dir(self) -> None:
        inner = self.state_dir / "tasks" / "task-42"
        os.environ["FLEET_STATE_DIR"] = str(self.state_dir)
        sd, tid = task_context.resolve(cwd=inner)
        self.assertEqual(tid, "42")

    def test_cwd_deeper_inside_task_dir(self) -> None:
        deeper = self.state_dir / "tasks" / "task-42" / "subdir"
        deeper.mkdir()
        os.environ["FLEET_STATE_DIR"] = str(self.state_dir)
        sd, tid = task_context.resolve(cwd=deeper)
        self.assertEqual(tid, "42")

    def test_no_state_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(task_context.TaskNotFound):
                task_context.resolve(cwd=Path(tmp))

    def test_no_id_source(self) -> None:
        os.environ["FLEET_STATE_DIR"] = str(self.state_dir)
        with self.assertRaises(task_context.TaskNotFound):
            task_context.resolve(cwd=self.project)

    def test_cwd_based_state_resolution(self) -> None:
        # Without FLEET_STATE_DIR, resolve from cwd via registry.
        os.environ["FLEET_TASK_ID"] = "99"
        sd, tid = task_context.resolve(cwd=self.project)
        self.assertEqual(sd, self.state_dir.resolve())
        self.assertEqual(tid, "99")


if __name__ == "__main__":
    unittest.main()
