"""Tests for ``fleet scope`` command (Issue #172)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from tests._fleet_test_helpers import run_fleet, make_project  # noqa: E402
from fleet import state  # noqa: E402


class ScopeCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "alpha", self.project)
        self.beta = Path(self._tmp.name) / "beta"
        self.beta.mkdir()
        make_project(self.fleet_home, "beta", self.beta)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def _run(self, *args: str, env_extra: dict | None = None):
        return run_fleet(*args, fleet_home=self.fleet_home, env_extra=env_extra)

    # --- display ---

    def test_display_unscoped(self) -> None:
        result = self._run("scope", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unscoped", result.stdout)

    def test_display_with_fleet_session(self) -> None:
        result = self._run("scope", env_extra={"FLEET_SESSION": "main"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("main", result.stdout)

    def test_display_defaults_to_main_when_no_env(self) -> None:
        result = self._run("scope")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("main", result.stdout)

    # --- mutations ---

    def test_set(self) -> None:
        result = self._run("scope", "main", "--set", "alpha,beta")
        self.assertEqual(result.returncode, 0, result.stderr)
        scope = state.session_scope("main")
        self.assertIsNotNone(scope)
        self.assertIn("alpha", scope)
        self.assertIn("beta", scope)

    def test_add(self) -> None:
        self._run("scope", "main", "--set", "alpha")
        result = self._run("scope", "main", "--add", "beta")
        self.assertEqual(result.returncode, 0, result.stderr)
        scope = state.session_scope("main")
        self.assertIn("alpha", scope)
        self.assertIn("beta", scope)

    def test_rm(self) -> None:
        self._run("scope", "main", "--set", "alpha,beta")
        result = self._run("scope", "main", "--rm", "alpha")
        self.assertEqual(result.returncode, 0, result.stderr)
        scope = state.session_scope("main")
        self.assertNotIn("alpha", scope)
        self.assertIn("beta", scope)

    def test_clear(self) -> None:
        self._run("scope", "main", "--set", "alpha")
        result = self._run("scope", "main", "--clear")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(state.session_scope("main"))

    # --- validation ---

    def test_unknown_name_rejected(self) -> None:
        result = self._run("scope", "main", "--set", "no-such-project")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown", result.stderr)

    # --- record-less session dir ---

    def test_set_creates_record_for_recordless_session(self) -> None:
        """set_session_scope should create a minimal record when no session.json exists."""
        result = self._run("scope", "orphan", "--set", "alpha")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(state.session_record_path("orphan").exists())
        scope = state.session_scope("orphan")
        self.assertIn("alpha", scope)

    def test_fleet_session_env_resolves_label(self) -> None:
        result = self._run("scope", "--set", "alpha", env_extra={"FLEET_SESSION": "main"})
        self.assertEqual(result.returncode, 0, result.stderr)
        scope = state.session_scope("main")
        self.assertIn("alpha", scope)


if __name__ == "__main__":
    unittest.main()
