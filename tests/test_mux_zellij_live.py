"""Live zellij backend suite: ``ZellijMux`` end to end against a real zellij.

This is the mux layer of docs/windows-support.md §4 / §6 exercised on a real
server with a plain shell as the pane command — no agent CLI, no API key. It is
what the ``unittest-zellij-linux`` CI job runs (Refs #258); run it locally with
the same command::

    FLEET_LIVE_ZELLIJ=1 python -m unittest tests.test_mux_zellij_live -v

(``FLEET_ZELLIJ`` points at a zellij that is not on ``PATH``.) It is opt-in and
skips cleanly without ``FLEET_LIVE_ZELLIJ=1``, without a zellij binary, or with
a zellij older than :data:`fleet.mux.zellij.MIN_VERSION`, so the default hermetic
``python tests/run_parallel.py`` neither needs nor touches zellij.

POSIX only (the pane is ``/bin/sh``; the Windows equivalent is
``tests.test_mux_zellij.ZellijLiveTests``). Every session is named
``fleet-citest-<random>`` and is torn down in a class / test cleanup even when a
test fails; no other session is ever addressed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
import unittest.mock
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from tests._fleet_test_helpers import requires_live_zellij  # noqa: E402  (hermetic env first)

from fleet import pane_launch  # noqa: E402
from fleet.mux import Key  # noqa: E402
from fleet.mux import zellij as zmod  # noqa: E402
from fleet.mux.zellij import ZellijError, ZellijMux, parse_exited_sessions, parse_sessions  # noqa: E402

SESSION_PREFIX = "fleet-citest-"
#: Shell used for the driver-style tabs: predictable prompt and quoting.
SH = "/bin/sh"


def new_session_name() -> str:
    return SESSION_PREFIX + uuid.uuid4().hex[:8]


def _guard(session: str) -> str:
    """Refuse to ever address a session that is not one of ours."""
    if not session.startswith(SESSION_PREFIX):
        raise AssertionError(f"live zellij tests must only touch {SESSION_PREFIX}* sessions, got {session!r}")
    return session


def teardown_session(m: ZellijMux, session: str) -> None:
    """Best-effort removal of one test session (live or resurrectable)."""
    _guard(session)
    try:
        if m.session_exists(session):
            m.kill_session(session)
    except Exception:
        pass
    b = m.binary
    if b:
        for cmd in (["kill-session", session], ["delete-session", "--force", session]):
            try:
                subprocess.run(
                    [b, *cmd], stdin=subprocess.DEVNULL, capture_output=True, timeout=30
                )
            except (OSError, subprocess.SubprocessError):
                pass


def wait_until(predicate, timeout: float = 20.0, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


@requires_live_zellij
@unittest.skipIf(sys.platform == "win32", "POSIX shell panes; see tests.test_mux_zellij.ZellijLiveTests on Windows")
class ZellijLiveTests(unittest.TestCase):
    """One shared session (leader tab on the platform shell); one tab per test."""

    m: ZellijMux
    session: str
    tmp: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = TemporaryDirectory(prefix="fleet-zlive-")
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.tmp = Path(cls._tmp.name)
        cls.m = ZellijMux(pane_env_dir=cls.tmp / "pane-env")
        if cls.m.version is None or cls.m.version < zmod.MIN_VERSION:
            raise unittest.SkipTest(f"zellij {cls.m.version} is older than {zmod.MIN_VERSION}")
        cls.session = _guard(new_session_name())
        # Registered before the session exists: runs even if creation fails.
        cls.addClassCleanup(teardown_session, cls.m, cls.session)
        # A creating agent's markers leak into the server env, hence every pane
        # (§4.4); the launcher must strip them.
        marker = unittest.mock.patch.dict(os.environ, {"CLAUDECODE": "1", "CLAUDE_CODE_CITEST": "1"})
        marker.start()
        try:
            # argv=None: the launcher starts the platform shell.
            cls.m.new_session(cls.session, window="leader", cwd=str(cls.tmp), env={"FLEET_CITEST": "leader"})
        finally:
            marker.stop()

    # -- helpers ----------------------------------------------------------------

    def open_tab(self, name: str, *, argv=(SH,), cwd=None, env=None) -> str:
        """Open tab ``name`` (closed again at the end of the test)."""
        self.m.new_window(
            self.session, name, argv=list(argv) if argv else None, cwd=str(cwd or self.tmp), env=env
        )
        self.addCleanup(self._close_tab_quietly, name)
        return name

    def _close_tab_quietly(self, name: str) -> None:
        try:
            if name in self.m.list_windows(self.session):
                self.m.kill_window(self.session, name)
        except Exception:
            pass

    def wait_capture(self, window: str, needle: str, timeout: float = 20.0) -> str:
        screen = ""

        def seen() -> bool:
            nonlocal screen
            screen = self.m.capture(self.session, window)
            return needle in screen

        if not wait_until(seen, timeout, 0.3):
            self.fail(f"{needle!r} not on the {window!r} screen:\n{screen}")
        return screen

    def say(self, window: str, marker: str, value: str = "") -> None:
        """Type a shell command whose *output* is ``marker[value]``.

        The typed command line contains ``marker[%s]``, never ``marker[value]``,
        so waiting for the latter cannot match the echoed input.
        """
        self.m.send_text(self.session, window, f"printf '{marker}[%s]\\n' {value or 'x'}")

    # -- environment ------------------------------------------------------------

    def test_binary_and_version(self) -> None:
        self.assertTrue(self.m.available())
        self.assertTrue(os.path.isabs(self.m.binary))
        self.assertGreaterEqual(self.m.version, zmod.MIN_VERSION)

    # -- session / tab lifecycle ------------------------------------------------

    def test_leader_tab_runs_the_platform_shell(self) -> None:
        self.assertTrue(self.m.session_exists(self.session))
        self.assertIn("leader", self.m.list_windows(self.session))
        self.say("leader", "LEADER", "up")
        self.wait_capture("leader", "LEADER[up]")

    def test_session_lifecycle_and_listing_hygiene(self) -> None:
        name = _guard(new_session_name())
        self.addCleanup(teardown_session, self.m, name)
        self.assertFalse(self.m.session_exists(name))
        self.m.new_session(name, window="lead", argv=[SH], cwd=str(self.tmp))
        self.assertTrue(self.m.session_exists(name))
        self.assertEqual(self.m.list_windows(name), ["lead"])
        with self.assertRaisesRegex(ZellijError, "duplicate session"):
            self.m.new_session(name, window="lead", argv=[SH])
        env_dir = self.m.pane_env_dir() / name
        self.assertTrue(env_dir.is_dir())

        self.m.kill_session(name)
        self.assertFalse(self.m.session_exists(name))
        # kill_session also drops the resurrectable entry (§4.6) and the env dir.
        raw = self.m._list_sessions_raw()
        self.assertNotIn(name, parse_sessions(raw))
        self.assertNotIn(name, parse_exited_sessions(raw))
        self.assertFalse(env_dir.exists())
        with self.assertRaises(ZellijError):
            self.m.kill_session(name)

    def test_session_names_do_not_collide_across_sessions(self) -> None:
        other = _guard(new_session_name())
        self.addCleanup(teardown_session, self.m, other)
        self.m.new_session(other, window="only", argv=[SH], cwd=str(self.tmp))
        self.assertEqual(self.m.list_windows(other), ["only"])
        self.assertNotIn("only", self.m.list_windows(self.session))
        self.assertTrue(self.m.session_exists(self.session))

    def test_list_tabs_and_panes(self) -> None:
        tab = self.open_tab("2·list")
        self.assertEqual(self.m.list_windows(self.session)[0], "leader")
        self.assertIn(tab, self.m.list_windows(self.session))
        panes = zmod.terminal_panes(self.m._panes(self.session), tab)
        self.assertEqual(len(panes), 1)
        self.assertRegex(self.m._pane_id(self.session, tab), r"^terminal_\d+$")
        self.assertEqual(self.m.tab_position(self.session, tab), self.m.list_windows(self.session).index(tab) + 1)
        self.assertIsNone(self.m.tab_position(self.session, "no-such-tab"))

    def test_new_tab_detached(self) -> None:
        # No client is attached: on 0.45.x this is the #5594 path (temp client).
        self.assertEqual(self.m._clients(self.session), [])
        tab = self.open_tab("3·detached")
        self.assertIn(tab, self.m.list_windows(self.session))
        self.assertEqual(self.m._clients(self.session), [], "the temporary client must be gone")
        self.say(tab, "DET", "ok")
        self.wait_capture(tab, "DET[ok]")

    def test_new_tab_with_a_client_attached(self) -> None:
        client = self.m._attach_temp_client(self.m.binary, self.session)
        try:
            self.assertTrue(self.m._clients(self.session))
            tab = self.open_tab("4·attached")
            self.assertIn(tab, self.m.list_windows(self.session))
            self.say(tab, "ATT", "ok")
            self.wait_capture(tab, "ATT[ok]")
        finally:
            self.m._kill_temp_client(self.session, client)
        self.assertEqual(self.m._clients(self.session), [])
        # The tab survives the client going away.
        self.assertIn("4·attached", self.m.list_windows(self.session))

    def test_close_tab_waits_for_the_pane_processes(self) -> None:
        keep = self.open_tab("5·keep")
        tab = self.open_tab("5·close")
        env_path = self.m._env_path(self.session, tab)
        self.assertTrue(env_path.is_file())
        pids = pane_launch.read_pid_file(pane_launch.pid_file_for(env_path))
        self.assertTrue(wait_until(lambda: len(pane_launch.read_pid_file(pane_launch.pid_file_for(env_path))) >= 2))
        pids = pane_launch.read_pid_file(pane_launch.pid_file_for(env_path))
        self.assertTrue(all(pane_launch.pid_alive(p) for p in pids))

        self.m.kill_window(self.session, tab)
        self.assertNotIn(tab, self.m.list_windows(self.session))
        self.assertIn(keep, self.m.list_windows(self.session))
        self.assertIn("leader", self.m.list_windows(self.session))
        # kill_window returns only once the pane's processes are gone.
        self.assertFalse(any(pane_launch.pid_alive(p) for p in pids))
        self.assertFalse(env_path.exists())
        self.assertFalse(pane_launch.pid_file_for(env_path).exists())

    def test_tab_name_can_be_reused_after_close(self) -> None:
        # Tab / pane ids are reused (§4.8): only names are stable.
        for round_ in ("first", "second"):
            tab = self.open_tab("6·again")
            self.say(tab, "RE", round_)
            self.wait_capture(tab, f"RE[{round_}]")
            self.m.kill_window(self.session, tab)
            self.assertNotIn(tab, self.m.list_windows(self.session))

    # -- per-pane environment (pane_launch) -------------------------------------

    def test_per_pane_env_cwd_and_marker_stripping(self) -> None:
        workdir = self.tmp / "citest-cwd"
        workdir.mkdir()
        tab = self.open_tab(
            "7·env", cwd=workdir,
            env={"FLEET_TASK_ID": "7", "FLEET_CITEST_MARK": "héllo·env"},
        )
        cmds = [
            ("TASK", '"$FLEET_TASK_ID"'),
            ("MARK", '"$FLEET_CITEST_MARK"'),
            ("UTF8", '"$PYTHONUTF8"'),
            ("CC", '"${CLAUDECODE-unset}"'),
            ("CCC", '"${CLAUDE_CODE_CITEST-unset}"'),
        ]
        for marker, value in cmds:
            self.say(tab, marker, value)
        self.say(tab, "CWD", '"$(basename "$(pwd -P)")"')
        screen = self.wait_capture(tab, "CWD[citest-cwd]")
        self.assertIn("TASK[7]", screen)
        self.assertIn("MARK[héllo·env]", screen)
        self.assertIn("UTF8[1]", screen)
        # The launcher strips the creating agent's session markers (§4.4).
        self.assertIn("CC[unset]", screen)
        self.assertIn("CCC[unset]", screen)

    def test_env_does_not_leak_between_tabs(self) -> None:
        a = self.open_tab("8·a", env={"FLEET_CITEST_MARK": "aaa"})
        b = self.open_tab("8·b", env={"FLEET_CITEST_MARK": "bbb"})
        self.say(a, "V", '"$FLEET_CITEST_MARK"')
        self.say(b, "V", '"$FLEET_CITEST_MARK"')
        self.wait_capture(a, "V[aaa]")
        self.wait_capture(b, "V[bbb]")

    # -- input / output ---------------------------------------------------------

    def test_send_text_and_capture_roundtrip(self) -> None:
        tab = self.open_tab("9·io")
        self.say(tab, "OUT", "plain")
        self.wait_capture(tab, "OUT[plain]")
        self.say(tab, "OUT", "héllo·ok")
        self.wait_capture(tab, "OUT[héllo·ok]")

    def test_send_text_without_enter_then_send_key_enter(self) -> None:
        tab = self.open_tab("10·keys")
        self.m.send_text(self.session, tab, "printf 'ENT[%s]\\n' done", enter=False)
        self.wait_capture(tab, "printf 'ENT[%s]")
        self.assertNotIn("ENT[done]", self.m.capture(self.session, tab))
        self.m.send_key(self.session, tab, "Enter")
        self.wait_capture(tab, "ENT[done]")

    def test_send_key_ctrl_u_clears_the_line(self) -> None:
        tab = self.open_tab("11·ctrlu")
        self.m.send_text(self.session, tab, "printf 'GONE[%s]\\n' x", enter=False)
        self.wait_capture(tab, "GONE[%s]")
        self.m.send_key(self.session, tab, Key("Ctrl-u"))
        self.say(tab, "KEPT", "y")
        screen = self.wait_capture(tab, "KEPT[y]")
        self.assertNotIn("GONE[x]", screen)

    def test_send_key_ctrl_c_interrupts_a_foreground_command(self) -> None:
        tab = self.open_tab("12·ctrlc")
        self.m.send_text(self.session, tab, "sleep 300; printf 'SLEPT[%s]\\n' x")
        time.sleep(0.5)
        self.m.send_key(self.session, tab, Key("Ctrl-c"))
        self.say(tab, "ALIVE", "yes")
        screen = self.wait_capture(tab, "ALIVE[yes]", timeout=10)
        self.assertNotIn("SLEPT[x]", screen)

    def test_text_starting_with_a_dash_is_typed_not_parsed(self) -> None:
        tab = self.open_tab("13·dash")
        self.m.send_text(self.session, tab, "-n --version -x", enter=False)
        self.wait_capture(tab, "-n --version -x")

    def test_paste(self) -> None:
        tab = self.open_tab("14·paste")
        self.m.paste(self.session, tab, "printf 'PASTE[%s]\\n' ok")
        self.m.send_key(self.session, tab, "Enter")
        self.wait_capture(tab, "PASTE[ok]")

    # -- what fleet relies on: zellij's exit-0 for a missing target -------------

    def test_action_on_a_missing_session_reports_not_found(self) -> None:
        ghost = _guard(new_session_name())
        r = subprocess.run(
            [self.m.binary, "-s", ghost, "action", "list-tabs", "-j"],
            stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        )
        # zellij exits 0 here (§4.1); the backend must key on the message.
        m = zmod._NOT_FOUND_RE.search(f"{r.stdout}\n{r.stderr}")
        self.assertIsNotNone(m, f"no 'Session … not found' in {r!r}")
        self.assertEqual(m.group(1), ghost)

        self.assertFalse(self.m.session_exists(ghost))
        for call in (
            lambda: self.m.list_windows(ghost),
            lambda: self.m.capture(ghost, "leader"),
            lambda: self.m.send_text(ghost, "leader", "x"),
            lambda: self.m.send_key(ghost, "leader", "Enter"),
            lambda: self.m.kill_window(ghost, "leader"),
            lambda: self.m.kill_session(ghost),
            lambda: self.m.new_window(ghost, "t", argv=[SH]),
        ):
            with self.assertRaises(ZellijError):
                call()
        self.assertFalse(self.m.session_exists(ghost), "a failed call must not create the session")

    def test_missing_tab_is_an_error_not_a_silent_noop(self) -> None:
        before = self.m.list_windows(self.session)
        for call in (
            lambda: self.m.capture(self.session, "no-such-tab"),
            lambda: self.m.send_text(self.session, "no-such-tab", "x"),
            lambda: self.m.send_key(self.session, "no-such-tab", "Enter"),
            lambda: self.m.paste(self.session, "no-such-tab", "x"),
            lambda: self.m.kill_window(self.session, "no-such-tab"),
        ):
            with self.assertRaisesRegex(ZellijError, "tab not found"):
                call()
        self.assertEqual(self.m.list_windows(self.session), before)

    def test_closing_a_missing_tab_id_exits_zero_and_changes_nothing(self) -> None:
        # The raw behaviour behind the listing-based confirmation in kill_window.
        before = self.m.list_windows(self.session)
        r = self.m._action(self.session, "close-tab-by-id", "987654", check=False)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.m.list_windows(self.session), before)

    # -- window_close_kills_caller ----------------------------------------------

    def test_stage_advance_from_a_caller_inside_the_closing_tab(self) -> None:
        """``done`` in the old stage's tab: kill that tab, then open the next one.

        This is the orchestrator's cross-stage advance run from a driver pane,
        with the agent's tool-call process as the caller. Such a caller is not a
        job of the pane's terminal (it has its own session, as an agent's shell
        does), so closing the tab must not end it: ``window_close_kills_caller``
        is False on POSIX and the advance has to complete. (A foreground job of
        the pane's own shell *is* hung up when the tab closes; that is why the
        Windows backend defers the launch to a detached helper.)
        """
        self.assertFalse(self.m.window_close_kills_caller)
        old, new = "15·stage1", "15·stage2"
        done = self.tmp / "advance.done"
        log = self.tmp / "advance.log"
        script = self.tmp / "advance.py"
        script.write_text(
            "import os, sys\n"
            "if os.fork():\n"  # the pane's shell job returns at once ...
            "    os._exit(0)\n"
            "os.setsid()\n"  # ... the caller lives on in a session of its own
            f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
            "from fleet.mux.zellij import ZellijMux\n"
            f"m = ZellijMux(pane_env_dir={str(self.tmp / 'pane-env')!r})\n"
            f"m.kill_window({self.session!r}, {old!r})\n"
            f"m.new_window({self.session!r}, {new!r}, argv=[{SH!r}])\n"
            f"open({str(done)!r}, 'w').write('ok')\n",
            encoding="utf-8",
        )
        self.addCleanup(self._close_tab_quietly, new)
        self.open_tab(old)
        self.m.send_text(self.session, old, f"{sys.executable} {script} >{log} 2>&1")
        completed = wait_until(done.exists, timeout=60)
        out = log.read_text(encoding="utf-8", errors="replace") if log.exists() else "<no log>"
        self.assertTrue(completed, f"the caller did not survive its tab closing; log: {out!r}")
        self.assertIn(new, self.m.list_windows(self.session))
        self.assertNotIn(old, self.m.list_windows(self.session))
        self.say(new, "NEXT", "ok")
        self.wait_capture(new, "NEXT[ok]")


if __name__ == "__main__":
    unittest.main()
