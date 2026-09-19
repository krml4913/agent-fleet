"""Tests for fleet.verify_shell: mapping ``verify.shell`` to a subprocess argv.

Hermetic: ``shutil.which`` / the platform are patched, so no shell needs to be
installed and no multiplexer is touched. The last class runs a real shell when
one is available on the test machine.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fleet import verify_shell  # noqa: E402
from fleet.verify_shell import ShellUnavailableError, build_argv  # noqa: E402


def _patch_platform(name: str):
    return unittest.mock.patch.object(verify_shell.sys, "platform", name)


class BuildArgvTests(unittest.TestCase):
    def test_unset_shell_uses_platform_default(self) -> None:
        self.assertIsNone(build_argv(None, "echo hi"))

    def test_unknown_shell_is_rejected(self) -> None:
        with self.assertRaises(ShellUnavailableError) as ctx:
            build_argv("zsh", "echo hi")
        self.assertIn("unsupported verify shell 'zsh'", str(ctx.exception))
        for name in verify_shell.SUPPORTED_SHELLS:
            self.assertIn(name, str(ctx.exception))

    def test_bash_and_sh_run_command_with_dash_c(self) -> None:
        with _patch_platform("linux"), unittest.mock.patch.object(
            verify_shell.shutil, "which", side_effect=lambda n, **kw: f"/usr/bin/{n}"
        ):
            self.assertEqual(
                build_argv("bash", "a && b"), ["/usr/bin/bash", "-c", "a && b"]
            )
            self.assertEqual(build_argv("sh", "a && b"), ["/usr/bin/sh", "-c", "a && b"])

    def test_pwsh_and_powershell_run_command_without_profile(self) -> None:
        with _patch_platform("win32"), unittest.mock.patch.object(
            verify_shell.shutil, "which", side_effect=lambda n, **kw: f"C:/ps/{n}.exe"
        ):
            for name in ("pwsh", "powershell"):
                self.assertEqual(
                    build_argv(name, "Get-Date"),
                    [f"C:/ps/{name}.exe", "-NoProfile", "-NonInteractive",
                     "-Command", "Get-Date"],
                )

    def test_powershell_falls_back_to_pwsh_off_windows(self) -> None:
        def which(name, **kw):
            return "/opt/pwsh" if name == "pwsh" else None

        with _patch_platform("linux"), unittest.mock.patch.object(
            verify_shell.shutil, "which", side_effect=which
        ):
            self.assertEqual(build_argv("powershell", "x")[0], "/opt/pwsh")

    def test_powershell_does_not_fall_back_to_pwsh_on_windows(self) -> None:
        def which(name, **kw):
            return "C:/pwsh.exe" if name == "pwsh" else None

        with _patch_platform("win32"), unittest.mock.patch.object(
            verify_shell.shutil, "which", side_effect=which
        ):
            with self.assertRaises(ShellUnavailableError):
                build_argv("powershell", "x")

    def test_cmd_is_the_default_shell_on_windows(self) -> None:
        with _patch_platform("win32"):
            self.assertIsNone(build_argv("cmd", "dir"))

    def test_cmd_off_windows_is_an_error(self) -> None:
        with _patch_platform("linux"):
            with self.assertRaises(ShellUnavailableError) as ctx:
                build_argv("cmd", "dir")
        self.assertIn("only available on Windows", str(ctx.exception))

    def test_missing_shell_error_names_shell_and_field(self) -> None:
        with _patch_platform("linux"), unittest.mock.patch.object(
            verify_shell.shutil, "which", return_value=None
        ):
            for name in ("bash", "sh", "pwsh", "powershell"):
                with self.assertRaises(ShellUnavailableError) as ctx:
                    build_argv(name, "x")
                msg = str(ctx.exception)
                self.assertIn(repr(name), msg)
                self.assertIn("verify.shell", msg)
                self.assertIn("not found", msg)


class WindowsBashResolutionTests(unittest.TestCase):
    """On Windows, bash must be Git Bash, never the System32 WSL launcher."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _touch(self, *parts: str) -> str:
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return str(path)

    def _resolve(self, name: str, *, roots: list[Path], hits: list[str]):
        with _patch_platform("win32"), unittest.mock.patch.object(
            verify_shell, "_git_roots", return_value=roots
        ), unittest.mock.patch.object(
            verify_shell, "_which_all", return_value=hits
        ), unittest.mock.patch.dict(
            verify_shell.os.environ, {"SystemRoot": "C:\\Windows"}
        ):
            return verify_shell._find_posix_shell(name)

    def test_prefers_git_root_bin_over_path(self) -> None:
        bash = self._touch("Git", "bin", "bash.exe")
        self.assertEqual(
            self._resolve("bash", roots=[self.root / "Git"], hits=["C:\\other\\bash.exe"]),
            bash,
        )

    def test_finds_sh_under_usr_bin(self) -> None:
        sh = self._touch("Git", "usr", "bin", "sh.exe")
        self.assertEqual(self._resolve("sh", roots=[self.root / "Git"], hits=[]), sh)

    def test_skips_system32_wsl_launcher(self) -> None:
        got = self._resolve(
            "bash",
            roots=[],
            hits=["C:\\Windows\\System32\\bash.exe", "D:\\tools\\bash.exe"],
        )
        self.assertEqual(got, "D:\\tools\\bash.exe")

    def test_only_wsl_launcher_means_missing(self) -> None:
        got = self._resolve("bash", roots=[], hits=["C:\\Windows\\System32\\bash.exe"])
        self.assertIsNone(got)


class RealShellTests(unittest.TestCase):
    """Run a real shell when this machine has one (skipped otherwise)."""

    def _run(self, shell: str, command: str) -> subprocess.CompletedProcess:
        try:
            argv = build_argv(shell, command)
        except ShellUnavailableError:
            self.skipTest(f"{shell} is not available here")
        return subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60
        )

    def test_bash_runs_posix_syntax_and_propagates_exit_code(self) -> None:
        r = self._run("bash", 'echo "a b" && exit 3')
        self.assertEqual(r.returncode, 3)
        self.assertIn(b"a b", r.stdout)

    def test_sh_runs_command(self) -> None:
        r = self._run("sh", "echo 'q q' && exit 0")
        self.assertEqual(r.returncode, 0)
        self.assertIn(b"q q", r.stdout)

    def test_pwsh_propagates_exit_code(self) -> None:
        r = self._run("pwsh", 'Write-Output "x y"; exit 5')
        self.assertEqual(r.returncode, 5)
        self.assertIn(b"x y", r.stdout)


if __name__ == "__main__":
    unittest.main()
