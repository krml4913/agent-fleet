"""Tests for ``fleet attach`` — error paths, target resolution, and the tmux
backend's grouped-session attach flow (Issue #76)."""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet.commands.attach import run  # noqa: E402
from fleet.mux import MuxError  # noqa: E402
from fleet.mux.tmux import TmuxError, TmuxMux, _sweep_stale_view_sessions  # noqa: E402
from tests._fake_mux import use_fake_mux  # noqa: E402
from tests._fleet_test_helpers import run_fleet, make_project, requires_live_tmux  # noqa: E402


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
        self.tmux = TmuxMux()

    def tearDown(self) -> None:
        if shutil.which("tmux") and self.tmux.session_exists(self.session):
            self.tmux.kill_session(self.session)
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

    @requires_live_tmux
    def test_session_not_running(self) -> None:
        r = run_fleet("attach", "--project", self.project_name,
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("session not running", r.stderr)

    @requires_live_tmux
    def test_unknown_window(self) -> None:
        self.tmux.new_session(self.session, window="leader")
        try:
            r = run_fleet("attach", "1", "--project", self.project_name,
                          fleet_home=self.fleet_home)
            self.assertEqual(r.returncode, 1)
            self.assertIn("window not found", r.stderr)
        finally:
            self.tmux.kill_session(self.session)


class AttachTargetResolutionTests(unittest.TestCase):
    """``fleet attach`` resolves the window, then hands off to ``Mux.attach``."""

    def _make_args(self, target: str = "leader", project: str = "testproj") -> MagicMock:
        args = MagicMock()
        args.target = target
        args.project = project
        return args

    def _run(self, target: str, windows: list[str] | None, **fake_kw):
        sessions = {} if windows is None else {"fleet-testproj": windows}
        err = io.StringIO()
        with (
            patch("fleet.commands.attach.state_mod.resolve_state_dir", return_value=Path("/fake")),
            patch("fleet.commands.attach.state_mod.load_project", return_value={"name": "testproj"}),
            use_fake_mux(sessions=sessions, **fake_kw) as fake,
            contextlib.redirect_stderr(err),
        ):
            rc = run(self._make_args(target=target))
        return rc, fake, err.getvalue()

    def test_leader_attaches_to_leader_window(self) -> None:
        rc, fake, _err = self._run("leader", ["leader"])
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_named("attach"), [(("fleet-testproj", "leader"), {})])

    def test_task_id_resolves_role_tagged_window(self) -> None:
        rc, fake, _err = self._run("42", ["leader", "42·implementer", "420·driver"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            fake.calls_named("attach"), [(("fleet-testproj", "42·implementer"), {})]
        )

    def test_ambiguous_task_window_returns_error(self) -> None:
        rc, fake, err = self._run("42", ["leader", "42·designer", "42·implementer"])
        self.assertEqual(rc, 1)
        self.assertIn("multiple windows found", err)
        self.assertEqual(fake.calls_named("attach"), [])

    def test_unknown_window_returns_error(self) -> None:
        rc, fake, err = self._run("7", ["leader"])
        self.assertEqual(rc, 1)
        self.assertIn("window not found", err)
        self.assertEqual(fake.calls_named("attach"), [])

    def test_session_not_running(self) -> None:
        rc, fake, err = self._run("leader", None)
        self.assertEqual(rc, 1)
        self.assertIn("tmux session not running: fleet-testproj", err)

    def test_backend_unavailable(self) -> None:
        rc, _fake, err = self._run("leader", ["leader"], available=False)
        self.assertEqual(rc, 1)
        self.assertIn("tmux not on PATH", err)

    def test_attach_failure_returns_error(self) -> None:
        sessions = {"fleet-testproj": ["leader"]}
        err = io.StringIO()
        with (
            patch("fleet.commands.attach.state_mod.resolve_state_dir", return_value=Path("/fake")),
            patch("fleet.commands.attach.state_mod.load_project", return_value={"name": "testproj"}),
            use_fake_mux(sessions=sessions) as fake,
            contextlib.redirect_stderr(err),
        ):
            fake.fail["attach"] = MuxError("failed to create view session: dup")
            rc = run(self._make_args(target="leader"))
        self.assertEqual(rc, 1)
        self.assertIn("failed to create view session", err.getvalue())

    def test_attach_exit_code_is_returned(self) -> None:
        rc, _fake, _err = self._run("leader", ["leader"], attach_rc=3)
        self.assertEqual(rc, 3)


def _ok() -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stderr = ""
    r.stdout = ""
    return r


def _fail(stderr: str = "err") -> MagicMock:
    r = MagicMock()
    r.returncode = 1
    r.stderr = stderr
    return r


class TmuxGroupedSessionAttachTests(unittest.TestCase):
    """The tmux backend's attach(session, window) via a grouped view session."""

    def setUp(self) -> None:
        p = patch("fleet.mux.tmux.os.name", "posix")
        p.start()
        self.addCleanup(p.stop)

    @patch("fleet.mux.tmux.os.execvp")
    @patch("fleet.mux.tmux.subprocess.run")
    def test_grouped_session_flow(self, mock_subproc, mock_execvp) -> None:
        mock_subproc.return_value = _ok()
        mock_execvp.side_effect = SystemExit(0)  # simulate process replacement by execvp

        with self.assertRaises(SystemExit):
            TmuxMux().attach("fleet-testproj", "leader")

        calls = mock_subproc.call_args_list
        # 1) new-session (grouped)
        self.assertTrue(
            any("new-session" in str(c) and "-t" in str(c) for c in calls),
            f"new-session not called: {calls}",
        )
        # 2) set-option destroy-unattached is not called (PR #77 regression fix)
        self.assertFalse(
            any("destroy-unattached" in str(c) for c in calls),
            f"destroy-unattached must not be set: {calls}",
        )
        # 3) select-window
        self.assertTrue(
            any("select-window" in str(c) for c in calls),
            f"select-window not called: {calls}",
        )
        # 4) attach to the view session via execvp
        execvp_args = mock_execvp.call_args[0]
        self.assertEqual(execvp_args[0], "tmux")
        tmux_cmd = execvp_args[1]
        self.assertIn("attach", tmux_cmd)
        # the view session name has the form "<session>-view-<pid>"
        view_name = tmux_cmd[-1]
        self.assertIn("-view-", view_name)
        self.assertNotIn("fleet-testproj:", view_name)

    @patch("fleet.mux.tmux.os.execvp")
    @patch("fleet.mux.tmux.subprocess.run")
    def test_task_window_flow(self, mock_subproc, mock_execvp) -> None:
        mock_subproc.return_value = _ok()
        mock_execvp.side_effect = SystemExit(0)

        with self.assertRaises(SystemExit):
            TmuxMux().attach("fleet-testproj", "42·implementer")

        select_calls = [c for c in mock_subproc.call_args_list if "select-window" in str(c)]
        self.assertTrue(len(select_calls) > 0)
        self.assertIn("42·implementer", str(select_calls[0]))

    @patch("fleet.mux.tmux.os.execvp")
    @patch("fleet.mux.tmux.subprocess.run")
    def test_new_session_failure_raises(self, mock_subproc, mock_execvp) -> None:
        # both the first and second attempts fail (pid + retry both fail)
        mock_subproc.return_value = _fail("duplicate session")

        with self.assertRaises(TmuxError) as cm:
            TmuxMux().attach("fleet-testproj", "leader")
        self.assertIn("failed to create view session", str(cm.exception))
        mock_execvp.assert_not_called()

    @patch("fleet.mux.tmux.os.execvp")
    @patch("fleet.mux.tmux.subprocess.run")
    def test_select_window_failure_raises_and_kills_view(self, mock_subproc, mock_execvp) -> None:
        # list-sessions (sweep) ok, new-session ok, select-window fails, kill-session (cleanup)
        mock_subproc.side_effect = [_ok(), _ok(), _fail("select-window failed"), _ok()]

        with self.assertRaises(TmuxError) as cm:
            TmuxMux().attach("fleet-testproj", "leader")
        self.assertIn("failed to select window", str(cm.exception))
        self.assertIn("kill-session", str(mock_subproc.call_args_list[-1]))
        mock_execvp.assert_not_called()

    @patch("fleet.mux.tmux.subprocess.run")
    def test_sweep_called_before_grouped_session_creation(self, mock_subproc: MagicMock) -> None:
        """Confirm sweep runs before new-session inside attach()."""
        list_ok = _ok()
        mock_subproc.side_effect = [list_ok, _ok(), _ok()]

        with patch("fleet.mux.tmux.os.execvp", side_effect=SystemExit(0)):
            with self.assertRaises(SystemExit):
                TmuxMux().attach("fleet-testproj", "leader")

        calls = mock_subproc.call_args_list
        # 1st: list-sessions (sweep), 2nd: new-session
        self.assertIn("list-sessions", str(calls[0]))
        self.assertIn("new-session", str(calls[1]))

    @patch("fleet.mux.tmux.subprocess.call", return_value=5)
    @patch("fleet.mux.tmux.os.execvp")
    def test_windows_uses_subprocess_and_returns_exit_code(self, mock_execvp, mock_call) -> None:
        with patch("fleet.mux.tmux.os.name", "nt"):
            rc = TmuxMux().attach("fleet-testproj")
        self.assertEqual(rc, 5)
        mock_call.assert_called_once_with(["tmux", "attach", "-t", "fleet-testproj"])
        mock_execvp.assert_not_called()


class SweepStaleViewSessionTests(unittest.TestCase):
    """Verify _sweep_stale_view_sessions behavior with mocks."""

    @patch("fleet.mux.tmux.subprocess.run")
    def test_kills_unattached_view_sessions(self, mock_subproc: MagicMock) -> None:
        list_ok = MagicMock()
        list_ok.returncode = 0
        list_ok.stdout = (
            "fleet-proj-view-1234 0\n"   # stale (attached=0) → kill target
            "fleet-proj-view-5678 1\n"   # attached → do not kill
            "fleet-proj 1\n"              # not a view session → ignore
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

    @patch("fleet.mux.tmux.subprocess.run")
    def test_no_stale_sessions_no_kill(self, mock_subproc: MagicMock) -> None:
        list_ok = MagicMock()
        list_ok.returncode = 0
        list_ok.stdout = "fleet-proj 1\n"

        mock_subproc.return_value = list_ok

        _sweep_stale_view_sessions("fleet-proj")

        calls = mock_subproc.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertIn("list-sessions", str(calls[0]))

    @patch("fleet.mux.tmux.subprocess.run")
    def test_list_sessions_failure_is_ignored(self, mock_subproc: MagicMock) -> None:
        fail = MagicMock()
        fail.returncode = 1
        mock_subproc.return_value = fail

        # confirm no exception is raised
        _sweep_stale_view_sessions("fleet-proj")


if __name__ == "__main__":
    unittest.main()
