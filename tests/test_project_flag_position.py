"""``--project`` on grouped commands works both before and after the subcommand (#279)."""
from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import tests._fleet_test_helpers  # noqa: E402,F401  (hermetic env: FLEET_NO_NOTIFY / FLEET_NO_MUX)
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from fleet.cli import build_parser_agent, build_parser_user  # noqa: E402
from tests._fleet_test_helpers import make_project, run_fleet  # noqa: E402


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    found: dict[str, argparse.ArgumentParser] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            found.update(action.choices)
    return found


class GroupedCommandAuditTests(unittest.TestCase):
    """Every grouped command with a group-level ``--project`` must repeat it per subcommand."""

    def test_every_leaf_of_a_project_group_accepts_project(self) -> None:
        checked = 0
        for build in (build_parser_user, build_parser_agent):
            for cmd_name, cmd in _subparsers(build()).items():
                leaves = _subparsers(cmd)
                if not leaves or "--project" not in cmd._option_string_actions:
                    continue
                for leaf_name, leaf in leaves.items():
                    checked += 1
                    self.assertIn(
                        "--project",
                        leaf._option_string_actions,
                        f"`{cmd_name} {leaf_name}` rejects --project after the subcommand",
                    )
        # formation list/show/seed, workspace list/set, role seed
        self.assertGreaterEqual(checked, 6)


class ProjectFlagPositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.states: dict[str, Path] = {}
        for name in ("demo", "other"):
            repo = Path(self._tmp.name) / name
            repo.mkdir()
            self.states[name] = make_project(self.fleet_home, name, repo)
        self.cwd = Path(self._tmp.name)  # not inside any project

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def fleet(self, *args: str):
        return run_fleet(*args, fleet_home=self.fleet_home, cwd=self.cwd)

    def workspace_of(self, name: str) -> str:
        return state.load_project(self.states[name]).get("workspace", "worktree")

    # -- formation ---------------------------------------------------------

    def test_formation_show_after_subcommand(self) -> None:
        r = self.fleet("formation", "show", "solo", "--project", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("stages", r.stdout)

    def test_formation_show_before_subcommand(self) -> None:
        r = self.fleet("formation", "--project", "demo", "show", "solo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("stages", r.stdout)

    def test_formation_list_after_subcommand(self) -> None:
        r = self.fleet("formation", "list", "--project", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[project:demo]", r.stdout)

    def test_formation_list_before_subcommand(self) -> None:
        r = self.fleet("formation", "--project", "demo", "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[project:demo]", r.stdout)

    def test_formation_list_subcommand_project_wins(self) -> None:
        r = self.fleet("formation", "--project", "demo", "list", "--project", "other")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[project:other]", r.stdout)
        self.assertNotIn("[project:demo]", r.stdout)

    def test_formation_show_unknown_project_after_subcommand_errors(self) -> None:
        r = self.fleet("formation", "show", "solo", "--project", "no-such")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("unrecognized arguments", r.stderr)

    # -- workspace ---------------------------------------------------------

    def test_workspace_list_after_subcommand(self) -> None:
        r = self.fleet("workspace", "list", "--project", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("active workspace:", r.stdout)

    def test_workspace_set_after_subcommand(self) -> None:
        r = self.fleet("workspace", "set", "none", "--project", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.workspace_of("demo"), "none")
        self.assertEqual(self.workspace_of("other"), "worktree")

    def test_workspace_set_before_subcommand(self) -> None:
        r = self.fleet("workspace", "--project", "demo", "set", "none")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.workspace_of("demo"), "none")

    def test_workspace_set_subcommand_project_wins(self) -> None:
        r = self.fleet("workspace", "--project", "demo", "set", "none", "--project", "other")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.workspace_of("other"), "none")
        self.assertEqual(self.workspace_of("demo"), "worktree")

    # -- seed (group-level flag keeps working alongside the shared helper) --

    def test_role_seed_subcommand_project_wins(self) -> None:
        r = self.fleet("role", "--project", "demo", "seed", "driver", "--project", "other")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.states["other"] / "roles" / "driver.md").is_file())
        self.assertFalse((self.states["demo"] / "roles" / "driver.md").exists())


if __name__ == "__main__":
    unittest.main()
