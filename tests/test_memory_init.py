"""Tests for fleet memory initialisation via ``fleet init``."""
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
from fleet import state  # noqa: E402


class MemoryInitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_creates_memory_directory(self) -> None:
        result = run_fleet("init", "--name", "mem-test", str(self.project),
                           fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        memory_dir = self.fleet_home / "projects" / "mem-test" / "memory"
        self.assertTrue(memory_dir.is_dir())

    def test_creates_memory_index(self) -> None:
        run_fleet("init", "--name", "mem-test", str(self.project),
                  fleet_home=self.fleet_home)
        memory_index = self.fleet_home / "projects" / "mem-test" / "memory" / "MEMORY.md"
        self.assertTrue(memory_index.is_file())
        content = memory_index.read_text(encoding="utf-8")
        self.assertIn("Memory Index", content)

    def test_creates_guide(self) -> None:
        run_fleet("init", "--name", "mem-test", str(self.project),
                  fleet_home=self.fleet_home)
        guide = self.fleet_home / "projects" / "mem-test" / "memory" / "GUIDE.md"
        self.assertTrue(guide.is_file())
        content = guide.read_text(encoding="utf-8")
        self.assertIn("feedback", content)
        self.assertIn("project", content)
        self.assertIn("reference", content)

    def test_guide_has_no_user_type(self) -> None:
        run_fleet("init", "--name", "mem-test", str(self.project),
                  fleet_home=self.fleet_home)
        guide = self.fleet_home / "projects" / "mem-test" / "memory" / "GUIDE.md"
        content = guide.read_text(encoding="utf-8")
        self.assertNotIn("### user", content)


class DriverPromptBudgetTest(unittest.TestCase):
    """Guard that adding the memory entry didn't blow the line-count cap."""

    def test_driver_base_under_budget(self) -> None:
        base = (ROOT / "docs" / "prompts" / "driver-base.md").read_text(encoding="utf-8")
        line_count = base.count("\n")
        self.assertLess(line_count, 58, f"driver-base.md too long: {line_count} lines")


if __name__ == "__main__":
    unittest.main()
