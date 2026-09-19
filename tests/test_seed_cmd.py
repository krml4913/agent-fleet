"""Tests for shipped-seed hints in resolution errors and ``fleet {formation,role} seed``.

Shipped = seed, a miss = hard error (Issues #225 / #227): nothing here may make a
runtime lookup fall back to the shipped files. The hints only *name* the next step.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import tests._fleet_test_helpers  # noqa: E402,F401  (hermetic env: FLEET_NO_NOTIFY / FLEET_NO_MUX)
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import driver_prompt, formation, seeds, state as state_mod  # noqa: E402
from tests._fleet_test_helpers import run_fleet, run_fleet_agent  # noqa: E402


def _posix(text: str) -> str:
    return text.replace(os.sep, "/")


class _UnseededProject(unittest.TestCase):
    """A registered project with empty ``formations/`` / ``roles/`` and no global tier."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.repo = Path(self._tmp.name) / "proj"
        self.repo.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        # Deliberately not tests._fleet_test_helpers.make_project: it seeds the
        # global roles and a solo formation, which is exactly what is absent here.
        self.state_dir = state_mod.project_state_dir("demo")
        state_mod.init_state(self.state_dir, name="demo", repo=self.repo)
        state_mod.register_project("demo", self.repo)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def project_target(self, kind: str, name: str) -> Path:
        return seeds.target_path(kind, name, state_dir=self.state_dir)

    def global_target(self, kind: str, name: str) -> Path:
        return seeds.target_path(kind, name, state_dir=None)


