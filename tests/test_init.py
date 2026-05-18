"""Tests for ``fleet init`` — stdlib-only (unittest) so they run anywhere."""
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


class InitCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_creates_state_dir(self) -> None:
        result = run_fleet("init", "--name", "demo", str(self.project))
        self.assertEqual(result.returncode, 0, result.stderr)
        state_dir = self.project / ".fleet-state"
        self.assertTrue(state_dir.is_dir())
        self.assertTrue((state_dir / "project.yaml").is_file())
        self.assertTrue((state_dir / "events.jsonl").is_file())
        self.assertTrue((state_dir / "tasks").is_dir())
        self.assertIn("name: demo", (state_dir / "project.yaml").read_text())

    def test_rejects_existing(self) -> None:
        run_fleet("init", "--name", "demo", str(self.project))
        result = run_fleet("init", "--name", "demo", str(self.project))
        self.assertEqual(result.returncode, 1)
        self.assertIn("already initialized", result.stderr)

    def test_rejects_nonexistent_path(self) -> None:
        nope = self.project / "nope"
        result = run_fleet("init", "--name", "demo", str(nope))
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
