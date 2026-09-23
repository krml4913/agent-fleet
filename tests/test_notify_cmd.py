"""Tests for ``fleet notify`` CLI (opt-in leader-pane push toggle, and #317's
Windows setup-windows / teardown-windows)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from fleet import windows_notify_setup as wns  # noqa: E402
from fleet.commands import done as done_cmd  # noqa: E402
from fleet.commands import notify as notify_cmd  # noqa: E402
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


class NotifySetupWindowsCmdTests(unittest.TestCase):
    """``fleet notify setup-windows`` / ``teardown-windows`` (#317): CLI plumbing only.

    The registry layer itself is covered hermetically by
    ``tests/test_windows_notify_setup.py``; here we only check the command
    dispatches correctly and never needs --project.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_setup_windows_off_windows_fails_without_touching_registry(self) -> None:
        with mock.patch.object(wns, "is_windows", return_value=False), \
             mock.patch.object(wns, "setup") as fake_setup:
            r = run_fleet("notify", "setup-windows", fleet_home=self.fleet_home)
        fake_setup.assert_not_called()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("only available on Windows", r.stderr)

    def test_teardown_windows_off_windows_fails_without_touching_registry(self) -> None:
        with mock.patch.object(wns, "is_windows", return_value=False), \
             mock.patch.object(wns, "teardown") as fake_teardown:
            r = run_fleet("notify", "teardown-windows", fleet_home=self.fleet_home)
        fake_teardown.assert_not_called()
        self.assertNotEqual(r.returncode, 0)

    def test_setup_windows_calls_setup_and_reports_result(self) -> None:
        result = wns.SetupResult(
            aumid_created=True,
            protocol_created=False,
            command='"pythonw.exe" "fleet" url-handler "%1"',
        )
        with mock.patch.object(wns, "is_windows", return_value=True), \
             mock.patch.object(wns, "setup", return_value=result) as fake_setup:
            r = run_fleet("notify", "setup-windows", fleet_home=self.fleet_home)
        fake_setup.assert_called_once()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("created", r.stdout)
        self.assertIn("already present", r.stdout)
        self.assertIn("url-handler", r.stdout)

    def test_teardown_windows_calls_teardown_and_reports_result(self) -> None:
        result = wns.TeardownResult(aumid_removed=True, protocol_removed=True)
        with mock.patch.object(wns, "is_windows", return_value=True), \
             mock.patch.object(wns, "teardown", return_value=result) as fake_teardown:
            r = run_fleet("notify", "teardown-windows", fleet_home=self.fleet_home)
        fake_teardown.assert_called_once()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("removed", r.stdout)

    def test_setup_windows_ignores_project_flag(self) -> None:
        # No project needs to be registered at all.
        result = wns.SetupResult(aumid_created=True, protocol_created=True, command="cmd")
        with mock.patch.object(wns, "is_windows", return_value=True), \
             mock.patch.object(wns, "setup", return_value=result):
            r = run_fleet(
                "notify", "--project", "no-such-project", "setup-windows",
                fleet_home=self.fleet_home,
            )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_state_choices_include_windows_setup(self) -> None:
        self.assertIn("setup-windows", notify_cmd.STATES)
        self.assertIn("teardown-windows", notify_cmd.STATES)


if __name__ == "__main__":
    unittest.main()
