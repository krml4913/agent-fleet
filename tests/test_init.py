"""Tests for ``fleet init`` — stdlib-only (unittest) so they run anywhere."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from tests._fleet_test_helpers import run_fleet  # noqa: E402


class InitCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_creates_state_dir(self) -> None:
        result = run_fleet("init", "--name", "demo", str(self.project),
                           fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_dir = self.fleet_home / "projects" / "demo"
        self.assertTrue(state_dir.is_dir())
        self.assertTrue((state_dir / "project.yaml").is_file())
        self.assertTrue((state_dir / "events.jsonl").is_file())
        self.assertTrue((state_dir / "tasks").is_dir())
        self.assertIn("name: demo", (state_dir / "project.yaml").read_text())

    def test_name_defaults_to_basename(self) -> None:
        result = run_fleet("init", str(self.project), fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_dir = self.fleet_home / "projects" / "proj"
        self.assertTrue(state_dir.is_dir())

    def test_rejects_duplicate_name(self) -> None:
        run_fleet("init", "--name", "demo", str(self.project),
                  fleet_home=self.fleet_home)
        # Second init with same name → error
        proj2 = Path(self._tmp.name) / "proj2"
        proj2.mkdir()
        result = run_fleet("init", "--name", "demo", str(proj2),
                           fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("already registered", result.stderr)

    def test_rejects_duplicate_repo(self) -> None:
        run_fleet("init", "--name", "demo", str(self.project),
                  fleet_home=self.fleet_home)
        # Same path, different name → repo path duplicate error
        result = run_fleet("init", "--name", "demo2", str(self.project),
                           fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("already registered", result.stderr)

    def test_rejects_nonexistent_path(self) -> None:
        nope = self.project / "nope"
        result = run_fleet("init", "--name", "demo", str(nope),
                           fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
