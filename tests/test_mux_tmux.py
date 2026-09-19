"""Tests for ``fleet.mux`` and its tmux backend.

The live tests use real tmux subprocesses (opt-in via FLEET_LIVE_TMUX=1) and an
isolated session name so they can run alongside an existing user session. The
rest are mock-based and need no tmux at all.
"""
from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet import mux  # noqa: E402
from fleet.mux import Key, MuxError  # noqa: E402
from fleet.mux.tmux import TmuxError, TmuxMux, tmux_key  # noqa: E402
from tests._fake_mux import FakeMux, use_fake_mux  # noqa: E402
from tests._fleet_test_helpers import requires_live_tmux  # noqa: E402


SESSION = "fleet-test-" + os.urandom(3).hex()


@requires_live_tmux
class TmuxLiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = TmuxMux()
        if self.m.session_exists(SESSION):
            self.m.kill_session(SESSION)
        self.m.new_session(SESSION, window="leader")
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        if self.m.session_exists(SESSION):
            self.m.kill_session(SESSION)
        self._tmp.cleanup()

    def test_session_lifecycle(self) -> None:
        self.assertTrue(self.m.session_exists(SESSION))
        self.assertEqual(self.m.list_windows(SESSION), ["leader"])

    def test_new_window_with_env_and_cwd(self) -> None:
        self.m.new_window(
            SESSION,
            "1·driver",
            cwd=str(self.tmp),
            env={"FLEET_TASK_ID": "1", "FLEET_STATE_DIR": str(self.tmp)},
        )
        self.assertIn("1·driver", self.m.list_windows(SESSION))

    def test_paste_and_preload(self) -> None:
        self.m.new_window(SESSION, "scratch", cwd=str(self.tmp))
        # paste must not raise and must not leave its one-shot buffer behind.
        self.m.paste(SESSION, "scratch", "hello paste")
        hint = self.m.preload_paste("fleet-test-buf", "hello buffer")
        self.assertIn("C-b ]", hint)
        self.m.drop_paste("fleet-test-buf")
        # Re-dropping must be safe (best-effort).
        self.m.drop_paste("fleet-test-buf")

    def _wait_for(self, window: str, needle: str, timeout: float = 5.0) -> str:
        import time

        deadline = time.monotonic() + timeout
        pane = ""
        while time.monotonic() < deadline:
            pane = self.m.capture(SESSION, window)
            if needle in pane:
                return pane
            time.sleep(0.1)
        self.fail(f"{needle!r} never appeared in pane:\n{pane}")

    def test_argv_is_typed_into_a_shell_that_survives(self) -> None:
        self.m.new_window(
            SESSION,
            "argv",
            argv=["printf", "%s-%s\\n", "argv", "ok"],
            cwd=str(self.tmp),
        )
        self._wait_for("argv", "argv-ok")
        # The shell is still there after the command exits.
        self.m.send_text(SESSION, "argv", "echo still-$((20+22))")
        self._wait_for("argv", "still-42")

    def test_paste_then_enter_and_literal_text(self) -> None:
        self.m.new_window(SESSION, "io", cwd=str(self.tmp))
        self.m.paste(SESSION, "io", "echo pasted-$((1+1))")
        self.m.send_key(SESSION, "io", "Enter")
        self._wait_for("io", "pasted-2")
        # Text that looks like a key name is typed literally, not pressed.
        self.m.send_text(SESSION, "io", "echo C-u", enter=False)
        self.m.send_key(SESSION, "io", "Ctrl-u")  # clears the line instead
        self.m.send_text(SESSION, "io", "echo cleared-ok")
        pane = self._wait_for("io", "cleared-ok\n")
        self.assertNotIn("C-uecho", pane)

    def test_kill_window(self) -> None:
        self.m.new_window(SESSION, "doomed")
        self.assertIn("doomed", self.m.list_windows(SESSION))
        self.m.kill_window(SESSION, "doomed")
        self.assertNotIn("doomed", self.m.list_windows(SESSION))

    def test_task_window_names_and_kill_task_windows(self) -> None:
        self.m.new_window(SESSION, "alpha·designer")
        self.m.new_window(SESSION, "alpha·implementer")
        self.m.new_window(SESSION, "alpha-other·driver")
        self.assertEqual(
            mux.task_window_names(SESSION, "alpha", backend=self.m),
            ["alpha·designer", "alpha·implementer"],
        )
        mux.kill_task_windows(SESSION, "alpha", backend=self.m)
        windows = self.m.list_windows(SESSION)
        self.assertNotIn("alpha·designer", windows)
        self.assertNotIn("alpha·implementer", windows)
        self.assertIn("alpha-other·driver", windows)


