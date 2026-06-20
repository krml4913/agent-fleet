"""Tests for ``fleet dashboard`` CLI command."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import html_dashboard  # noqa: E402
from tests._fleet_test_helpers import make_project, run_fleet  # noqa: E402


class DashboardCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        base = Path(self._tmp.name)
        self.fleet_home = base / "fleet-state"
        self.fleet_home.mkdir()
        self.repo = base / "repo"
        self.repo.mkdir()
        self._old = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        make_project(self.fleet_home, "demo", self.repo)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old
        self._tmp.cleanup()

    def test_dashboard_no_open_writes_file(self) -> None:
        result = run_fleet(
            "dashboard", "--no-open", fleet_home=self.fleet_home, cwd=self.repo
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out_path = html_dashboard.global_dashboard_path()
        self.assertTrue(out_path.is_file())

    def test_dashboard_no_open_prints_path(self) -> None:
        result = run_fleet(
            "dashboard", "--no-open", fleet_home=self.fleet_home, cwd=self.repo
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("wrote ", result.stdout)
        self.assertIn("dashboard.html", result.stdout)

    def test_dashboard_no_open_does_not_call_webbrowser(self) -> None:
        import webbrowser
        opened: list[str] = []

        def _mock(uri: str, *a, **kw) -> bool:
            opened.append(uri)
            return True

        with patch.object(webbrowser, "open", _mock):
            result = run_fleet(
                "dashboard", "--no-open", fleet_home=self.fleet_home, cwd=self.repo
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(opened, [], "webbrowser.open should NOT be called with --no-open")

    def test_dashboard_without_no_open_calls_webbrowser(self) -> None:
        from fleet.commands import dashboard as dashboard_cmd

        opened: list[str] = []

        def _mock(uri: str, *a, **kw) -> bool:
            opened.append(uri)
            return True

        import webbrowser
        import argparse

        args = argparse.Namespace(no_open=False)
        with patch.object(webbrowser, "open", _mock):
            rc = dashboard_cmd.run(args)
        self.assertEqual(rc, 0)
        self.assertEqual(len(opened), 1)
        self.assertIn("dashboard.html", opened[0])
        self.assertTrue(opened[0].startswith("file://"))
