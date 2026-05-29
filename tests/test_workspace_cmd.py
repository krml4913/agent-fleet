"""Tests for ``fleet workspace`` CLI."""
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


class WorkspaceCmdTests(unittest.TestCase):
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
        r = run_fleet("workspace", "--project", "demo", "list", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("worktree", r.stdout)
        self.assertIn("none", r.stdout)
        self.assertIn("active workspace:", r.stdout)
        # Default is worktree
        self.assertIn("worktree", r.stdout.split("active workspace:")[1])

    def test_set_changes_project_yaml(self) -> None:
        r = run_fleet("workspace", "--project", "demo", "set", "none",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        project = state.load_project(self.state_dir)
        self.assertEqual(project["workspace"], "none")

    def test_set_back_to_worktree(self) -> None:
        r = run_fleet("workspace", "--project", "demo", "set", "worktree",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        project = state.load_project(self.state_dir)
        self.assertEqual(project["workspace"], "worktree")

    def test_set_invalid_value_fails(self) -> None:
        r = run_fleet("workspace", "--project", "demo", "set", "no-such",
                      fleet_home=self.fleet_home)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
