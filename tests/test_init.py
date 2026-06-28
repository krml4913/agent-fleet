"""Tests for ``fleet init`` — stdlib-only (unittest) so they run anywhere."""
from __future__ import annotations

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
        proj2 = Path(self._tmp.name) / "proj2"
        proj2.mkdir()
        result = run_fleet("init", "--name", "demo", str(proj2),
                           fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("already registered", result.stderr)

    def test_rejects_duplicate_repo(self) -> None:
        run_fleet("init", "--name", "demo", str(self.project),
                  fleet_home=self.fleet_home)
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

    def test_creates_formations_and_roles_dirs(self) -> None:
        result = run_fleet("init", "--name", "demo", "--no-formation", str(self.project),
                           fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_dir = self.fleet_home / "projects" / "demo"
        self.assertTrue((state_dir / "formations").is_dir())
        self.assertTrue((state_dir / "roles").is_dir())

    def test_no_formation_flag_empty_formations(self) -> None:
        result = run_fleet("init", "--name", "demo", "--no-formation", str(self.project),
                           fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_dir = self.fleet_home / "projects" / "demo"
        formations = list((state_dir / "formations").glob("*.yaml"))
        self.assertEqual(formations, [])
        self.assertIn("(none)", result.stdout)

    def test_formation_solo_copies_yaml(self) -> None:
        result = run_fleet("init", "--name", "demo", "--formation", "solo", str(self.project),
                           fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_dir = self.fleet_home / "projects" / "demo"
        self.assertTrue((state_dir / "formations" / "solo.yaml").is_file())
        self.assertIn("solo", result.stdout)

    def test_formation_comma_separated(self) -> None:
        result = run_fleet("init", "--name", "demo", "--formation", "solo,pair_review",
                           str(self.project), fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_dir = self.fleet_home / "projects" / "demo"
        self.assertTrue((state_dir / "formations" / "solo.yaml").is_file())
        self.assertTrue((state_dir / "formations" / "pair_review.yaml").is_file())

    def test_formation_repeated_flag(self) -> None:
        result = run_fleet("init", "--name", "demo",
                           "--formation", "solo", "--formation", "multi_stage",
                           str(self.project), fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_dir = self.fleet_home / "projects" / "demo"
        self.assertTrue((state_dir / "formations" / "solo.yaml").is_file())
        self.assertTrue((state_dir / "formations" / "multi_stage.yaml").is_file())

    def test_formation_deduplication(self) -> None:
        result = run_fleet("init", "--name", "demo",
                           "--formation", "solo,solo",
                           str(self.project), fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_dir = self.fleet_home / "projects" / "demo"
        yamls = list((state_dir / "formations").glob("solo*.yaml"))
        self.assertEqual(len(yamls), 1)

    def test_unknown_template_name_is_error(self) -> None:
        result = run_fleet("init", "--name", "demo", "--formation", "bogus",
                           str(self.project), fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("bogus", result.stderr)
        self.assertIn("Available", result.stderr)

    def test_formation_and_no_formation_conflict(self) -> None:
        result = run_fleet("init", "--name", "demo",
                           "--formation", "solo", "--no-formation",
                           str(self.project), fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("mutually exclusive", result.stderr)

    def test_non_tty_no_formation_flag_defaults_to_empty(self) -> None:
        # run_fleet uses subprocess.PIPE (non-TTY) — should default to no formations
        result = run_fleet("init", "--name", "demo", str(self.project),
                           fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_dir = self.fleet_home / "projects" / "demo"
        # formations/ must exist and be empty (no template copying)
        self.assertTrue((state_dir / "formations").is_dir())
        yamls = list((state_dir / "formations").glob("*.yaml"))
        self.assertEqual(yamls, [])

    def test_non_interactive_flag_suppresses_picker(self) -> None:
        result = run_fleet("init", "--name", "demo", "--non-interactive",
                           str(self.project), fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 0, result.stderr)
        state_dir = self.fleet_home / "projects" / "demo"
        yamls = list((state_dir / "formations").glob("*.yaml"))
        self.assertEqual(yamls, [])


class FormationListCmdTests(unittest.TestCase):
    """Tests for ``fleet formation list``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        run_fleet("init", "--name", "demo", "--no-formation", str(self.project),
                  fleet_home=self.fleet_home)
        self.state_dir = self.fleet_home / "projects" / "demo"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_formation_init_subcommand_is_removed(self) -> None:
        result = run_fleet("formation", "init", "--from", "solo",
                           fleet_home=self.fleet_home, cwd=self.project)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_formation_list_shows_runtime_provenance_and_seed_sources(self) -> None:
        global_dir = self.fleet_home / "global" / "formations"
        global_dir.mkdir(parents=True)
        (global_dir / "solo.yaml").write_text(
            "name: solo\nstages:\n  - role: driver\n    agent: claude:opus\n"
        )
        (self.state_dir / "formations" / "solo.yaml").write_text(
            "name: solo\nstages:\n  - role: driver\n    agent: codex:gpt-5.5\n"
        )

        result = run_fleet("formation", "list",
                           fleet_home=self.fleet_home, cwd=self.project)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("solo [project:demo] (wins)", result.stdout)
        self.assertIn("solo [global] (shadowed)", result.stdout)
        self.assertIn("solo [seed:template]", result.stdout)
        self.assertIn("pair_review [seed:template]", result.stdout)
        self.assertNotIn("[template] (wins)", result.stdout)
        self.assertNotIn("[template] (shadowed)", result.stdout)


if __name__ == "__main__":
    unittest.main()
