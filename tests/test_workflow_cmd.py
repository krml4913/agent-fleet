"""Tests for ``fleet workflow`` CLI."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402


def run_fleet(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("FLEET_TASK_ID", None)
    return subprocess.run(
        [sys.executable, str(FLEET), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
    )


class WorkflowCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        state.init_state(self.project / ".fleet-state", name="demo")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_list(self) -> None:
        r = run_fleet("workflow", "--project", str(self.project), "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("bare", r.stdout)
        self.assertIn("git_worktree", r.stdout)
        self.assertIn("active workflow:", r.stdout)
        self.assertIn("bare", r.stdout.split("active workflow:")[1])

    def test_show_bare(self) -> None:
        r = run_fleet("workflow", "--project", str(self.project), "show", "bare")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("workflow: bare", r.stdout)
        self.assertIn("on_pre_spawn", r.stdout)
        self.assertIn("on_post_done", r.stdout)

    def test_show_unknown(self) -> None:
        r = run_fleet(
            "workflow", "--project", str(self.project), "show", "no-such",
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("workflow not found", r.stderr)

    def test_set_changes_project_yaml(self) -> None:
        r = run_fleet(
            "workflow", "--project", str(self.project), "set", "git_worktree",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        project = state.load_project(self.project / ".fleet-state")
        self.assertEqual(project["workflow"], "git_worktree")

    def test_set_unknown(self) -> None:
        r = run_fleet(
            "workflow", "--project", str(self.project), "set", "no-such",
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("workflow not found", r.stderr)


if __name__ == "__main__":
    unittest.main()
