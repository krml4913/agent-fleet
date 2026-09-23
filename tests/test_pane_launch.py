"""Tests for ``fleet.pane_launch`` (the zellij pane launcher, spec §6.3)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import tests._fleet_test_helpers  # noqa: E402,F401  (hermetic env: FLEET_NO_NOTIFY / FLEET_NO_MUX)

from fleet import pane_launch as pl  # noqa: E402


class MarkerTests(unittest.TestCase):
    def test_strips_agent_session_markers(self) -> None:
        env = {
            "CLAUDECODE": "1",
            "CLAUDE_PID": "123",
            "CLAUDE_CODE_CHILD_SESSION": "1",
            "CLAUDE_CODE_SESSION_ID": "abc",
            "CLAUDE_CODE_MESSAGING_SOCKET": "x",
            "CLAUDE_CODE_ENTRYPOINT": "cli",
            "CLAUDE_CONFIG_DIR": "/cfg",
            "ANTHROPIC_API_KEY": "k",
            "ANTHROPIC_BASE_URL": "u",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_GIT_BASH_PATH": "C:/Git/bin/bash.exe",
            "HOME": "/home/u",
        }
        out = pl.strip_agent_markers(env)
        self.assertEqual(
            sorted(out),
            sorted([
                "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_GIT_BASH_PATH", "HOME",
            ]),
        )

    def test_strips_creator_effort_and_agent_identity(self) -> None:
        # CLAUDE_EFFORT would force the creating session's effort level onto
        # every pane; AI_AGENT names the creating claude session.
        env = {"CLAUDE_EFFORT": "high", "AI_AGENT": "claude-code_2_agent", "HOME": "/h"}
        self.assertEqual(pl.strip_agent_markers(env), {"HOME": "/h"})

    @unittest.skipUnless(sys.platform == "win32", "case-insensitive env on Windows")
    def test_markers_case_insensitive_on_windows(self) -> None:
        self.assertTrue(pl.is_agent_marker("claudecode"))
        self.assertTrue(pl.is_agent_marker("Claude_Code_Session_Id"))


class FleetVarTests(unittest.TestCase):
    def test_strips_inherited_fleet_vars(self) -> None:
        env = {
            "FLEET_SESSION": "main",
            "FLEET_STATE_DIR": "/leader/session/dir",
            "FLEET_TASK_ID": "leaked",
            "HOME": "/h",
        }
        self.assertEqual(pl.strip_fleet_vars(env), {"HOME": "/h"})

    def test_keeps_user_level_fleet_config_vars(self) -> None:
        # Only the task-scoped FLEET_* keys a per-pane override always
        # supplies are stripped; FLEET_HOME / FLEET_MUX / FLEET_NO_NOTIFY /
        # FLEET_NO_MUX / FLEET_NO_TMUX / FLEET_ZELLIJ* are user configuration
        # a driver pane must keep seeing (a blanket FLEET_* strip broke this).
        env = {
            "FLEET_HOME": "/custom/fleet-home",
            "FLEET_MUX": "tmux",
            "FLEET_NO_NOTIFY": "1",
            "FLEET_NO_MUX": "1",
            "FLEET_NO_TMUX": "1",
            "FLEET_ZELLIJ": "/opt/zellij",
            "FLEET_ZELLIJ_TEMP_CLIENT": "1",
        }
        self.assertEqual(pl.strip_fleet_vars(env), env)
        for key in env:
            self.assertFalse(pl.is_fleet_var(key), key)

    @unittest.skipUnless(sys.platform == "win32", "case-insensitive env on Windows")
    def test_fleet_vars_case_insensitive_on_windows(self) -> None:
        self.assertTrue(pl.is_fleet_var("fleet_session"))
        self.assertFalse(pl.is_fleet_var("fleet_home"))


class BuildEnvTests(unittest.TestCase):
    def test_overrides_fixed_vars_and_path(self) -> None:
        root = os.path.abspath("clone-root")
        inherited = {
            "PATH": os.pathsep.join(["/usr/bin", "~/bin", root]),
            "CLAUDECODE": "1",
            "KEEP": "yes",
        }
        env = pl.build_env(
            inherited,
            {"FLEET_TASK_ID": "7", "FLEET_STATE_DIR": "/state", "FLEET_SESSION": "p"},
            clone_root=root,
        )
        self.assertNotIn("CLAUDECODE", env)
        self.assertEqual(env["KEEP"], "yes")
        self.assertEqual(env["FLEET_TASK_ID"], "7")
        self.assertEqual(env["FLEET_SESSION"], "p")
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["MSYS_NO_PATHCONV"], "1")
        parts = env["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], root)
        self.assertEqual(parts.count(root), 1)  # not duplicated
        self.assertIn(os.path.expanduser("~/bin"), parts)  # ~ expanded
        self.assertNotIn("~/bin", parts)

    def test_override_path_wins_over_inherited(self) -> None:
        root = os.path.abspath("r")
        env = pl.build_env({"PATH": "/old"}, {"PATH": "/new"}, clone_root=root)
        self.assertEqual(env["PATH"].split(os.pathsep), [root, "/new"])

    def test_override_cannot_be_stripped(self) -> None:
        # An explicit override is applied after stripping (caller intent wins).
        env = pl.build_env({}, {"CLAUDE_CODE_FOO": "1"}, clone_root="/r")
        self.assertEqual(env["CLAUDE_CODE_FOO"], "1")

    def test_inherited_fleet_vars_do_not_leak_through(self) -> None:
        # zellij has no per-pane env: every pane in a session inherits the
        # *server's* environment — the leader's, if the session was created
        # from a leader pane (#315). None of that may survive into a driver
        # pane's env, even for a FLEET_* key the overrides don't set.
        inherited = {
            "FLEET_SESSION": "main",
            "FLEET_STATE_DIR": "/leader/session/dir",
            "HOME": "/h",
        }
        env = pl.build_env(inherited, {"FLEET_TASK_ID": "7"}, clone_root="/r")
        self.assertEqual(env["FLEET_TASK_ID"], "7")
        self.assertNotIn("FLEET_SESSION", env)
        self.assertNotIn("FLEET_STATE_DIR", env)
        self.assertEqual(env["HOME"], "/h")

    def test_fleet_var_override_still_wins_over_inherited(self) -> None:
        inherited = {"FLEET_STATE_DIR": "/leader/session/dir"}
        env = pl.build_env(
            inherited, {"FLEET_STATE_DIR": "/project/state"}, clone_root="/r"
        )
        self.assertEqual(env["FLEET_STATE_DIR"], "/project/state")

    def test_user_level_fleet_vars_survive_build_env(self) -> None:
        # FLEET_HOME (state root) / FLEET_MUX (backend choice) are user
        # configuration, not task-scoped overrides: build_env must not wipe
        # them just because they share the FLEET_ prefix (#315 follow-up).
        inherited = {
            "FLEET_HOME": "/custom/fleet-home",
            "FLEET_MUX": "tmux",
            "FLEET_SESSION": "main",
            "FLEET_STATE_DIR": "/leader/session/dir",
        }
        env = pl.build_env(
            inherited, {"FLEET_TASK_ID": "7", "FLEET_STATE_DIR": "/project/state"},
            clone_root="/r",
        )
        self.assertEqual(env["FLEET_HOME"], "/custom/fleet-home")
        self.assertEqual(env["FLEET_MUX"], "tmux")
        self.assertEqual(env["FLEET_STATE_DIR"], "/project/state")
        self.assertNotIn("FLEET_SESSION", env)


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.fallback = self.tmp / "fallback"
        self.fallback.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_exe(self, d: Path, name: str) -> Path:
        if sys.platform == "win32":
            p = d / f"{name}.exe"
            p.write_bytes(b"")
        else:
            p = d / name
            p.write_text("#!/bin/sh\n", encoding="utf-8")
            p.chmod(0o755)
        return p

    def test_resolves_on_path(self) -> None:
        exe = self._make_exe(self.bin, "agentx")
        got = pl.resolve_command("agentx", str(self.bin))
        self.assertEqual(os.path.normcase(got), os.path.normcase(str(exe)))

    def test_falls_back_to_extra_dirs(self) -> None:
        exe = self._make_exe(self.fallback, "agenty")
        self.assertIsNone(pl.resolve_command("agenty", str(self.bin)))
        got = pl.resolve_command("agenty", str(self.bin), extra_dirs=[str(self.fallback)])
        self.assertEqual(os.path.normcase(got), os.path.normcase(str(exe)))

    def test_tilde_entry_in_path_expanded(self) -> None:
        exe = self._make_exe(self.bin, "agentz")
        with unittest.mock.patch.dict(os.environ, {"HOME": str(self.tmp), "USERPROFILE": str(self.tmp)}):
            got = pl.resolve_command("agentz", "~/bin")
        self.assertEqual(os.path.normcase(got), os.path.normcase(str(exe)))

    def test_not_found(self) -> None:
        self.assertIsNone(pl.resolve_command("nope-agent-xyz", str(self.bin), extra_dirs=[str(self.fallback)]))

    def test_fallback_dirs_include_local_bin(self) -> None:
        dirs = pl.fallback_dirs({"APPDATA": "A", "LOCALAPPDATA": "L"})
        self.assertIn(os.path.join(os.path.expanduser("~"), ".local", "bin"), dirs)
        if sys.platform == "win32":
            self.assertIn(os.path.join("A", "npm"), dirs)


class PidTests(unittest.TestCase):
    def test_pid_alive(self) -> None:
        self.assertTrue(pl.pid_alive(os.getpid()))
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(pl.pid_alive(p.pid))
        self.assertFalse(pl.pid_alive(0))

    def test_pid_file_roundtrip(self) -> None:
        with TemporaryDirectory() as d:
            f = pl.pid_file_for(Path(d) / "w.json")
            self.assertEqual(f.name, "w.json.pids")
            self.assertEqual(pl.read_pid_file(f), [])
            pl.write_pid_file(f, launcher_pid=1, agent_pid=2)
            self.assertEqual(pl.read_pid_file(f), [1, 2])


class EnvFileAndArgvTests(unittest.TestCase):
    def test_roundtrip_and_launcher_argv(self) -> None:
        with TemporaryDirectory() as d:
            path = pl.write_env_file(Path(d) / "x" / "w·1.json", env={"A": "é"}, cwd="/c")
            env, cwd = pl.load_env_file(path)
            self.assertEqual(env, {"A": "é"})
            self.assertEqual(cwd, "/c")
            argv = pl.launcher_argv(path, ["claude", "--model", "x"])
        self.assertEqual(argv[1:3], ["-X", "utf8"])
        self.assertEqual(Path(argv[3]), pl.LAUNCHER_FILE)
        self.assertEqual(argv[4:7], ["--env-file", str(path), "--"])
        self.assertEqual(argv[7:], ["claude", "--model", "x"])

    def test_parse_args(self) -> None:
        self.assertEqual(pl._parse_args(["--env-file", "f", "--", "a", "--b"]), ("f", ["a", "--b"]))
        self.assertEqual(pl._parse_args(["--env-file=f"]), ("f", []))
        self.assertEqual(pl._parse_args(["a", "b"]), (None, ["a", "b"]))


class MainTests(unittest.TestCase):
    def test_main_runs_agent_with_env_and_cwd(self) -> None:
        with TemporaryDirectory() as d:
            work = Path(d) / "work"
            work.mkdir()
            env_file = pl.write_env_file(Path(d) / "e.json", env={"FLEET_TASK_ID": "7"}, cwd=str(work))
            captured = {}

            class Proc:
                pid = 4242

                def __init__(self, argv, env=None):
                    captured["argv"] = argv
                    captured["env"] = env
                    captured["cwd"] = os.getcwd()

                def wait(self):
                    return 3

            old = os.getcwd()
            try:
                with unittest.mock.patch.object(pl.subprocess, "Popen", Proc), \
                        unittest.mock.patch.object(pl.signal, "signal"), \
                        unittest.mock.patch.dict(os.environ, {"CLAUDECODE": "1"}):
                    rc = pl.main(["--env-file", str(env_file), "--", sys.executable, "-V"])
            finally:
                os.chdir(old)
            pids = pl.read_pid_file(pl.pid_file_for(env_file))
        self.assertEqual(rc, 3)
        self.assertEqual(pids, [os.getpid(), 4242])
        self.assertEqual(os.path.normcase(captured["argv"][0]), os.path.normcase(os.path.abspath(sys.executable)))
        self.assertEqual(captured["argv"][1:], ["-V"])
        self.assertEqual(captured["env"]["FLEET_TASK_ID"], "7")
        self.assertNotIn("CLAUDECODE", captured["env"])
        self.assertEqual(os.path.normcase(os.path.realpath(captured["cwd"])), os.path.normcase(os.path.realpath(work)))

    def test_main_ignores_sigint_after_the_spawn_on_posix_only(self) -> None:
        # An ignored signal survives exec: on POSIX the agent must start with
        # the default SIGINT disposition (Ctrl+C has to reach it).
        for windows, expected in ((False, ["popen", "sigint"]), (True, ["sigint", "popen"])):
            with self.subTest(windows=windows), TemporaryDirectory() as d:
                order: list[str] = []

                class Proc:
                    pid = 1

                    def __init__(self, argv, env=None):
                        order.append("popen")

                    def wait(self):
                        return 0

                with unittest.mock.patch.object(pl, "_is_windows", return_value=windows),                         unittest.mock.patch.object(pl.subprocess, "Popen", Proc),                         unittest.mock.patch.object(
                            pl.signal, "signal", side_effect=lambda *a: order.append("sigint")
                        ) as sig:
                    pl.main(["--", sys.executable, "-V"])
                sig.assert_called_once_with(pl.signal.SIGINT, pl.signal.SIG_IGN)
                self.assertEqual(order, expected)

    def test_main_missing_agent_holds_pane(self) -> None:
        with TemporaryDirectory() as d:
            env_file = pl.write_env_file(Path(d) / "e.json", env={"PATH": d}, cwd=None)
            with unittest.mock.patch.object(pl, "fallback_dirs", return_value=[]), \
                    unittest.mock.patch.object(pl, "_hold") as hold, \
                    unittest.mock.patch.object(pl.subprocess, "Popen") as call:
                rc = pl.main(["--env-file", str(env_file), "--", "no-such-agent-xyz"])
        self.assertEqual(rc, pl.EXIT_NOT_FOUND)
        call.assert_not_called()
        self.assertIn("agent CLI not found", hold.call_args.args[0])

    def test_main_bad_env_file_holds_pane(self) -> None:
        with unittest.mock.patch.object(pl, "_hold") as hold:
            rc = pl.main(["--env-file", "/definitely/missing.json", "--", "x"])
        self.assertEqual(rc, 1)
        hold.assert_called_once()

    def test_script_bootstrap_runs_without_pythonpath(self) -> None:
        """Run the launcher by file path with a clean PYTHONPATH (as zellij does)."""
        with TemporaryDirectory() as d:
            env_file = pl.write_env_file(Path(d) / "e.json", env={"FLEET_TASK_ID": "42"}, cwd=d)
            code = (
                "import os,sys; print(os.environ['FLEET_TASK_ID'], os.environ['PYTHONUTF8'], "
                "'CLAUDECODE' in os.environ, "
                "os.path.realpath(os.getcwd()) == os.path.realpath(sys.argv[1]))"
            )
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            env["CLAUDECODE"] = "1"
            r = subprocess.run(
                pl.launcher_argv(env_file, [sys.executable, "-c", code, d]),
                capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(ROOT),
                timeout=60,
            )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["42", "1", "False", "True"])


if __name__ == "__main__":
    unittest.main()
