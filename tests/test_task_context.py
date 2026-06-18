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

    # -- Phase 6: --project (leader path) ----------------------------------

    def test_project_name_resolves_by_registry(self) -> None:
        sd, tid = task_context.resolve(
            explicit_id="7", cwd=Path("/tmp"), project_name="demo"
        )
        self.assertEqual(sd, self.state_dir.resolve())
        self.assertEqual(tid, "7")

    def test_project_name_wins_over_fleet_state_dir(self) -> None:
        # Leader pane: FLEET_STATE_DIR is the session dir, but --project names
        # the real project and must override it.
        session = state.session_dir("main")
        session.mkdir(parents=True, exist_ok=True)
        os.environ["FLEET_STATE_DIR"] = str(session)
        sd, tid = task_context.resolve(
            explicit_id="7", cwd=Path("/tmp"), project_name="demo"
        )
        self.assertEqual(sd, self.state_dir.resolve())
        self.assertEqual(tid, "7")

    def test_unknown_project_name_raises(self) -> None:
        with self.assertRaises(task_context.TaskNotFound) as cm:
            task_context.resolve(explicit_id="7", project_name="nope")
        self.assertIn("nope", str(cm.exception))

    def test_session_dir_state_dir_demands_project(self) -> None:
        # FLEET_STATE_DIR = a leader session dir, no --project → clear error
        # that points at --project (no silent cwd fallback for the leader path).
        session = state.session_dir("main")
        session.mkdir(parents=True, exist_ok=True)
        os.environ["FLEET_STATE_DIR"] = str(session)
        with self.assertRaises(task_context.TaskNotFound) as cm:
            task_context.resolve(explicit_id="7", cwd=Path("/tmp"))
        self.assertIn("--project", str(cm.exception))


class ResolveProjectStateDirTests(unittest.TestCase):
    """Tests for :func:`task_context.resolve_project_state_dir`."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)
        self._saved_state_dir = os.environ.pop("FLEET_STATE_DIR", None)

    def tearDown(self) -> None:
        if self._saved_state_dir is not None:
            os.environ["FLEET_STATE_DIR"] = self._saved_state_dir
        else:
            os.environ.pop("FLEET_STATE_DIR", None)
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_explicit_project_name_resolves(self) -> None:
        sd = task_context.resolve_project_state_dir(project_name="demo")
        self.assertEqual(sd, self.state_dir.resolve())

    def test_explicit_project_name_ignores_fleet_state_dir(self) -> None:
        session = state.session_dir("main")
        session.mkdir(parents=True, exist_ok=True)
        os.environ["FLEET_STATE_DIR"] = str(session)
        sd = task_context.resolve_project_state_dir(
            project_name="demo", cwd=Path("/tmp")
        )
        self.assertEqual(sd, self.state_dir.resolve())

    def test_unknown_project_name_raises(self) -> None:
        with self.assertRaises(task_context.ProjectNotFound) as cm:
            task_context.resolve_project_state_dir(project_name="nope")
        self.assertIn("nope", str(cm.exception))

    def test_leader_session_dir_raises(self) -> None:
        session = state.session_dir("main")
        session.mkdir(parents=True, exist_ok=True)
        os.environ["FLEET_STATE_DIR"] = str(session)
        with self.assertRaises(task_context.ProjectNotFound) as cm:
            task_context.resolve_project_state_dir(cwd=Path("/tmp"))
        self.assertIn("--project", str(cm.exception))

    def test_cwd_fallback_resolves_registered_project(self) -> None:
        sd = task_context.resolve_project_state_dir(cwd=self.project)
        self.assertEqual(sd, self.state_dir.resolve())

    def test_cwd_fallback_with_no_project_raises(self) -> None:
        unregistered = Path(self._tmp.name) / "other"
        unregistered.mkdir()
        with self.assertRaises(task_context.ProjectNotFound) as cm:
            task_context.resolve_project_state_dir(cwd=unregistered)
        self.assertIn("--project", str(cm.exception))

    def test_fleet_state_dir_non_session_used(self) -> None:
        os.environ["FLEET_STATE_DIR"] = str(self.state_dir)
        sd = task_context.resolve_project_state_dir(cwd=Path("/tmp"))
        self.assertEqual(sd, self.state_dir.resolve())


if __name__ == "__main__":
    unittest.main()
