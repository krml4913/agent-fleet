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


class TaskContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        state.init_state(self.project / ".fleet-state", name="demo")
        self.state_dir = (self.project / ".fleet-state").resolve()
        # Create a task dir so cwd-detection has something to find.
        (self.state_dir / "tasks" / "task-42").mkdir()
        self._saved_env = os.environ.pop("FLEET_TASK_ID", None)

    def tearDown(self) -> None:
        if self._saved_env is not None:
            os.environ["FLEET_TASK_ID"] = self._saved_env
        else:
            os.environ.pop("FLEET_TASK_ID", None)
        self._tmp.cleanup()

    def test_explicit_id_wins(self) -> None:
        os.environ["FLEET_TASK_ID"] = "env-id"
        sd, tid = task_context.resolve(explicit_id="9", cwd=self.project)
        self.assertEqual(sd, self.state_dir)
        self.assertEqual(tid, "9")

    def test_env_var(self) -> None:
        os.environ["FLEET_TASK_ID"] = "5"
        sd, tid = task_context.resolve(cwd=self.project)
        self.assertEqual(tid, "5")

    def test_cwd_inside_task_dir(self) -> None:
        inner = self.state_dir / "tasks" / "task-42"
        sd, tid = task_context.resolve(cwd=inner)
        self.assertEqual(tid, "42")

    def test_cwd_deeper_inside_task_dir(self) -> None:
        deeper = self.state_dir / "tasks" / "task-42" / "subdir"
        deeper.mkdir()
        sd, tid = task_context.resolve(cwd=deeper)
        self.assertEqual(tid, "42")

    def test_no_state_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(task_context.TaskNotFound):
                task_context.resolve(cwd=Path(tmp))

    def test_no_id_source(self) -> None:
        with self.assertRaises(task_context.TaskNotFound):
            task_context.resolve(cwd=self.project)


if __name__ == "__main__":
    unittest.main()