def _patch_run():
    return unittest.mock.patch("fleet.mux.tmux._run", return_value=None)


class NewWindowMockTests(unittest.TestCase):
    """Mock-based tests for new_window / new_session — no live tmux required."""

    def test_new_window_includes_detach_flag(self) -> None:
        with _patch_run() as mock_run:
            TmuxMux().new_window("sess", "mywin")
        args = mock_run.call_args[0][0]
        self.assertIn("-d", args)
        self.assertEqual(args[0], "tmux")
        self.assertEqual(args[1], "new-window")

    def test_new_window_detach_flag_position(self) -> None:
        with _patch_run() as mock_run:
            TmuxMux().new_window("sess", "mywin", cwd="/tmp", env={"FOO": "bar"})
        args = mock_run.call_args[0][0]
        # -d must appear before -t so it is always a positional flag
        self.assertLess(args.index("-d"), args.index("-t"))

    def test_new_window_argv_is_typed_into_the_shell(self) -> None:
        """argv is shlex-joined and typed, so the pane keeps a shell afterwards."""
        with _patch_run() as mock_run:
            TmuxMux().new_window(
                "sess",
                "1·driver",
                argv=["claude", "--name", "my proj"],
                cwd="/w",
                env={"FLEET_TASK_ID": "1"},
            )
        self.assertEqual(
            [c.args[0] for c in mock_run.call_args_list],
            [
                [
                    "tmux", "new-window", "-d", "-t", "sess", "-n", "1·driver",
                    "-c", "/w", "-e", "FLEET_TASK_ID=1",
                ],
                ["tmux", "send-keys", "-t", "sess:1·driver", "-l", "claude --name 'my proj'"],
                ["tmux", "send-keys", "-t", "sess:1·driver", "Enter"],
            ],
        )

    def test_new_window_without_argv_types_nothing(self) -> None:
        with _patch_run() as mock_run:
            TmuxMux().new_window("sess", "w")
        self.assertEqual(mock_run.call_count, 1)

    def test_new_session_with_window_and_argv(self) -> None:
        with _patch_run() as mock_run:
            TmuxMux().new_session(
                "fleet-main",
                window="leader",
                argv=["claude", "--model", "opus"],
                cwd="/clone",
                env={"FLEET_SESSION": "main"},
            )
        self.assertEqual(
            [c.args[0] for c in mock_run.call_args_list],
            [
                [
                    "tmux", "new-session", "-d", "-s", "fleet-main", "-n", "leader",
                    "-c", "/clone", "-e", "FLEET_SESSION=main",
                ],
                ["tmux", "send-keys", "-t", "fleet-main:leader", "-l", "claude --model opus"],
                ["tmux", "send-keys", "-t", "fleet-main:leader", "Enter"],
            ],
        )

    def test_new_session_bare(self) -> None:
        with _patch_run() as mock_run:
            TmuxMux().new_session("fleet-main", window="leader")
        mock_run.assert_called_once_with(
            ["tmux", "new-session", "-d", "-s", "fleet-main", "-n", "leader"]
        )


