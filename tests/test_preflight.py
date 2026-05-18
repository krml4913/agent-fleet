"""Tests for ``fleet preflight``."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet"
sys.path.insert(0, str(ROOT / "src"))

from fleet.commands import preflight  # noqa: E402


class PreflightLibraryTests(unittest.TestCase):
    def test_check_all_returns_results(self) -> None:
        results = preflight.check_all()
        names = [r.name for r in results]
        for required in ("python", "tmux", "git"):
            self.assertIn(required, names)

    def test_python_marked_required(self) -> None:
        results = {r.name: r for r in preflight.check_all()}
        self.assertTrue(results["python"].required)

    def test_optionals_dont_block(self) -> None:
        results = {r.name: r for r in preflight.check_all()}
        self.assertFalse(results["claude"].required)
        self.assertFalse(results["codex"].required)


class PreflightCmdSmokeTest(unittest.TestCase):
    """The CLI itself should at least run; exit code depends on the host."""

    def test_cli_runs(self) -> None:
        r = subprocess.run(
            [sys.executable, str(FLEET), "preflight"],
            capture_output=True,
            text=True,
        )
        self.assertIn("python", r.stdout)
        # Exit 0 or 1 depending on the host; just assert it ran.
        self.assertIn(r.returncode, (0, 1))


if __name__ == "__main__":
    unittest.main()
