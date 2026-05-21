"""Tests for ``fleet preflight``."""
from __future__ import annotations

import subprocess
import sys
import types
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet"
sys.path.insert(0, str(ROOT / "src"))

from fleet.commands import preflight  # noqa: E402


class PreflightLibraryTests(unittest.TestCase):
    def test_check_all_returns_results(self) -> None:
        results = preflight.check_all()
        names = [r.name for r in results]
        for required in ("python", "tmux"):
            self.assertIn(required, names)
        self.assertIn("git", names)

    def test_python_marked_required(self) -> None:
        results = {r.name: r for r in preflight.check_all()}
        self.assertTrue(results["python"].required)

    def test_optionals_dont_block(self) -> None:
        results = {r.name: r for r in preflight.check_all()}
        self.assertFalse(results["git"].required)
        self.assertFalse(results["claude"].required)
        self.assertFalse(results["codex"].required)
        self.assertFalse(results["codex-trust"].required)

    def test_git_required_for_git_worktree_workflow(self) -> None:
        with (
            unittest.mock.patch(
                "fleet.commands.preflight.state_mod.resolve_state_dir",
                return_value=Path("/tmp/state"),
            ),
            unittest.mock.patch(
                "fleet.commands.preflight.plugins_mod.load_workflow",
                return_value=types.SimpleNamespace(WORKFLOW_NAME="git_worktree"),
            ),
        ):
            self.assertTrue(preflight._git_required_for_cwd(Path("/tmp/repo")))

    def test_git_optional_for_bare_workflow(self) -> None:
        with (
            unittest.mock.patch(
                "fleet.commands.preflight.state_mod.resolve_state_dir",
                return_value=Path("/tmp/state"),
            ),
            unittest.mock.patch(
                "fleet.commands.preflight.plugins_mod.load_workflow",
                return_value=types.SimpleNamespace(WORKFLOW_NAME="bare"),
            ),
        ):
            self.assertFalse(preflight._git_required_for_cwd(Path("/tmp/repo")))

    def test_codex_trust_warns_when_untrusted(self) -> None:
        with (
            unittest.mock.patch(
                "fleet.commands.preflight.shutil.which",
                return_value="/bin/codex",
            ),
            unittest.mock.patch(
                "fleet.commands.preflight._git_toplevel",
                return_value=Path("/tmp/repo"),
            ),
            unittest.mock.patch(
                "fleet.commands.preflight.agents_mod.codex_repo_trusted",
                return_value=False,
            ),
        ):
            result = preflight._check_codex_trust()

        self.assertEqual(result.name, "codex-trust")
        self.assertFalse(result.ok)
        self.assertFalse(result.required)
        self.assertIn("not trusted", result.detail)

    def test_codex_trust_skips_without_codex(self) -> None:
        with unittest.mock.patch("fleet.commands.preflight.shutil.which", return_value=None):
            result = preflight._check_codex_trust()

        self.assertTrue(result.ok)
        self.assertIn("skipped", result.detail)


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
