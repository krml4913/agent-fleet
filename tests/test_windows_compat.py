"""Windows-compatibility helpers (docs/windows-support.md §7 PR1).

Covers the portable file lock, ``os.replace`` retry, ``spawn_detached``,
``fleet_agent_bin`` / prompt embedding on both platforms, the Windows
prompt-file project inference, and the UTF-8 stdio behavior of the CLI.
These run on every OS; platform-specific branches are exercised by patching
the ``_is_windows`` / ``_IS_WINDOWS`` switches (with ``subprocess.Popen``
mocked) rather than by pretending the host OS is different.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet import cli, locking, paths, proc  # noqa: E402
from fleet.commands import start as start_cmd  # noqa: E402
from tests._fleet_test_helpers import make_project, run_fleet  # noqa: E402


class LockFileTests(unittest.TestCase):
    """Real locks on the host OS (flock on POSIX, msvcrt.locking on Windows)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        # addCleanup (LIFO) so lock fps close before the dir is removed.
        self.addCleanup(self._tmp.cleanup)
        self.lock_path = Path(self._tmp.name) / "x.lock"

    def _open(self):
        fp = open(self.lock_path, "a+", encoding="utf-8")  # noqa: SIM115
        self.addCleanup(fp.close)
        return fp

    def test_nonblocking_fails_while_held_and_succeeds_after_release(self) -> None:
        a, b = self._open(), self._open()
        self.assertTrue(locking.lock_file(a, blocking=False))
        self.assertFalse(locking.lock_file(b, blocking=False))
        locking.unlock_file(a)
        self.assertTrue(locking.lock_file(b, blocking=False))
        locking.unlock_file(b)

    def test_blocking_waits_for_release(self) -> None:
        a, b = self._open(), self._open()
        self.assertTrue(locking.lock_file(a))
        acquired = threading.Event()

        def waiter() -> None:
            locking.lock_file(b)
            acquired.set()
            locking.unlock_file(b)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.2)
        self.assertFalse(acquired.is_set(), "blocking lock returned while held")
        locking.unlock_file(a)
        t.join(timeout=5)
        self.assertTrue(acquired.is_set())

    def test_lock_works_on_nonempty_file_opened_for_append(self) -> None:
        # The lock is on byte 0 regardless of the append position.
        self.lock_path.write_text("some content\n", encoding="utf-8")
        a, b = self._open(), self._open()
        a.seek(0, os.SEEK_END)
        self.assertTrue(locking.lock_file(a, blocking=False))
        self.assertFalse(locking.lock_file(b, blocking=False))
        locking.unlock_file(a)


class ReplaceFileTests(unittest.TestCase):
    def test_posix_calls_os_replace_once(self) -> None:
        with (
            mock.patch.object(locking, "_IS_WINDOWS", False),
            mock.patch("fleet.locking.os.replace", side_effect=PermissionError) as rep,
        ):
            with self.assertRaises(PermissionError):
                locking.replace_file(Path("a"), Path("b"))
        self.assertEqual(rep.call_count, 1)

    def test_windows_retries_permission_error(self) -> None:
        with (
            mock.patch.object(locking, "_IS_WINDOWS", True),
            mock.patch.object(locking, "_REPLACE_RETRY_SECONDS", 0),
            mock.patch(
                "fleet.locking.os.replace",
                side_effect=[PermissionError, PermissionError, None],
            ) as rep,
        ):
            locking.replace_file(Path("a"), Path("b"))
        self.assertEqual(rep.call_count, 3)

    def test_windows_retry_is_bounded(self) -> None:
        with (
            mock.patch.object(locking, "_IS_WINDOWS", True),
            mock.patch.object(locking, "_REPLACE_RETRY_SECONDS", 0),
            mock.patch("fleet.locking.os.replace", side_effect=PermissionError) as rep,
        ):
            with self.assertRaises(PermissionError):
                locking.replace_file(Path("a"), Path("b"))
        self.assertEqual(rep.call_count, locking._REPLACE_RETRIES)


class SpawnDetachedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.log = Path(self._tmp.name) / "helper.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_posix_uses_new_session(self) -> None:
        with (
            mock.patch("fleet.proc._is_windows", return_value=False),
            mock.patch("fleet.proc.subprocess.Popen") as popen,
        ):
            proc.spawn_detached(
                ["python", "-m", "x", Path("p")], cwd=Path("/r"), env={"A": "1"},
                log_path=self.log,
            )
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["python", "-m", "x", "p"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertNotIn("creationflags", kwargs)
        self.assertEqual(kwargs["cwd"], str(Path("/r")))
        self.assertEqual(kwargs["env"], {"A": "1"})  # POSIX env untouched
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], kwargs["stderr"])
        self.assertTrue(self.log.exists())

    def test_windows_uses_detach_flags(self) -> None:
        with (
            mock.patch("fleet.proc._is_windows", return_value=True),
            mock.patch("fleet.proc.subprocess.Popen") as popen,
        ):
            proc.spawn_detached(["py"], cwd="C:/r", env={"A": "1"}, log_path=self.log)
        popen.assert_called_once()
        kwargs = popen.call_args.kwargs
        self.assertNotIn("start_new_session", kwargs)
        flags = kwargs["creationflags"]
        self.assertTrue(flags & proc.DETACHED_PROCESS)
        self.assertTrue(flags & proc.CREATE_NEW_PROCESS_GROUP)
        self.assertTrue(flags & proc.CREATE_BREAKAWAY_FROM_JOB)
        self.assertEqual(kwargs["env"]["PYTHONUTF8"], "1")
        self.assertEqual(kwargs["env"]["A"], "1")
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)

    def test_windows_retries_without_breakaway(self) -> None:
        with (
            mock.patch("fleet.proc._is_windows", return_value=True),
            mock.patch(
                "fleet.proc.subprocess.Popen", side_effect=[OSError("denied"), mock.Mock()]
            ) as popen,
        ):
            proc.spawn_detached(["py"], cwd="C:/r", env=None, log_path=self.log)
        self.assertEqual(popen.call_count, 2)
        first = popen.call_args_list[0].kwargs["creationflags"]
        second = popen.call_args_list[1].kwargs["creationflags"]
        self.assertTrue(first & proc.CREATE_BREAKAWAY_FROM_JOB)
        self.assertFalse(second & proc.CREATE_BREAKAWAY_FROM_JOB)
        self.assertTrue(second & proc.DETACHED_PROCESS)
        self.assertTrue(second & proc.CREATE_NEW_PROCESS_GROUP)

    def test_real_spawn_writes_to_log(self) -> None:
        p = proc.spawn_detached(
            [sys.executable, "-c", "print('hello from child')"],
            cwd=self._tmp.name,
            env=os.environ.copy(),
            log_path=self.log,
        )
        p.wait(timeout=30)
        self.assertIn(b"hello from child", self.log.read_bytes())


class FleetAgentBinTests(unittest.TestCase):
    def test_posix_returns_extensionless_script(self) -> None:
        with mock.patch("fleet.paths._is_windows", return_value=False):
            bin_path = paths.fleet_agent_bin()
        self.assertEqual(Path(bin_path).name, "fleet-agent")
        self.assertTrue(Path(bin_path).is_absolute())

    def test_windows_returns_forward_slash_cmd_shim(self) -> None:
        with mock.patch("fleet.paths._is_windows", return_value=True):
            bin_path = paths.fleet_agent_bin()
        self.assertTrue(bin_path.endswith("/fleet-agent.cmd"), bin_path)
        self.assertNotIn("\\", bin_path)
        self.assertTrue(Path(bin_path).is_file())

    def test_prompt_bin_ref_posix_quotes(self) -> None:
        with mock.patch("fleet.paths._is_windows", return_value=False):
            self.assertEqual(paths.prompt_bin_ref("/a b/fleet-agent"), "'/a b/fleet-agent'")
            self.assertEqual(paths.prompt_bin_ref("/ab/fleet-agent"), "/ab/fleet-agent")

    def test_prompt_bin_ref_windows_unquoted(self) -> None:
        with mock.patch("fleet.paths._is_windows", return_value=True):
            self.assertEqual(
                paths.prompt_bin_ref("D:/x/fleet-agent.cmd"), "D:/x/fleet-agent.cmd"
            )

    def test_cmd_shims_exist_with_crlf(self) -> None:
        for name in ("fleet.cmd", "fleet-agent.cmd"):
            data = (ROOT / name).read_bytes()
            self.assertIn(b"PYTHONUTF8=1", data)
            self.assertIn(b"%*", data)
            self.assertIn(b"\r\n", data, f"{name} must keep CRLF line endings")


