"""Tests for ``fleet preflight``."""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet"
sys.path.insert(0, str(ROOT / "src"))

from fleet.commands import preflight  # noqa: E402


class PreflightLibraryTests(unittest.TestCase):
    def test_check_all_returns_results(self) -> None:
        results = preflight.check_all()
        names = [r.name for r in results]
        self.assertIn("python", names)
        self.assertIn(preflight._mux_backend_name(), names)
        self.assertIn("git", names)

    def test_python_marked_required(self) -> None:
        results = {r.name: r for r in preflight.check_all()}
        self.assertTrue(results["python"].required)

    def test_optionals_dont_block(self) -> None:
        # git required-ness depends on workspace (may be true in git repos with worktree).
        # Only verify that the truly optional tools are optional.
        results = {r.name: r for r in preflight.check_all()}
        self.assertFalse(results["claude"].required)
        self.assertFalse(results["codex"].required)
        self.assertFalse(results["codex-update"].required)
        self.assertFalse(results["codex-trust"].required)

    def test_git_required_for_worktree_workspace(self) -> None:
        with (
            unittest.mock.patch(
                "fleet.commands.preflight.state_mod.resolve_state_dir",
                return_value=Path("/tmp/state"),
            ),
            unittest.mock.patch(
                "fleet.commands.preflight.workspace_mod.load",
                return_value="worktree",
            ),
        ):
            self.assertTrue(preflight._git_required_for_cwd(Path("/tmp/repo")))

    def test_git_optional_for_none_workspace(self) -> None:
        with (
            unittest.mock.patch(
                "fleet.commands.preflight.state_mod.resolve_state_dir",
                return_value=Path("/tmp/state"),
            ),
            unittest.mock.patch(
                "fleet.commands.preflight.workspace_mod.load",
                return_value="none",
            ),
        ):
            self.assertFalse(preflight._git_required_for_cwd(Path("/tmp/repo")))

    def test_codex_trust_warns_when_untrusted(self) -> None:
        with (
            unittest.mock.patch(
                "fleet.commands.preflight.shutil.which",
                return_value="/bin/codex",
            ),
            unittest.mock.patch(
                "fleet.commands.preflight._git_toplevel",
                return_value=Path("/tmp/repo"),
            ),
            unittest.mock.patch(
                "fleet.commands.preflight.agents_mod.codex_repo_trusted",
                return_value=False,
            ),
        ):
            result = preflight._check_codex_trust()

        self.assertEqual(result.name, "codex-trust")
        self.assertFalse(result.ok)
        self.assertFalse(result.required)
        self.assertIn("not trusted", result.detail)

    def test_codex_trust_skips_without_codex(self) -> None:
        with unittest.mock.patch("fleet.commands.preflight.shutil.which", return_value=None):
            result = preflight._check_codex_trust()

        self.assertTrue(result.ok)
        self.assertIn("skipped", result.detail)

    def test_codex_update_warns_when_latest_is_newer(self) -> None:
        with (
            unittest.mock.patch(
                "fleet.commands.preflight.shutil.which",
                side_effect=lambda name: f"/bin/{name}",
            ),
            unittest.mock.patch(
                "fleet.commands.preflight._codex_version",
                return_value="0.132.0",
            ),
            unittest.mock.patch(
                "fleet.commands.preflight._npm_latest_codex_version",
                return_value="0.133.0",
            ),
            unittest.mock.patch(
                "fleet.commands.preflight._npm_global_codex_version",
                return_value="0.133.0",
            ),
        ):
            result = preflight._check_codex_update()

        self.assertEqual(result.name, "codex-update")
        self.assertFalse(result.ok)
        self.assertFalse(result.required)
        self.assertIn("update prompt may appear", result.detail)
        self.assertIn("npm global @openai/codex is 0.133.0", result.detail)

    def test_codex_update_ok_when_current(self) -> None:
        with (
            unittest.mock.patch(
                "fleet.commands.preflight.shutil.which",
                side_effect=lambda name: f"/bin/{name}",
            ),
            unittest.mock.patch(
                "fleet.commands.preflight._codex_version",
                return_value="0.133.0",
            ),
            unittest.mock.patch(
                "fleet.commands.preflight._npm_latest_codex_version",
                return_value="0.133.0",
            ),
            unittest.mock.patch(
                "fleet.commands.preflight._npm_global_codex_version",
                return_value="0.133.0",
            ),
        ):
            result = preflight._check_codex_update()

        self.assertTrue(result.ok)
        self.assertIn("is current", result.detail)

    def test_codex_update_skips_without_codex(self) -> None:
        with unittest.mock.patch("fleet.commands.preflight.shutil.which", return_value=None):
            result = preflight._check_codex_update()

        self.assertTrue(result.ok)
        self.assertIn("skipped", result.detail)

    def test_extract_version_from_codex_output(self) -> None:
        self.assertEqual(preflight._extract_version("codex-cli 0.132.0"), "0.132.0")
        self.assertIsNone(preflight._extract_version("codex-cli dev"))


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class MuxBackendCheckTests(unittest.TestCase):
    def _name(self, *, env: dict[str, str], platform: str) -> str:
        with (
            unittest.mock.patch.dict(os.environ, env, clear=False),
            unittest.mock.patch("fleet.mux.sys.platform", platform),
        ):
            if "FLEET_MUX" not in env:
                os.environ.pop("FLEET_MUX", None)
            return preflight._mux_backend_name()

    def test_backend_name_from_env(self) -> None:
        self.assertEqual(self._name(env={"FLEET_MUX": " TMUX "}, platform="win32"), "tmux")
        self.assertEqual(self._name(env={"FLEET_MUX": "zellij"}, platform="linux"), "zellij")

    def test_backend_name_platform_default(self) -> None:
        self.assertEqual(self._name(env={}, platform="win32"), "zellij")
        self.assertEqual(self._name(env={}, platform="linux"), "tmux")
        self.assertEqual(self._name(env={}, platform="darwin"), "tmux")

    def test_backend_name_is_mux_backend_name(self) -> None:
        with unittest.mock.patch("fleet.mux.backend_name", return_value="zellij"):
            self.assertEqual(preflight._mux_backend_name(), "zellij")

    def _check_mux(self, backend: str, *, which: str | None, stdout: str = "", rc: int = 0):
        with (
            unittest.mock.patch(
                "fleet.commands.preflight._mux_backend_name", return_value=backend
            ),
            unittest.mock.patch("fleet.commands.preflight.shutil.which", return_value=which),
            unittest.mock.patch(
                "fleet.commands.preflight.subprocess.run",
                return_value=_completed(stdout, rc),
            ) as run,
        ):
            return preflight._check_mux(), run

    def test_tmux_uses_dash_v(self) -> None:
        result, run = self._check_mux("tmux", which="/usr/bin/tmux", stdout="tmux 3.4\n")
        self.assertEqual(run.call_args.args[0], ["tmux", "-V"])
        self.assertEqual(result.name, "tmux")
        self.assertTrue(result.ok)
        self.assertTrue(result.required)
        self.assertFalse(result.warn)
        self.assertEqual(result.detail, "tmux 3.4")

    def test_tmux_missing_is_required_failure(self) -> None:
        result, _ = self._check_mux("tmux", which=None)
        self.assertFalse(result.ok)
        self.assertTrue(result.required)

    def test_zellij_missing_is_required_failure(self) -> None:
        result, _ = self._check_mux("zellij", which=None)
        self.assertEqual(result.name, "zellij")
        self.assertFalse(result.ok)
        self.assertTrue(result.required)
        self.assertIn("not on PATH", result.detail)

    def test_zellij_below_minimum_fails(self) -> None:
        result, run = self._check_mux("zellij", which="/bin/zellij", stdout="zellij 0.44.3\n")
        self.assertEqual(run.call_args.args[0], ["zellij", "--version"])
        self.assertFalse(result.ok)
        self.assertTrue(result.required)
        self.assertIn("need >=0.45.0", result.detail)
        self.assertIn("--no-focus", result.detail)

    def test_zellij_045x_ok_with_workaround_note(self) -> None:
        for version in ("0.45.0", "0.45.1"):
            with self.subTest(version=version):
                result, _ = self._check_mux(
                    "zellij", which="/bin/zellij", stdout=f"zellij {version}\n"
                )
                self.assertTrue(result.ok)
                self.assertTrue(result.warn)
                self.assertIn(version, result.detail)
                self.assertIn("zellij#5594", result.detail)

    def test_zellij_newer_ok(self) -> None:
        result, _ = self._check_mux("zellij", which="/bin/zellij", stdout="zellij 0.46.0\n")
        self.assertTrue(result.ok)
        self.assertFalse(result.warn)
        self.assertEqual(result.detail, "0.46.0")

    def test_zellij_unparseable_version_warns(self) -> None:
        result, _ = self._check_mux("zellij", which="/bin/zellij", stdout="zellij dev\n")
        self.assertTrue(result.ok)
        self.assertTrue(result.warn)
        self.assertIn("could not parse", result.detail)

    def test_unknown_backend_fails(self) -> None:
        result, run = self._check_mux("screen", which="/bin/screen")
        run.assert_not_called()
        self.assertFalse(result.ok)
        self.assertTrue(result.required)
        self.assertIn("unknown FLEET_MUX", result.detail)

    def test_parse_version(self) -> None:
        self.assertEqual(preflight._parse_version("zellij 0.45.1"), (0, 45, 1))
        self.assertEqual(preflight._parse_version("zellij 0.46.0-rc1"), (0, 46, 0))
        self.assertIsNone(preflight._parse_version("zellij"))