class WindowHelperTests(unittest.TestCase):
    def test_matching_task_window_names_matches_task_id_prefix(self) -> None:
        self.assertEqual(
            mux.matching_task_window_names(
                [
                    "leader",
                    "task-one·designer",
                    "task-one·implementer",
                    "task-one-extra·driver",
                    "task-one",
                ],
                "task-one",
            ),
            ["task-one·designer", "task-one·implementer", "task-one"],
        )

    def test_task_window_names_uses_backend_list_windows(self) -> None:
        with use_fake_mux(
            sessions={
                "sess": [
                    "leader",
                    "task-one·designer",
                    "task-one·implementer",
                    "task-one-extra·driver",
                    "task-one",
                ]
            }
        ):
            self.assertEqual(
                mux.task_window_names("sess", "task-one"),
                ["task-one·designer", "task-one·implementer", "task-one"],
            )

    def test_kill_task_windows_kills_all_matches(self) -> None:
        fake = FakeMux(
            sessions={"sess": ["leader", "task-one·designer", "task-one·implementer"]}
        )
        mux.kill_task_windows("sess", "task-one", backend=fake)
        self.assertEqual(
            [a for (a, _k) in fake.calls_named("kill_window")],
            [("sess", "task-one·designer"), ("sess", "task-one·implementer")],
        )
        self.assertEqual(fake.sessions["sess"], ["leader"])

    def test_list_windows_raises_tmux_error_which_is_a_mux_error(self) -> None:
        with unittest.mock.patch("fleet.mux.tmux.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = "no server"
            with self.assertRaises(MuxError):
                TmuxMux().list_windows("sess")
        self.assertTrue(issubclass(TmuxError, MuxError))


class SendTextMockTests(unittest.TestCase):
    """Mock-based tests for send_text / send_key — no live tmux required."""

    def test_empty_text_sends_only_enter(self) -> None:
        with _patch_run() as mock_run:
            TmuxMux().send_text("sess", "win", "", enter=True)
        mock_run.assert_called_once_with(
            ["tmux", "send-keys", "-t", "sess:win", "Enter"]
        )

    def test_nonempty_text_is_typed_literally_then_enter(self) -> None:
        with _patch_run() as mock_run:
            TmuxMux().send_text("sess", "win", "hello", enter=True)
        self.assertEqual(
            [c.args[0] for c in mock_run.call_args_list],
            [
                ["tmux", "send-keys", "-t", "sess:win", "-l", "hello"],
                ["tmux", "send-keys", "-t", "sess:win", "Enter"],
            ],
        )

    def test_enter_false_skips_enter(self) -> None:
        with _patch_run() as mock_run:
            TmuxMux().send_text("sess", "win", "hello", enter=False)
        mock_run.assert_called_once_with(
            ["tmux", "send-keys", "-t", "sess:win", "-l", "hello"]
        )

    def test_empty_text_enter_false_sends_nothing(self) -> None:
        with _patch_run() as mock_run:
            TmuxMux().send_text("sess", "win", "", enter=False)
        mock_run.assert_not_called()

    def test_text_that_looks_like_a_key_is_still_literal(self) -> None:
        with _patch_run() as mock_run:
            TmuxMux().send_text("sess", "win", "C-u", enter=False)
        mock_run.assert_called_once_with(
            ["tmux", "send-keys", "-t", "sess:win", "-l", "C-u"]
        )

    def test_send_key_translates_normalized_names(self) -> None:
        with _patch_run() as mock_run:
            m = TmuxMux()
            m.send_key("sess", "win", "Ctrl-u")
            m.send_key("sess", "win", Key("Enter"))
        self.assertEqual(
            [c.args[0] for c in mock_run.call_args_list],
            [
                ["tmux", "send-keys", "-t", "sess:win", "C-u"],
                ["tmux", "send-keys", "-t", "sess:win", "Enter"],
            ],
        )


class KeyTranslationTests(unittest.TestCase):
    def test_tmux_key_names(self) -> None:
        self.assertEqual(tmux_key("Enter"), "Enter")
        self.assertEqual(tmux_key("Ctrl-u"), "C-u")
        self.assertEqual(tmux_key(Key("Ctrl-c")), "C-c")
        self.assertEqual(tmux_key("Alt-b"), "M-b")
        self.assertEqual(tmux_key("Escape"), "Escape")
        self.assertEqual(tmux_key("Backspace"), "BSpace")

    def test_unknown_key_names_are_rejected(self) -> None:
        for bad in ("C-u", "ctrl-u", "Ctrl-", "Ctrl-uu", "Return", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(MuxError):
                    tmux_key(bad)
                with self.assertRaises(MuxError):
                    Key(bad)

    def test_send_key_rejects_unknown_key_before_running_tmux(self) -> None:
        with _patch_run() as mock_run:
            with self.assertRaises(MuxError):
                TmuxMux().send_key("sess", "win", "C-u")
        mock_run.assert_not_called()


class SendStepTests(unittest.TestCase):
    def test_text_and_key_steps(self) -> None:
        fake = FakeMux()
        mux.send_step("s", "w", "/rename", enter=False, backend=fake)
        mux.send_step("s", "w", "", enter=True, backend=fake)
        mux.send_step("s", "w", Key("Ctrl-u"), enter=False, backend=fake)
        mux.send_step("s", "w", Key("Ctrl-u"), enter=True, backend=fake)
        self.assertEqual(
            fake.sent(),
            [
                ("text", "w", "/rename"),
                ("key", "w", "Enter"),
                ("key", "w", "Ctrl-u"),
                ("key", "w", "Ctrl-u"),
                ("key", "w", "Enter"),
            ],
        )

    def test_send_step_uses_process_backend_by_default(self) -> None:
        with use_fake_mux() as fake:
            mux.send_step("s", "w", "hi", enter=True)
        self.assertEqual(fake.sent(), [("text", "w", "hi"), ("key", "w", "Enter")])


class PasteMockTests(unittest.TestCase):
    def test_paste_loads_one_shot_buffer_and_pastes_with_delete(self) -> None:
        seen: dict = {}

        def fake_run(args):
            if args[1] == "load-buffer":
                seen["buffer"] = args[3]
                seen["path"] = args[5]
                seen["text"] = Path(args[5]).read_text(encoding="utf-8")

        with unittest.mock.patch("fleet.mux.tmux._run", side_effect=fake_run) as run:
            TmuxMux().paste("sess", "win", "Read the prompt file · ok")

        calls = [c.args[0] for c in run.call_args_list]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][:3], ["tmux", "load-buffer", "-b"])
        self.assertEqual(calls[0][4], "--")
        self.assertTrue(seen["buffer"].startswith(f"fleet-paste-{os.getpid()}-"))
        self.assertEqual(seen["text"], "Read the prompt file · ok")
        self.assertEqual(
            calls[1],
            ["tmux", "paste-buffer", "-d", "-t", "sess:win", "-b", seen["buffer"]],
        )
        # The temp file is removed once loaded.
        self.assertFalse(Path(seen["path"]).exists())

    def test_paste_failure_drops_buffer_and_raises(self) -> None:
        def fake_run(args):
            if args[1] == "paste-buffer":
                raise TmuxError("no pane")

        with (
            unittest.mock.patch("fleet.mux.tmux._run", side_effect=fake_run),
            unittest.mock.patch("fleet.mux.tmux.subprocess.run") as sub_run,
        ):
            with self.assertRaises(MuxError):
                TmuxMux().paste("sess", "win", "x")
        args = sub_run.call_args.args[0]
        self.assertEqual(args[:3], ["tmux", "delete-buffer", "-b"])

    def test_preload_paste_uses_the_named_buffer(self) -> None:
        seen: dict = {}

        def fake_run(args):
            seen["args"] = args
            seen["text"] = Path(args[5]).read_text(encoding="utf-8")

        with unittest.mock.patch("fleet.mux.tmux._run", side_effect=fake_run):
            hint = TmuxMux().preload_paste("fleet-task-7", "pointer text")
        self.assertEqual(seen["args"][:5], ["tmux", "load-buffer", "-b", "fleet-task-7", "--"])
        self.assertEqual(seen["text"], "pointer text")
        self.assertEqual(hint, "inside the pane press C-b ], then Enter")

    def test_drop_paste_deletes_the_named_buffer_best_effort(self) -> None:
        with unittest.mock.patch("fleet.mux.tmux.subprocess.run") as run:
            run.return_value.returncode = 1  # missing buffer is not an error
            TmuxMux().drop_paste("fleet-task-7")
        run.assert_called_once_with(
            ["tmux", "delete-buffer", "-b", "fleet-task-7"], capture_output=True
        )