@unittest.skipUnless(sys.platform == "win32", "Windows path semantics")
class InferProjectWindowsTests(unittest.TestCase):
    def test_case_and_separator_insensitive(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "fleet-state" / "projects"
            (root / "demo" / "tasks").mkdir(parents=True)
            pf = root / "demo" / "tasks" / "p.md"
            weird = Path(str(pf).upper().replace("\\", "/"))
            self.assertEqual(start_cmd._infer_project_windows(weird, root), "DEMO")
            self.assertEqual(start_cmd._infer_project_windows(pf.resolve(), root), "demo")
            outside = Path(tmp) / "other" / "p.md"
            self.assertIsNone(start_cmd._infer_project_windows(outside.resolve(), root))


class Utf8StdioTests(unittest.TestCase):
    def test_windows_reconfigures_non_utf8_stream(self) -> None:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        with (
            mock.patch.object(cli.sys, "platform", "win32"),
            mock.patch.object(cli.sys, "stdout", stream),
            mock.patch.object(cli.sys, "stderr", io.StringIO()),  # no reconfigure: skipped
        ):
            cli._ensure_utf8_stdio()
            print("✔ a · b — c")
        stream.flush()
        self.assertEqual(stream.encoding, "utf-8")
        self.assertEqual(raw.getvalue().decode("utf-8").strip(), "✔ a · b — c")

    def test_posix_leaves_streams_alone(self) -> None:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="latin-1")
        with (
            mock.patch.object(cli.sys, "platform", "linux"),
            mock.patch.object(cli.sys, "stdout", stream),
        ):
            cli._ensure_utf8_stdio()
        self.assertEqual(stream.encoding, "latin-1")

    def test_cli_output_is_utf8_when_piped(self) -> None:
        # ``fleet status`` prints "·" / "—". Piped output must be UTF-8 and
        # must not crash even when the locale encoding is not UTF-8 (cp932 /
        # cp1252 on Windows). PYTHONUTF8=0 disables UTF-8 mode explicitly.
        with TemporaryDirectory() as tmp:
            fleet_home = Path(tmp) / "fleet-state"
            fleet_home.mkdir()
            project = Path(tmp) / "proj"
            project.mkdir()
            make_project(fleet_home, "demo", project)
            env = os.environ.copy()
            env.pop("PYTHONIOENCODING", None)
            env["PYTHONUTF8"] = "0"
            env["FLEET_HOME"] = str(fleet_home)
            env.pop("FLEET_TASK_ID", None)
            env.pop("FLEET_STATE_DIR", None)
            r = subprocess.run(
                [sys.executable, str(ROOT / "fleet"), "status", "demo"],
                capture_output=True,
                cwd=str(project),
                env=env,
            )
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn("demo  ·  ", r.stdout.decode("utf-8"))


class DriverPathTests(unittest.TestCase):
    def test_driver_path_joined_with_os_pathsep(self) -> None:
        with TemporaryDirectory() as tmp:
            fleet_home = Path(tmp) / "fleet-state"
            fleet_home.mkdir()
            project = Path(tmp) / "proj"
            project.mkdir()
            with mock.patch.dict(os.environ, {"FLEET_HOME": str(fleet_home)}):
                state_dir = make_project(fleet_home, "demo", project)
                task_dir = state_dir / "tasks" / "task-p1"
                task_dir.mkdir(parents=True)
                (task_dir / "driver-prompt.md").write_text("prompt", encoding="utf-8")
                with (
                    mock.patch("fleet.commands.start.tmux_mod") as mock_tmux,
                    mock.patch("sys.stdout", new_callable=io.StringIO),
                ):
                    mock_tmux.session_exists.return_value = True
                    mock_tmux.TmuxError = Exception
                    start_cmd.launch_stage_driver(
                        state_dir=state_dir,
                        task_id="p1",
                        task_dir=task_dir,
                        stage_idx=0,
                        stage={"agent": "claude:sonnet", "role": "implementer"},
                        project_name="demo",
                        owner_session="main",
                        auto_paste=False,
                    )
        env = mock_tmux.new_window.call_args.kwargs["env"]
        repo_root = str(start_cmd._fleet_clone_root())
        self.assertTrue(env["PATH"].startswith(repo_root + os.pathsep), env["PATH"])

    def test_run_fleet_helper_decodes_utf8(self) -> None:
        r = run_fleet("--help")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
