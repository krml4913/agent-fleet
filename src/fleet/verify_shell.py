"""Resolve the optional ``verify.shell`` field into an argv for ``subprocess``.

A verify command normally runs with ``shell=True`` — ``/bin/sh`` on POSIX and
``cmd.exe`` on Windows — so POSIX-style commands fail on Windows. A stage's
``verify.shell`` picks another shell explicitly:

    ============  =========================================================
    ``bash``      Bash. On Windows this is **Git Bash**, never the WSL
                  launcher in ``System32``.
    ``sh``        POSIX ``sh``. On Windows, the one shipped with Git for
                  Windows.
    ``pwsh``      PowerShell 7+.
    ``powershell`` Windows PowerShell 5.1 (on POSIX, falls back to ``pwsh``,
                  the only PowerShell there).
    ``cmd``       ``cmd.exe`` — Windows only (same as the default there).
    ============  =========================================================

A shell that is not installed raises :class:`ShellUnavailableError` with a
message meant to be shown to the driver as-is.
"""
from __future__ import annotations

import ntpath
import os
import shutil
import sys
from pathlib import Path

SUPPORTED_SHELLS = ("bash", "sh", "pwsh", "powershell", "cmd")


class ShellUnavailableError(RuntimeError):
    """The requested verify shell cannot be used on this machine."""


def build_argv(shell: str | None, command: str) -> list[str] | None:
    """Return the argv that runs ``command`` under ``shell``.

    ``None`` means "use the platform default": ``subprocess.run(command,
    shell=True)``. That is returned for an unset ``shell`` and for ``cmd`` on
    Windows (``shell=True`` already is ``cmd.exe`` there, and going through it
    keeps cmd's own quoting rules intact instead of Python's argv quoting).

    Raises :class:`ShellUnavailableError` if the shell is unknown, missing, or
    not available on this OS.
    """
    if shell is None:
        return None
    if shell not in SUPPORTED_SHELLS:
        raise ShellUnavailableError(
            f"unsupported verify shell {shell!r}; "
            f"supported: {', '.join(SUPPORTED_SHELLS)}"
        )
    if shell == "cmd":
        if sys.platform != "win32":
            raise ShellUnavailableError(
                "verify shell 'cmd' is only available on Windows"
            )
        return None
    exe = _find_shell(shell)
    if exe is None:
        raise ShellUnavailableError(_missing_message(shell))
    if shell in ("bash", "sh"):
        return [exe, "-c", command]
    # pwsh / powershell: no profile, no prompts; the command is one string.
    return [exe, "-NoProfile", "-NonInteractive", "-Command", command]


def _find_shell(shell: str) -> str | None:
    if shell in ("bash", "sh"):
        return _find_posix_shell(shell)
    if shell == "pwsh":
        return shutil.which("pwsh")
    # powershell
    found = shutil.which("powershell")
    if found is None and sys.platform != "win32":
        found = shutil.which("pwsh")
    return found


def _find_posix_shell(name: str) -> str | None:
    if sys.platform != "win32":
        return shutil.which(name)
    # Windows: prefer Git for Windows' shell. ``shutil.which("bash")`` can hit
    # ``System32\bash.exe`` (the WSL launcher), which runs a Linux userland
    # that cannot see the Windows paths the verify command expects.
    for root in _git_roots():
        for rel in (("bin", f"{name}.exe"), ("usr", "bin", f"{name}.exe")):
            candidate = root.joinpath(*rel)
            if candidate.is_file():
                return str(candidate)
    for found in _which_all(name):
        if not _is_wsl_launcher(found):
            return found
    return None


def _git_roots() -> list[Path]:
    """Candidate Git for Windows install roots, most specific first."""
    roots: list[Path] = []
    git = shutil.which("git")
    if git:
        # ...\Git\cmd\git.exe, ...\Git\bin\git.exe, ...\Git\mingw64\bin\git.exe
        for parent in Path(git).resolve().parents[:3]:
            roots.append(parent)
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            roots.append(Path(base) / "Git")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs" / "Git")
    return roots


def _which_all(name: str) -> list[str]:
    """Every ``PATH`` hit for ``name``, in order (``shutil.which`` returns one)."""
    hits: list[str] = []
    seen: set[str] = set()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        found = shutil.which(name, path=directory)
        if found and os.path.normcase(found) not in seen:
            seen.add(os.path.normcase(found))
            hits.append(found)
    return hits


def _is_wsl_launcher(path: str) -> bool:
    # Windows-only logic, so use ntpath explicitly (testable on any host OS).
    system_root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    system_dirs = {
        ntpath.normcase(ntpath.join(system_root, sub))
        for sub in ("System32", "Sysnative")
    }
    return ntpath.normcase(ntpath.dirname(path)) in system_dirs


def _missing_message(shell: str) -> str:
    hints = {
        "bash": (
            "install Git for Windows (Git Bash) and make sure it is on PATH"
            if sys.platform == "win32"
            else "install bash and make sure it is on PATH"
        ),
        "sh": (
            "install Git for Windows (Git Bash) and make sure it is on PATH"
            if sys.platform == "win32"
            else "install sh and make sure it is on PATH"
        ),
        "pwsh": "install PowerShell 7+ (https://aka.ms/powershell) and make sure `pwsh` is on PATH",
        "powershell": (
            "make sure `powershell` is on PATH"
            if sys.platform == "win32"
            else "install PowerShell (`pwsh`) and make sure it is on PATH"
        ),
    }
    return (
        f"verify shell {shell!r} was requested (verify.shell) but was not found "
        f"on this machine; {hints[shell]}, or change/remove verify.shell in the "
        f"formation."
    )
