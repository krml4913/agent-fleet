"""Tests for ``fleet attach`` — error paths, target resolution, and the tmux
backend's grouped-session attach flow (Issue #76)."""
from __future__ import annotations

import contextlib
import io
import json
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

from fleet import state as state_mod  # noqa: E402
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
        self.label = "attach-" + os.urandom(3).hex()
        self.session = f"fleet-{self.label}"
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
        r = run_fleet("attach", "1", "--project", "nonexistent",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no registered project", r.stderr)

    def test_task_not_found(self) -> None:
        r = run_fleet("attach", "no-such-task", "--project", self.project_name,
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("task not found: task-no-such-task", r.stderr)

    @requires_live_tmux
    def test_session_not_running(self) -> None:
        r = run_fleet("attach", "--session", self.label,
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("session not running", r.stderr)
        self.assertIn(f"fleet leader --name {self.label}", r.stderr)

    @requires_live_tmux
    def test_unknown_window(self) -> None:
        state_mod.save_task(
            self.state_dir, "1", {"id": "1", "status": "running", "owner_session": self.label}
        )
        self.tmux.new_session(self.session, window="leader")
        try:
            r = run_fleet("attach", "1", "--project", self.project_name,
                          fleet_home=self.fleet_home)
            self.assertEqual(r.returncode, 1)
            self.assertIn("window not found", r.stderr)
        finally:
            self.tmux.kill_session(self.session)


class AttachTargetResolutionTests(unittest.TestCase):
    """``fleet attach`` resolves the session + window, then hands off to ``Mux.attach``.

    Sessions are ``fleet-<label>`` (Issue #166): a task attaches to its
    ``owner_session``; the ``leader`` target uses ``--session`` / ``FLEET_SESSION``
    / ``main``. Everything runs against a fake mux — no real multiplexer session.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        repo = Path(self._tmp.name) / "proj"
        repo.mkdir()
        patcher = patch.dict(os.environ, {"FLEET_HOME": str(self.fleet_home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("FLEET_SESSION", None)
        os.environ.pop("FLEET_STATE_DIR", None)
        self.project_name = "testproj"
        self.state_dir = make_project(self.fleet_home, self.project_name, repo)

    def _add_task(self, task_id: str, owner_session: str | None = None, **extra) -> None:
        task = {"id": task_id, "status": "running", **extra}
        if owner_session is not None:
            task["owner_session"] = owner_session
        state_mod.save_task(self.state_dir, task_id, task)

    def _make_args(self, target: str = "leader", session: str | None = None) -> MagicMock:
        args = MagicMock()
        args.target = target
        args.project = self.project_name
        args.session = session
        return args

    def _run(self, target: str, sessions: dict[str, list[str]] | None,
             session: str | None = None, **fake_kw):
        err = io.StringIO()
        with (
            use_fake_mux(sessions=sessions or {}, **fake_kw) as fake,
            contextlib.redirect_stderr(err),
        ):
            rc = run(self._make_args(target=target, session=session))
        return rc, fake, err.getvalue()

    def _write_session_record(self, label: str) -> None:
        rec = state_mod.session_record_path(label)
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(json.dumps({"label": label, "agent": "claude:opus"}), encoding="utf-8")

    def test_leader_defaults_to_main_session(self) -> None:
        rc, fake, _err = self._run("leader", {"fleet-main": ["leader"]})
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_named("attach"), [(("fleet-main", "leader"), {})])

    def test_leader_uses_session_flag(self) -> None:
        rc, fake, _err = self._run(
            "leader", {"fleet-main": ["leader"], "fleet-alt": ["leader"]}, session="alt"
        )
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_named("attach"), [(("fleet-alt", "leader"), {})])

    def test_leader_uses_fleet_session_env(self) -> None:
        with patch.dict(os.environ, {"FLEET_SESSION": "envlabel"}):
            rc, fake, _err = self._run(
                "leader", {"fleet-main": ["leader"], "fleet-envlabel": ["leader"]}
            )
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_named("attach"), [(("fleet-envlabel", "leader"), {})])

    def test_session_flag_beats_fleet_session_env(self) -> None:
        with patch.dict(os.environ, {"FLEET_SESSION": "envlabel"}):
            rc, fake, _err = self._run(
                "leader",
                {"fleet-envlabel": ["leader"], "fleet-alt": ["leader"]},
                session="alt",
            )
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_named("attach"), [(("fleet-alt", "leader"), {})])

    def test_leader_needs_no_project(self) -> None:
        args = self._make_args(target="leader")
        args.project = "nonexistent"
        with use_fake_mux(sessions={"fleet-main": ["leader"]}) as fake:
            rc = run(args)
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_named("attach"), [(("fleet-main", "leader"), {})])

    def test_task_id_resolves_role_tagged_window(self) -> None:
        self._add_task("42", "main")
        rc, fake, _err = self._run(
            "42", {"fleet-main": ["leader", "42·implementer", "420·driver"]}
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            fake.calls_named("attach"), [(("fleet-main", "42·implementer"), {})]
        )

    def test_task_without_owner_session_defaults_to_main(self) -> None:
        self._add_task("42")
        rc, fake, _err = self._run("42", {"fleet-main": ["leader", "42·driver"]})
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_named("attach"), [(("fleet-main", "42·driver"), {})])

    def test_task_attaches_to_its_owner_session_not_project_session(self) -> None:
        self._add_task("7", "alt")
        rc, fake, _err = self._run(
            "7",
            {
                f"fleet-{self.project_name}": ["leader", "7·driver"],
                "fleet-main": ["leader", "7·driver"],
                "fleet-alt": ["leader", "7·driver"],
            },
        )
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_named("attach"), [(("fleet-alt", "7·driver"), {})])

    def test_task_ignores_session_flag(self) -> None:
        self._add_task("7", "alt")
        rc, fake, _err = self._run(
            "7",
            {"fleet-main": ["leader", "7·driver"], "fleet-alt": ["leader", "7·driver"]},
            session="main",
        )
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_named("attach"), [(("fleet-alt", "7·driver"), {})])

    def test_unknown_task_returns_error(self) -> None:
        rc, fake, err = self._run("99", {"fleet-main": ["leader"]})
        self.assertEqual(rc, 1)
        self.assertIn("task not found: task-99", err)
        self.assertEqual(fake.calls_named("attach"), [])

    def test_ambiguous_task_window_returns_error(self) -> None:
        self._add_task("42", "main")
        rc, fake, err = self._run(
            "42", {"fleet-main": ["leader", "42·designer", "42·implementer"]}
        )
        self.assertEqual(rc, 1)
        self.assertIn("multiple windows found", err)
        self.assertEqual(fake.calls_named("attach"), [])

    def test_unknown_window_returns_error(self) -> None:
        self._add_task("7", "main")
        rc, fake, err = self._run("7", {"fleet-main": ["leader"]})
        self.assertEqual(rc, 1)
        self.assertIn("window not found: fleet-main:7", err)
        self.assertEqual(fake.calls_named("attach"), [])

    def test_leader_session_not_running_lists_live_sessions(self) -> None:
        # ``alt`` is live (has a record); ``ghost`` is asked for but not running.
        self._write_session_record("alt")
        rc, fake, err = self._run("leader", {"fleet-alt": ["leader"]}, session="ghost")
        self.assertEqual(rc, 1)
        self.assertIn("tmux session not running: fleet-ghost", err)
        self.assertIn("live sessions: alt", err)
        self.assertIn("fleet leader --name ghost", err)
        self.assertNotIn("--project", err)
        self.assertEqual(fake.calls_named("attach"), [])

    def test_session_not_running_with_no_live_sessions(self) -> None:
        rc, _fake, err = self._run("leader", None)
        self.assertEqual(rc, 1)
        self.assertIn("tmux session not running: fleet-main", err)
        self.assertIn("live sessions: (none)", err)
        self.assertIn("fleet leader --name main", err)

    def test_task_owner_session_not_running(self) -> None:
        self._add_task("7", "alt")
        # The project-named session exists, but the task's owner session does not.
        rc, fake, err = self._run("7", {f"fleet-{self.project_name}": ["leader", "7·driver"]})
        self.assertEqual(rc, 1)
        self.assertIn("tmux session not running: fleet-alt", err)
        self.assertIn("fleet leader --name alt", err)
        self.assertEqual(fake.calls_named("attach"), [])

    def test_backend_unavailable(self) -> None:
        rc, _fake, err = self._run("leader", {"fleet-main": ["leader"]}, available=False)
        self.assertEqual(rc, 1)
        self.assertIn("tmux not on PATH", err)

    def test_attach_failure_returns_error(self) -> None:
        err = io.StringIO()
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}) as fake,
            contextlib.redirect_stderr(err),
        ):
            fake.fail["attach"] = MuxError("failed to create view session: dup")
            rc = run(self._make_args(target="leader"))
        self.assertEqual(rc, 1)
        self.assertIn("failed to create view session", err.getvalue())

    def test_attach_exit_code_is_returned(self) -> None:
        rc, _fake, _err = self._run("leader", {"fleet-main": ["leader"]}, attach_rc=3)
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
