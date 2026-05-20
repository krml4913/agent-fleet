"""Tests for fleet memory initialisation via ``fleet init``."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet"


def run_fleet(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FLEET), *args],
        capture_output=True,
        text=True,
    )


class MemoryInitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_creates_memory_directory(self) -> None:
        result = run_fleet("init", "--name", "mem-test", str(self.project))
        self.assertEqual(result.returncode, 0, result.stderr)
        memory_dir = self.project / ".fleet-state" / "memory"
        self.assertTrue(memory_dir.is_dir())

    def test_creates_memory_index(self) -> None:
        run_fleet("init", "--name", "mem-test", str(self.project))
        memory_index = self.project / ".fleet-state" / "memory" / "MEMORY.md"
        self.assertTrue(memory_index.is_file())
        content = memory_index.read_text(encoding="utf-8")
        self.assertIn("Memory Index", content)

    def test_creates_guide(self) -> None:
        run_fleet("init", "--name", "mem-test", str(self.project))
        guide = self.project / ".fleet-state" / "memory" / "GUIDE.md"
        self.assertTrue(guide.is_file())
        content = guide.read_text(encoding="utf-8")
        self.assertIn("feedback", content)
        self.assertIn("project", content)
        self.assertIn("reference", content)

    def test_guide_has_no_user_type(self) -> None:
        run_fleet("init", "--name", "mem-test", str(self.project))
        guide = self.project / ".fleet-state" / "memory" / "GUIDE.md"
        content = guide.read_text(encoding="utf-8")
        # user type is excluded from fleet memory (it's claude auto-memory only)
        self.assertNotIn("### user", content)


class DriverPromptBudgetTest(unittest.TestCase):
    """Guard that adding the memory entry didn't blow the line-count cap."""

    def test_driver_base_under_budget(self) -> None:
        base = (ROOT / "docs" / "prompts" / "driver-base.md").read_text(encoding="utf-8")
        line_count = base.count("\n")
        # driver_prompt.py adds ~12 lines of metadata; total cap is 70
        self.assertLess(line_count, 58, f"driver-base.md too long: {line_count} lines")


if __name__ == "__main__":
    unittest.main()
