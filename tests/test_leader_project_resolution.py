"""Phase 6 (Issue #166): leader-facing commands resolve cross-project via --project.

A project-agnostic leader pane sets ``FLEET_STATE_DIR`` to its session dir
(``global/sessions/<label>/``), which owns no ``tasks/``. ``approve`` / ``reject``
/ ``cleanup`` must therefore resolve the target project by ``--project <name>``
from the registry regardless of ``FLEET_STATE_DIR`` — and, when ``--project`` is
missing, fail with a message that points at it rather than dead-ending on
"task.yaml missing".
"""
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
from tests._fleet_test_helpers import make_project, run_fleet_agent  # noqa: E402


def _approval_task(task_id: str) -> dict:
    return {
        "id": task_id,
        "title": "x",
        "status": "awaiting_orders",
        "formation": "solo",
        "workspace": "none",
        "owner_session": "main",
        "current_stage": 0,
        "stages": [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "asked"},
            }
        ],
    }


class LeaderProjectResolutionTests(unittest.TestCase):
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
        # The leader pane's FLEET_STATE_DIR is its session dir — no tasks/ here.
        self.session_dir = state.session_dir("main")
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old
        self._tmp.cleanup()

    def _save(self, task_id: str, data: dict) -> None:
        state.save_task(self.state_dir, task_id, data)

    def _run(self, *args: str) -> "subprocess.CompletedProcess[str]":  # noqa: F821
        # Simulate a leader pane: FLEET_STATE_DIR = the session dir, cwd is not a
        # project tree we want resolution to lean on.
        return run_fleet_agent(
            *args,
            fleet_home=self.fleet_home,
            cwd=self.repo,
            env_extra={
                "FLEET_STATE_DIR": str(self.session_dir),
                "FLEET_NO_TMUX": "1",
            },
        )

    # -- cleanup ------------------------------------------------------------

    def test_cleanup_cross_project_with_project(self) -> None:
        self._save("1", {
            "id": "1", "title": "t1", "status": "completed",
            "agent": "claude:sonnet", "workspace": "none", "owner_session": "main",
        })
        r = self._run("cleanup", "1", "--project", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cleaned", r.stdout)

    def test_cleanup_without_project_points_at_project_flag(self) -> None:
        self._save("1", {
            "id": "1", "title": "t1", "status": "completed",
            "agent": "claude:sonnet", "workspace": "none", "owner_session": "main",
        })
        r = self._run("cleanup", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--project", r.stderr)
        self.assertNotIn("task.yaml missing", r.stderr)

    def test_cleanup_unknown_project_name(self) -> None:
        r = self._run("cleanup", "1", "--project", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("nope", r.stderr)

    # -- approve / reject ---------------------------------------------------

    def test_approve_cross_project_with_project(self) -> None:
        self._save("1", _approval_task("1"))
        r = self._run("approve", "1", "--project", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "approved")

    def test_reject_cross_project_with_project(self) -> None:
        self._save("1", _approval_task("1"))
        r = self._run("reject", "1", "--project", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        task = state.load_task(self.state_dir, "1")
        self.assertEqual(task["stages"][0]["user_approval"]["status"], "pending")

    def test_approve_without_project_points_at_project_flag(self) -> None:
        self._save("1", _approval_task("1"))
        r = self._run("approve", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--project", r.stderr)


if __name__ == "__main__":
    unittest.main()