class WindowsChecksTests(unittest.TestCase):
    def _check_all_names(self, platform: str) -> list[str]:
        ok = preflight.CheckResult("x", True, "", required=False)
        with (
            unittest.mock.patch("fleet.commands.preflight.sys.platform", platform),
            unittest.mock.patch("fleet.commands.preflight._check_python", return_value=ok._replace(name="python")),
            unittest.mock.patch("fleet.commands.preflight._check_mux", return_value=ok._replace(name="mux")),
            unittest.mock.patch("fleet.commands.preflight._check_command", return_value=ok._replace(name="git")),
            unittest.mock.patch("fleet.commands.preflight._check_clone_path", return_value=ok._replace(name="clone-path")),
            unittest.mock.patch("fleet.commands.preflight._check_longpaths", return_value=ok._replace(name="longpaths")),
            unittest.mock.patch("fleet.commands.preflight._check_fleet_agent_cmd", return_value=ok._replace(name="fleet-agent")),
            unittest.mock.patch("fleet.commands.preflight._check_agent_cli", side_effect=lambda n: ok._replace(name=n)),
            unittest.mock.patch("fleet.commands.preflight._check_codex_update", return_value=ok._replace(name="codex-update")),
            unittest.mock.patch("fleet.commands.preflight._check_codex_trust", return_value=ok._replace(name="codex-trust")),
            unittest.mock.patch("fleet.commands.preflight._git_required_for_cwd", return_value=False),
        ):
            return [r.name for r in preflight.check_all()]

    def test_windows_checks_only_on_windows(self) -> None:
        win = self._check_all_names("win32")
        for name in ("clone-path", "longpaths", "fleet-agent"):
            self.assertIn(name, win)
        linux = self._check_all_names("linux")
        for name in ("clone-path", "longpaths", "fleet-agent"):
            self.assertNotIn(name, linux)
        self.assertIn("claude", linux)
        self.assertIn("codex", linux)

    def test_clone_path_with_spaces_warns(self) -> None:
        with unittest.mock.patch(
            "fleet.commands.preflight._CLONE_ROOT", Path("C:/Users/Some One/agent-fleet")
        ):
            result = preflight._check_clone_path()
        self.assertFalse(result.ok)
        self.assertFalse(result.required)
        self.assertIn("spaces", result.detail)

    def test_clone_path_without_spaces_ok(self) -> None:
        with unittest.mock.patch(
            "fleet.commands.preflight._CLONE_ROOT", Path("D:/dev/agent-fleet")
        ):
            result = preflight._check_clone_path()
        self.assertTrue(result.ok)

    def _longpaths(self, stdout: str, rc: int = 0):
        with (
            unittest.mock.patch("fleet.commands.preflight.shutil.which", return_value="/bin/git"),
            unittest.mock.patch(
                "fleet.commands.preflight.subprocess.run",
                return_value=_completed(stdout, rc),
            ) as run,
        ):
            return preflight._check_longpaths(), run

    def test_longpaths_true_ok(self) -> None:
        result, run = self._longpaths("true\n")
        self.assertEqual(run.call_args.args[0], ["git", "config", "--get", "core.longpaths"])
        self.assertTrue(result.ok)

    def test_longpaths_unset_warns_with_fix(self) -> None:
        result, _ = self._longpaths("", rc=1)
        self.assertFalse(result.ok)
        self.assertFalse(result.required)
        self.assertIn("unset", result.detail)
        self.assertIn("git config --global core.longpaths true", result.detail)

    def test_longpaths_false_warns(self) -> None:
        result, _ = self._longpaths("false\n")
        self.assertFalse(result.ok)
        self.assertIn("core.longpaths is false", result.detail)

    def test_fleet_agent_cmd_present_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with unittest.mock.patch("fleet.commands.preflight._CLONE_ROOT", root):
                missing = preflight._check_fleet_agent_cmd()
                (root / "fleet-agent.cmd").write_text("@echo off\n", encoding="utf-8")
                present = preflight._check_fleet_agent_cmd()
        self.assertFalse(missing.ok)
        self.assertTrue(missing.required)
        self.assertIn("missing", missing.detail)
        self.assertTrue(present.ok)
        self.assertTrue(present.detail.endswith("fleet-agent.cmd"))


