"""Tests for ``fleet rm`` — remove project from registry and state."""
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


class RmCommandTests(unittest.TestCase):
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

    def test_rm_removes_from_registry(self) -> None:
        result = run_fleet("rm", "--yes", "demo", fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        reg = state.load_registry()
        self.assertNotIn("demo", reg.get("projects", {}))

    def test_rm_deletes_state_dir(self) -> None:
        self.assertTrue(self.state_dir.is_dir())
        run_fleet("rm", "--yes", "demo", fleet_home=self.fleet_home)
        self.assertFalse(self.state_dir.exists())

    def test_rm_unknown_project_errors(self) -> None:
        result = run_fleet("rm", "--yes", "nope", fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found in registry", result.stderr)

    def test_rm_without_yes_requires_tty(self) -> None:
        # Running in non-TTY (subprocess) without --yes → error
        result = run_fleet("rm", "demo", fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("--yes", result.stderr)


if __name__ == "__main__":
    unittest.main()