class MissingHintTests(_UnseededProject):
    def test_formation_miss_names_seed_and_both_targets(self) -> None:
        with self.assertRaises(FileNotFoundError) as ctx:
            formation.load_formation("solo", self.state_dir)
        message = str(ctx.exception)
        self.assertIn("no formation named 'solo'", message)
        self.assertIn("shipped seed", message)
        self.assertIn("fleet formation seed solo --project demo", message)
        self.assertIn("fleet formation seed solo --global", message)
        self.assertIn("fleet edit", message)
        self.assertIn(_posix(str(self.project_target("formation", "solo"))), _posix(message))
        self.assertIn(_posix(str(self.global_target("formation", "solo"))), _posix(message))

    def test_role_miss_names_seed_and_both_targets(self) -> None:
        with self.assertRaises(driver_prompt.RoleResolutionError) as ctx:
            driver_prompt.validate_formation_roles(
                {"stages": [{"role": "driver"}]}, self.state_dir
            )
        message = str(ctx.exception)
        self.assertIn("no role named 'driver'", message)
        self.assertIn("fleet role seed driver --project demo", message)
        self.assertIn("fleet role seed driver --global", message)
        self.assertIn(_posix(str(self.project_target("role", "driver"))), _posix(message))
        self.assertIn(_posix(str(self.global_target("role", "driver"))), _posix(message))

    def test_role_miss_without_project_only_offers_global(self) -> None:
        with self.assertRaises(driver_prompt.RoleResolutionError) as ctx:
            driver_prompt.render(
                task_id="1", description="x", formation_name="solo",
                role="driver", agent="claude:sonnet",
            )
        message = str(ctx.exception)
        self.assertIn("fleet role seed driver --global", message)
        self.assertNotIn("--project", message)

    def test_non_shipped_name_gets_no_hint(self) -> None:
        with self.assertRaises(FileNotFoundError) as ctx:
            formation.load_formation("no-such", self.state_dir)
        self.assertNotIn("shipped seed", str(ctx.exception))
        with self.assertRaises(driver_prompt.RoleResolutionError) as ctx2:
            driver_prompt.validate_formation_roles(
                {"stages": [{"role": "no-such"}]}, self.state_dir
            )
        self.assertNotIn("shipped seed", str(ctx2.exception))

    def test_miss_stays_a_hard_error_no_runtime_fallback(self) -> None:
        # The hint must not turn into a fallback to the shipped file.
        with self.assertRaises(FileNotFoundError):
            formation.load_formation("solo", self.state_dir)
        with self.assertRaises(driver_prompt.RoleResolutionError):
            driver_prompt.render(
                task_id="1", description="x", formation_name="solo",
                role="driver", agent="claude:sonnet", state_dir=self.state_dir,
            )
        self.assertFalse(self.project_target("formation", "solo").exists())
        self.assertFalse(self.global_target("role", "driver").exists())

    def test_fleet_agent_start_error_carries_hint(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run", "--formation", "solo",
            "seed-hint", "should fail",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no formation named 'solo'", result.stderr)
        self.assertIn("fleet formation seed solo --project demo", result.stderr)

    def test_fleet_agent_start_role_error_after_formation_seeded(self) -> None:
        r = run_fleet("formation", "seed", "solo", "--project", "demo", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run", "--formation", "solo",
            "seed-hint", "should fail",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no role named 'driver'", result.stderr)
        self.assertIn("fleet role seed driver --project demo", result.stderr)

    def test_seeding_both_makes_start_succeed(self) -> None:
        for args in (
            ("formation", "seed", "solo", "--project", "demo"),
            ("role", "seed", "driver", "--global"),
        ):
            r = run_fleet(*args, fleet_home=self.fleet_home)
            self.assertEqual(r.returncode, 0, r.stderr)
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run", "--formation", "solo",
            "seed-hint", "now works",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class SeedCommandTests(_UnseededProject):
    def test_formation_seed_project_tier_copies_shipped_bytes(self) -> None:
        r = run_fleet("formation", "seed", "solo", "--project", "demo", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        target = self.project_target("formation", "solo")
        self.assertTrue(target.is_file())
        self.assertEqual(
            target.read_text(encoding="utf-8").replace("\r\n", "\n"),
            (seeds.TEMPLATES_DIR / "solo.yaml").read_text(encoding="utf-8").replace("\r\n", "\n"),
        )
        self.assertFalse(self.global_target("formation", "solo").exists())

    def test_formation_seed_global_tier(self) -> None:
        r = run_fleet("formation", "seed", "solo", "--global", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.global_target("formation", "solo").is_file())
        self.assertFalse(self.project_target("formation", "solo").exists())
        self.assertIn("[global]", r.stdout)

    def test_role_seed_project_tier(self) -> None:
        r = run_fleet("role", "seed", "driver", "--project", "demo", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        target = self.project_target("role", "driver")
        self.assertTrue(target.is_file())
        self.assertEqual(
            target.read_text(encoding="utf-8").replace("\r\n", "\n"),
            (seeds.SHIPPED_ROLES_DIR / "driver.md").read_text(encoding="utf-8").replace("\r\n", "\n"),
        )

    def test_role_seed_global_tier(self) -> None:
        r = run_fleet("role", "seed", "driver", "--global", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.global_target("role", "driver").is_file())

    def test_group_level_project_flag_also_works(self) -> None:
        r = run_fleet("role", "--project", "demo", "seed", "driver", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.project_target("role", "driver").is_file())

    def test_refuses_to_overwrite_without_force(self) -> None:
        target = self.project_target("formation", "solo")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("name: solo\n# customized\n", encoding="utf-8")
        r = run_fleet("formation", "seed", "solo", "--project", "demo", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("already exists", r.stderr)
        self.assertIn("--force", r.stderr)
        self.assertIn("# customized", target.read_text(encoding="utf-8"))

    def test_force_overwrites(self) -> None:
        target = self.project_target("role", "driver")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("customized\n", encoding="utf-8")
        r = run_fleet("role", "seed", "driver", "--project", "demo", "--force", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotEqual(target.read_text(encoding="utf-8"), "customized\n")

    def test_unknown_seed_lists_shipped_names(self) -> None:
        r = run_fleet("formation", "seed", "no-such", "--global", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no shipped formation seed named 'no-such'", r.stderr)
        self.assertIn("solo", r.stderr)
        self.assertFalse(self.global_target("formation", "no-such").exists())

    def test_path_traversal_name_rejected(self) -> None:
        r = run_fleet("role", "seed", "../evil", "--global", fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid role name", r.stderr)

    def test_unresolved_project_suggests_flags(self) -> None:
        r = run_fleet(
            "role", "seed", "driver", fleet_home=self.fleet_home, cwd=Path(self._tmp.name),
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("--global", r.stderr)


if __name__ == "__main__":
    unittest.main()