class AgentCliTests(unittest.TestCase):
    def test_resolved_path_reported(self) -> None:
        exe = os.path.abspath("/opt/bin/claude")
        with (
            unittest.mock.patch("fleet.commands.preflight.shutil.which", return_value=exe),
            unittest.mock.patch(
                "fleet.commands.preflight.subprocess.run",
                return_value=_completed("2.1.0 (Claude Code)\n"),
            ) as run,
        ):
            result = preflight._check_agent_cli("claude")
        self.assertEqual(run.call_args.args[0], [exe, "--version"])
        self.assertTrue(result.ok)
        self.assertFalse(result.warn)
        self.assertFalse(result.required)
        self.assertIn(exe, result.detail)
        self.assertIn("2.1.0", result.detail)

    def test_missing_off_windows_has_no_fallback(self) -> None:
        with (
            unittest.mock.patch("fleet.commands.preflight.sys.platform", "linux"),
            unittest.mock.patch("fleet.commands.preflight.shutil.which", return_value=None) as which,
        ):
            result = preflight._check_agent_cli("codex")
        which.assert_called_once_with("codex")
        self.assertFalse(result.ok)
        self.assertFalse(result.required)
        self.assertEqual(result.detail, "not on PATH")

    def _windows_fallback(self, env: dict[str, str], hit_dir: str):
        def fake_which(name, path=None):
            if path is None:
                return None
            if os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(hit_dir)):
                return os.path.join(path, f"{name}.exe")
            return None

        with (
            unittest.mock.patch("fleet.commands.preflight.sys.platform", "win32"),
            unittest.mock.patch.dict(os.environ, env, clear=False),
            unittest.mock.patch("fleet.commands.preflight.shutil.which", side_effect=fake_which),
            unittest.mock.patch(
                "fleet.commands.preflight.subprocess.run",
                return_value=_completed("2.1.0 (Claude Code)\n"),
            ),
        ):
            return preflight._check_agent_cli("claude")

    def test_windows_fallback_userprofile_local_bin(self) -> None:
        profile = os.path.abspath("/home/someone")
        local_bin = os.path.join(profile, ".local", "bin")
        result = self._windows_fallback(
            {"USERPROFILE": profile, "PATH": os.pathsep.join(["/nowhere"])}, local_bin
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.warn)
        self.assertIn(os.path.join(local_bin, "claude.exe"), result.detail)
        self.assertIn("found only via fallback", result.detail)
        self.assertIn("not on PATH", result.detail)

    def test_windows_fallback_tilde_path_entry(self) -> None:
        home = os.path.abspath("/home/tilde")
        tools = os.path.join(home, "tools")
        with unittest.mock.patch(
            "fleet.commands.preflight.os.path.expanduser",
            side_effect=lambda p: p.replace("~", home, 1),
        ):
            result = self._windows_fallback(
                {
                    "USERPROFILE": os.path.abspath("/other"),
                    "PATH": os.pathsep.join(["/nowhere", "~/tools"]),
                },
                tools,
            )
        self.assertTrue(result.ok)
        self.assertTrue(result.warn)
        self.assertIn("found only via fallback", result.detail)

    def test_windows_not_found_anywhere(self) -> None:
        result = self._windows_fallback(
            {"USERPROFILE": os.path.abspath("/home/x"), "PATH": "/nowhere"},
            os.path.abspath("/elsewhere"),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "not on PATH")


