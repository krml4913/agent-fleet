"""Tests for ``fleet notify`` CLI (opt-in leader-pane push toggle)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from fleet.commands import done as done_cmd  # noqa: E402
from tests._fleet_test_helpers import run_fleet, make_project  # noqa: E402

KEY = "notify_leader_on_driver_done"


class NotifyCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def _notify(self, *args: str):
        return run_fleet("notify", "--project", "demo", *args, fleet_home=self.fleet_home)

    def test_no_arg_prints_off_by_default_and_writes_nothing(self) -> None:
        before = state.project_path(self.state_dir).read_text(encoding="utf-8")
        r = self._notify()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"{KEY}: off", r.stdout)
        self.assertEqual(
            state.project_path(self.state_dir).read_text(encoding="utf-8"), before
        )

    def test_on_sets_project_yaml_and_is_read_by_done(self) -> None:
        r = self._notify("on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"{KEY}: on", r.stdout)
        project = state.load_project(self.state_dir)
        self.assertEqual(project[KEY], "true")
        self.assertTrue(done_cmd._truthy(project[KEY]))

    def test_off_after_on(self) -> None:
        self._notify("on")
        r = self._notify("off")
        self.assertEqual(r.returncode, 0, r.stderr)
        project = state.load_project(self.state_dir)
        self.assertEqual(project[KEY], "false")
        self.assertFalse(done_cmd._truthy(project[KEY]))

    def test_status_reflects_current_state(self) -> None:
        self._notify("on")
        for args in ((), ("status",)):
            r = self._notify(*args)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(f"{KEY}: on", r.stdout)
        self._notify("off")
        self.assertIn(f"{KEY}: off", self._notify("status").stdout)

    def test_set_preserves_other_project_keys(self) -> None:
        before = state.load_project(self.state_dir)
        self._notify("on")
        after = state.load_project(self.state_dir)
        for k, v in before.items():
            self.assertEqual(after[k], v)

    def test_invalid_value_fails(self) -> None:
        r = self._notify("maybe")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn(KEY, state.load_project(self.state_dir))

    def test_unknown_project_fails(self) -> None:
        r = run_fleet("notify", "--project", "no-such", "on", fleet_home=self.fleet_home)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
