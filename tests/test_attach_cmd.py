"""Tests for ``fleet attach`` — error paths and grouped-session attach flow."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import tmux  # noqa: E402
from fleet.commands.attach import run, _sweep_stale_view_sessions  # noqa: E402
from tests._fleet_test_helpers import run_fleet, make_project  # noqa: E402


class AttachCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.project_name = "fleet-test-" + os.urandom(3).hex()
        self.state_dir = make_project(self.fleet_home, self.project_name, self.project)
        self.session = f"fleet-{self.project_name}"

    def tearDown(self) -> None:
        if shutil.which("tmux") and tmux.session_exists(self.session):
            tmux.kill_session(self.session)
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_no_project_found(self) -> None:
        r = run_fleet("attach", "--project", "nonexistent",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no registered project", r.stderr)

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_session_not_running(self) -> None:
        r = run_fleet("attach", "--project", self.project_name,
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("session not running", r.stderr)

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_unknown_window(self) -> None:
        tmux.new_session(self.session)
        try:
            r = run_fleet("attach", "1", "--project", self.project_name,
                          fleet_home=self.fleet_home)
            self.assertEqual(r.returncode, 1)
            self.assertIn("window not found", r.stderr)
        finally:
            tmux.kill_session(self.session)


class AttachGroupedSessionTests(unittest.TestCase):
    """grouped session 経由の attach フローをモックで検証する。"""

    def _make_args(self, target: str = "leader", project: str = "testproj") -> MagicMock:
        args = MagicMock()
        args.target = target
        args.project = project
        return args

    def _ok(self) -> MagicMock:
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        return r

    def _fail(self, stderr: str = "err") -> MagicMock:
        r = MagicMock()
        r.returncode = 1
        r.stderr = stderr
        return r

    @patch("fleet.commands.attach.os.execvp")
    @patch("fleet.commands.attach.subprocess.run")
    @patch("fleet.commands.attach.tmux_mod.list_windows", return_value=["leader"])
    @patch("fleet.commands.attach.tmux_mod.session_exists", return_value=True)
    @patch("fleet.commands.attach.tmux_mod.available", return_value=True)
    @patch("fleet.commands.attach.state_mod.load_project", return_value={"name": "testproj"})
    @patch("fleet.commands.attach.state_mod.resolve_state_dir")
    def test_grouped_session_flow(
        self,
        mock_resolve,
        mock_load,
        mock_avail,
        mock_exists,
        mock_windows,
        mock_subproc,
        mock_execvp,
    ) -> None:
        mock_resolve.return_value = Path("/fake/state")
        mock_subproc.return_value = self._ok()
        mock_execvp.side_effect = SystemExit(0)  # execvp でプロセス置換をシミュレート

        args = self._make_args(target="leader")
        with self.assertRaises(SystemExit):
            run(args)

        calls = mock_subproc.call_args_list
        # 1) new-session (grouped)
        self.assertTrue(
            any("new-session" in str(c) and "-t" in str(c) for c in calls),
            f"new-session not called: {calls}",
        )
        # 2) set-option destroy-unattached は呼ばれない (PR #77 regression 修正)
        self.assertFalse(
            any("destroy-unattached" in str(c) for c in calls),
            f"destroy-unattached must not be set: {calls}",
        )
        # 3) select-window
        self.assertTrue(
            any("select-window" in str(c) for c in calls),
            f"select-window not called: {calls}",
        )
        # 4) execvp で view session に attach
        execvp_args = mock_execvp.call_args[0]
        self.assertEqual(execvp_args[0], "tmux")
        tmux_cmd = execvp_args[1]
        self.assertIn("attach", tmux_cmd)
        # view session 名は "<session>-view-<pid>" の形式
        view_name = tmux_cmd[-1]
        self.assertIn("-view-", view_name)
        self.assertNotIn("fleet-testproj:", view_name)

    @patch("fleet.commands.attach.os.execvp")
    @patch("fleet.commands.attach.subprocess.run")
    @patch("fleet.commands.attach.tmux_mod.list_windows", return_value=["leader", "42·implementer"])
    @patch("fleet.commands.attach.tmux_mod.session_exists", return_value=True)
    @patch("fleet.commands.attach.tmux_mod.available", return_value=True)
    @patch("fleet.commands.attach.state_mod.load_project", return_value={"name": "testproj"})
    @patch("fleet.commands.attach.state_mod.resolve_state_dir")
    def test_task_window_flow(
        self,
        mock_resolve,
        mock_load,
        mock_avail,
        mock_exists,
        mock_windows,
        mock_subproc,
        mock_execvp,
    ) -> None:
        mock_resolve.return_value = Path("/fake/state")
        mock_subproc.return_value = self._ok()
        mock_execvp.side_effect = SystemExit(0)

        args = self._make_args(target="42")
        with self.assertRaises(SystemExit):
            run(args)

        # select-window が task id に対応する role 付き window を対象にしていることを確認
        calls = mock_subproc.call_args_list
        select_calls = [c for c in calls if "select-window" in str(c)]
        self.assertTrue(len(select_calls) > 0)
        self.assertIn("42·implementer", str(select_calls[0]))

    @patch("fleet.commands.attach.subprocess.run")
    @patch("fleet.commands.attach.tmux_mod.list_windows", return_value=["leader", "42·designer", "42·implementer"])
    @patch("fleet.commands.attach.tmux_mod.session_exists", return_value=True)
    @patch("fleet.commands.attach.tmux_mod.available", return_value=True)
    @patch("fleet.commands.attach.state_mod.load_project", return_value={"name": "testproj"})
    @patch("fleet.commands.attach.state_mod.resolve_state_dir")
    def test_task_window_ambiguous_returns_error(
        self,
        mock_resolve,
        mock_load,
        mock_avail,
        mock_exists,
        mock_windows,
        mock_subproc,
    ) -> None:
        mock_resolve.return_value = Path("/fake/state")
        mock_subproc.return_value = self._ok()

        result = run(self._make_args(target="42"))

        self.assertEqual(result, 1)
        self.assertFalse(any("select-window" in str(c) for c in mock_subproc.call_args_list))

    @patch("fleet.commands.attach.subprocess.run")
    @patch("fleet.commands.attach.tmux_mod.list_windows", return_value=["leader"])
    @patch("fleet.commands.attach.tmux_mod.session_exists", return_value=True)
    @patch("fleet.commands.attach.tmux_mod.available", return_value=True)
    @patch("fleet.commands.attach.state_mod.load_project", return_value={"name": "testproj"})
    @patch("fleet.commands.attach.state_mod.resolve_state_dir")
    def test_new_session_failure_returns_error(
        self,
        mock_resolve,
        mock_load,
        mock_avail,
        mock_exists,
        mock_windows,
        mock_subproc,
    ) -> None:
        mock_resolve.return_value = Path("/fake/state")
        # 1回目も2回目も失敗 (pid + retry も失敗)
        mock_subproc.return_value = self._fail("duplicate session")

        args = self._make_args(target="leader")
        result = run(args)
        self.assertEqual(result, 1)

    @patch("fleet.commands.attach.subprocess.run")
    @patch("fleet.commands.attach.tmux_mod.list_windows", return_value=["leader"])
    @patch("fleet.commands.attach.tmux_mod.session_exists", return_value=True)
    @patch("fleet.commands.attach.tmux_mod.available", return_value=True)
    @patch("fleet.commands.attach.state_mod.load_project", return_value={"name": "testproj"})
    @patch("fleet.commands.attach.state_mod.resolve_state_dir")
    def test_select_window_failure_returns_error(
        self,
        mock_resolve,
        mock_load,
        mock_avail,
        mock_exists,
        mock_windows,
        mock_subproc,
    ) -> None:
        mock_resolve.return_value = Path("/fake/state")
        ok = self._ok()
        fail = self._fail("select-window failed")
        # list-sessions (sweep) 成功, new-session 成功, select-window 失敗, kill-session (cleanup)
        mock_subproc.side_effect = [ok, ok, fail, ok]

        args = self._make_args(target="leader")
        result = run(args)
        self.assertEqual(result, 1)


class SweepStaleViewSessionTests(unittest.TestCase):
    """_sweep_stale_view_sessions の動作をモックで検証する。"""

    @patch("fleet.commands.attach.subprocess.run")
    def test_kills_unattached_view_sessions(self, mock_subproc: MagicMock) -> None:
        list_ok = MagicMock()
        list_ok.returncode = 0
        list_ok.stdout = (
            "fleet-proj-view-1234 0\n"   # stale (attached=0) → kill 対象
            "fleet-proj-view-5678 1\n"   # attached → kill しない
            "fleet-proj 1\n"              # view session でない → 無視
        )
        kill_ok = MagicMock()
        kill_ok.returncode = 0

        mock_subproc.side_effect = [list_ok, kill_ok]

        _sweep_stale_view_sessions("fleet-proj")

        calls = mock_subproc.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn("list-sessions", str(calls[0]))
        self.assertIn("kill-session", str(calls[1]))
        self.assertIn("fleet-proj-view-1234", str(calls[1]))
        self.assertNotIn("fleet-proj-view-5678", str(calls[1]))

    @patch("fleet.commands.attach.subprocess.run")
    def test_no_stale_sessions_no_kill(self, mock_subproc: MagicMock) -> None:
        list_ok = MagicMock()
        list_ok.returncode = 0
        list_ok.stdout = "fleet-proj 1\n"

        mock_subproc.return_value = list_ok

        _sweep_stale_view_sessions("fleet-proj")

        calls = mock_subproc.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertIn("list-sessions", str(calls[0]))

    @patch("fleet.commands.attach.subprocess.run")
    def test_list_sessions_failure_is_ignored(self, mock_subproc: MagicMock) -> None:
        fail = MagicMock()
        fail.returncode = 1
        mock_subproc.return_value = fail

        # 例外が発生しないことを確認
        _sweep_stale_view_sessions("fleet-proj")

    @patch("fleet.commands.attach.subprocess.run")
    def test_sweep_called_before_grouped_session_creation(self, mock_subproc: MagicMock) -> None:
        """run() 内で sweep が new-session より前に呼ばれることを確認する。"""
        list_ok = MagicMock()
        list_ok.returncode = 0
        list_ok.stdout = ""
        new_session_ok = MagicMock()
        new_session_ok.returncode = 0
        select_ok = MagicMock()
        select_ok.returncode = 0
        mock_subproc.side_effect = [list_ok, new_session_ok, select_ok]

        args = MagicMock()
        args.target = "leader"
        args.project = "testproj"

        with (
            patch("fleet.commands.attach.state_mod.resolve_state_dir", return_value=Path("/fake")),
            patch("fleet.commands.attach.state_mod.load_project", return_value={"name": "testproj"}),
            patch("fleet.commands.attach.tmux_mod.available", return_value=True),
            patch("fleet.commands.attach.tmux_mod.session_exists", return_value=True),
            patch("fleet.commands.attach.tmux_mod.list_windows", return_value=["leader"]),
            patch("fleet.commands.attach.os.execvp", side_effect=SystemExit(0)),
        ):
            with self.assertRaises(SystemExit):
                run(args)

        calls = mock_subproc.call_args_list
        # 1番目: list-sessions (sweep), 2番目: new-session
        self.assertIn("list-sessions", str(calls[0]))
        self.assertIn("new-session", str(calls[1]))


if __name__ == "__main__":
    unittest.main()