class PreflightRunOutputTests(unittest.TestCase):
    def _run(self, results):
        out = io.StringIO()
        with (
            unittest.mock.patch("fleet.commands.preflight.check_all", return_value=results),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            rc = preflight.run(argparse.Namespace())
        return rc, out.getvalue()

    def test_warn_is_ok_and_marked(self) -> None:
        rc, out = self._run([
            preflight.CheckResult("zellij", True, "0.45.1 (workaround)", required=True, warn=True),
            preflight.CheckResult("longpaths", False, "unset", required=False),
        ])
        self.assertEqual(rc, 0)
        self.assertIn("⚠ zellij", out)
        self.assertIn("⚠ longpaths", out)
        self.assertIn("preflight: ok", out)

    def test_required_failure_exits_1(self) -> None:
        rc, out = self._run([
            preflight.CheckResult("zellij", False, "0.44.3 (need >=0.45.0)", required=True),
        ])
        self.assertEqual(rc, 1)
        self.assertIn("✘ zellij", out)


class PreflightCmdSmokeTest(unittest.TestCase):
    """The CLI itself should at least run; exit code depends on the host."""

    def test_cli_runs(self) -> None:
        r = subprocess.run(
            [sys.executable, str(FLEET), "preflight"],
            capture_output=True,
            text=True, encoding="utf-8",
        )
        self.assertIn("python", r.stdout)
        # Exit 0 or 1 depending on the host; just assert it ran.
        self.assertIn(r.returncode, (0, 1))


if __name__ == "__main__":
    unittest.main()