class CaptureMockTests(unittest.TestCase):
    @unittest.mock.patch("fleet.mux.tmux.subprocess.run")
    def test_capture_uses_recent_history(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "pane text"
        self.assertEqual(TmuxMux().capture("sess", "win"), "pane text")
        mock_run.assert_called_once_with(
            ["tmux", "capture-pane", "-p", "-J", "-S", "-200", "-t", "sess:win"],
            capture_output=True,
            text=True,
        )

    @unittest.mock.patch("fleet.mux.tmux.subprocess.run")
    def test_capture_failure_raises(self, mock_run) -> None:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "can't find pane"
        with self.assertRaises(TmuxError):
            TmuxMux().capture("sess", "win")


class AttachHintTests(unittest.TestCase):
    def test_attach_hint(self) -> None:
        m = TmuxMux()
        self.assertEqual(m.attach_hint("fleet-main"), "tmux attach -t fleet-main")
        self.assertEqual(
            m.attach_hint("fleet-main", "1·driver"), "tmux attach -t fleet-main:1·driver"
        )
        self.assertEqual(
            m.kill_session_hint("fleet-main"), "tmux kill-session -t fleet-main"
        )

    def test_attach_without_window_execs_plain_attach(self) -> None:
        with (
            unittest.mock.patch("fleet.mux.tmux.os.name", "posix"),
            unittest.mock.patch("fleet.mux.tmux.os.execvp") as execvp,
        ):
            TmuxMux().attach("fleet-main")
        execvp.assert_called_once_with("tmux", ["tmux", "attach", "-t", "fleet-main"])


class AvailabilityTests(unittest.TestCase):
    def _env(self, **kv):
        env = {k: v for k, v in os.environ.items() if k not in ("FLEET_NO_MUX", "FLEET_NO_TMUX")}
        env.update(kv)
        return unittest.mock.patch.dict(os.environ, env, clear=True)

    def test_fleet_no_mux_disables(self) -> None:
        with self._env(FLEET_NO_MUX="1"), unittest.mock.patch(
            "fleet.mux.tmux.shutil.which", return_value="/usr/bin/tmux"
        ):
            self.assertFalse(TmuxMux().available())

    def test_fleet_no_tmux_alias_disables(self) -> None:
        with self._env(FLEET_NO_TMUX="1"), unittest.mock.patch(
            "fleet.mux.tmux.shutil.which", return_value="/usr/bin/tmux"
        ):
            self.assertFalse(TmuxMux().available())

    def test_available_when_on_path(self) -> None:
        with self._env(), unittest.mock.patch(
            "fleet.mux.tmux.shutil.which", return_value="/usr/bin/tmux"
        ):
            self.assertTrue(TmuxMux().available())
        with self._env(), unittest.mock.patch(
            "fleet.mux.tmux.shutil.which", return_value=None
        ):
            self.assertFalse(TmuxMux().available())


class BackendSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = mux.set_backend(None)

    def tearDown(self) -> None:
        mux.set_backend(self._prev)

    def _env(self, value: str | None):
        env = dict(os.environ)
        env.pop("FLEET_MUX", None)
        if value is not None:
            env["FLEET_MUX"] = value
        return unittest.mock.patch.dict(os.environ, env, clear=True)

    def test_default_is_tmux(self) -> None:
        with self._env(None):
            self.assertEqual(mux.backend_name(), "tmux")
            self.assertIsInstance(mux.get(), TmuxMux)

    def test_explicit_tmux(self) -> None:
        with self._env(" TMUX "):
            self.assertIsInstance(mux.get(), TmuxMux)

    def test_get_is_cached_per_process(self) -> None:
        with self._env(None):
            self.assertIs(mux.get(), mux.get())

    def test_zellij_not_implemented_yet(self) -> None:
        with self._env("zellij"):
            with self.assertRaises(MuxError) as cm:
                mux.get()
        self.assertIn("zellij backend not implemented yet", str(cm.exception))

    def test_unknown_backend(self) -> None:
        with self._env("screen"):
            with self.assertRaises(MuxError) as cm:
                mux.get()
        self.assertIn("screen", str(cm.exception))

    def test_set_backend_installs_and_restores(self) -> None:
        fake = FakeMux()
        prev = mux.set_backend(fake)
        try:
            self.assertIs(mux.get(), fake)
        finally:
            mux.set_backend(prev)


if __name__ == "__main__":
    unittest.main()
