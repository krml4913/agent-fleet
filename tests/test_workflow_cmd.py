"""Tests for ``fleet workflow`` CLI."""
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
from tests._fleet_test_helpers import run_fleet, make_project  # noqa: E402


class WorkflowCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_list(self) -> None:
        r = run_fleet("workflow", "--project", "demo", "list", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("bare", r.stdout)
        self.assertIn("git_worktree", r.stdout)
        self.assertIn("active workflow:", r.stdout)
        self.assertIn("bare", r.stdout.split("active workflow:")[1])

    def test_show_bare(self) -> None:
        r = run_fleet("workflow", "--project", "demo", "show", "bare",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("workflow: bare", r.stdout)
        self.assertIn("on_pre_start", r.stdout)
        self.assertIn("on_post_done", r.stdout)

    def test_show_unknown(self) -> None:
        r = run_fleet("workflow", "--project", "demo", "show", "no-such",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("workflow not found", r.stderr)

    def test_set_changes_project_yaml(self) -> None:
        r = run_fleet("workflow", "--project", "demo", "set", "git_worktree",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        project = state.load_project(self.state_dir)
        self.assertEqual(project["workflow"], "git_worktree")

    def test_set_unknown(self) -> None:
        r = run_fleet("workflow", "--project", "demo", "set", "no-such",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("workflow not found", r.stderr)


if __name__ == "__main__":
    unittest.main()
