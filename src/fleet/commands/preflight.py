"""``fleet preflight`` — environment dependency check.

Verifies the toolbelt fleet relies on: Python version, the configured
multiplexer backend (tmux / zellij, with a version gate for zellij), workspace
dependencies, and the agent CLIs (claude / codex, resolved to absolute paths).
On Windows it also checks the clone path, ``core.longpaths`` and the
``fleet-agent.cmd`` shim. Required tools missing → exit 1; optional tools
missing → warn but continue.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from .. import agents as agents_mod
from .. import mux
from .. import workspace as workspace_mod
# Constants only: importing the backend module runs no zellij command.
from ..mux import zellij as zellij_mux
from .. import state as state_mod


class CheckResult(NamedTuple):
    name: str
    ok: bool
    detail: str
    required: bool
    # ok but worth a look (printed with ⚠, never affects the exit code)
    warn: bool = False


# src/fleet/commands/preflight.py → parents[0]=commands, [1]=fleet, [2]=src, [3]=clone root
_CLONE_ROOT = Path(__file__).resolve().parents[3]

# zellij: 0.44.x lacks `new-tab --no-focus`; versions below
# FIXED_DETACHED_TAB_VERSION get the detached new-tab workaround for
# zellij#5594 (docs/windows-support.md §4.3 / §6.6). Both come from the
# backend so the preflight warning and the backend's behavior cannot drift.
ZELLIJ_MIN_VERSION = zellij_mux.MIN_VERSION
ZELLIJ_WORKAROUND_BELOW_VERSION = zellij_mux.FIXED_DETACHED_TAB_VERSION


def _version_str(version: tuple[int, ...]) -> str:
    return ".".join(str(n) for n in version)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "preflight",
        help="Check environment dependencies",
        description=(
            "Verify Python >=3.11, the multiplexer (zellij by default; tmux via "
            "FLEET_MUX or `fleet config set mux tmux`) and show which backend "
            "is selected and where that choice came from (env / config / "
            "default), git, and the agent CLIs (claude / codex); on Windows also the "
            "clone path, core.longpaths and fleet-agent.cmd. Optional tools "
            "missing → warn; required tools missing → exit 1."
        ),
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    results = check_all()
    rc = 0
    for r in results:
        if r.ok:
            mark = "⚠" if r.warn else "✔"
        else:
            mark = "✘" if r.required else "⚠"
        kind = "required" if r.required else "optional"
        print(f"  {mark} {r.name:<8} {kind:<8} {r.detail}")
        if r.required and not r.ok:
            rc = 1
    print()
    if rc == 0:
        print("preflight: ok")
    else:
        print("preflight: missing required tool(s)", file=sys.stderr)
    return rc


def check_all() -> list[CheckResult]:
    git_required = _git_required_for_cwd(Path.cwd())
    results = [
        _check_python(),
        _check_mux_selection(),
        _check_mux(),
        _check_command("git", ["git", "--version"], required=git_required),
    ]
    if _is_windows():
        results += [
            _check_clone_path(),
            _check_longpaths(),
            _check_fleet_agent_cmd(),
        ]
    results += [
        _check_agent_cli("claude"),
        _check_agent_cli("codex"),
        _check_codex_update(),
        _check_codex_trust(),
    ]
    return results


def _is_windows() -> bool:
    return sys.platform == "win32"


def _check_python() -> CheckResult:
    ok = sys.version_info >= (3, 11)
    detail = (
        f"{sys.version.split()[0]} (need >=3.11)"
        if not ok
        else f"{sys.version.split()[0]}"
    )
    return CheckResult("python", ok, detail, required=True)


def _mux_backend_name() -> str:
    """The selected multiplexer backend name, without importing a backend.

    ``FLEET_MUX``, else the global config's ``mux``, else the built-in default
    (zellij) — see :func:`fleet.mux.backend_name`.
    """
    return mux.backend_name()


def _check_mux_selection() -> CheckResult:
    """Report which backend is selected and where that choice came from.

    ``source`` is ``env`` (``FLEET_MUX``), ``config`` (``fleet config set mux``)
    or ``default``. The default is zellij on every platform, so the default case
    also names the two ways back to tmux.
    """
    name, source = mux.backend_selection()
    detail = f"{name} (from {source})"
    if source == "default":
        detail += "; for tmux: `fleet config set mux tmux` or FLEET_MUX=tmux"
    return CheckResult("mux", True, detail, required=False)


def _check_mux() -> CheckResult:
    """Check the selected multiplexer backend's binary (and version)."""
    name = _mux_backend_name()
    if name == "tmux":
        return _check_command("tmux", ["tmux", "-V"], required=True)
    if name == "zellij":
        return _check_zellij()
    return CheckResult(
        name,
        False,
        f"unknown FLEET_MUX={name!r} (expected one of: {', '.join(mux.BACKENDS)})",
        required=True,
    )


def _check_zellij() -> CheckResult:
    base = _check_command("zellij", ["zellij", "--version"], required=True)
    if not base.ok:
        return base
    version = _parse_version(base.detail)
    if version is None:
        return base._replace(
            detail=f"{base.detail} (could not parse version; need >={_version_str(ZELLIJ_MIN_VERSION)})",
            warn=True,
        )
    shown = _version_str(version)
    if version < ZELLIJ_MIN_VERSION:
        return base._replace(
            ok=False,
            detail=(
                f"{shown} (need >={_version_str(ZELLIJ_MIN_VERSION)}: "
                "older zellij lacks `new-tab --no-focus`)"
            ),
        )
    if version < ZELLIJ_WORKAROUND_BELOW_VERSION:
        return base._replace(
            detail=f"{shown} (detached new-tab workaround for zellij#5594 active)",
            warn=True,
        )
    return base._replace(detail=shown)


def _parse_version(text: str) -> tuple[int, ...] | None:
    """First ``N.N[.N…]`` in ``text`` as an int tuple (pre-release tags ignored)."""
    m = re.search(r"\d+(?:\.\d+)+", text)
    if not m:
        return None
    return tuple(int(part) for part in m.group(0).split("."))


def _check_clone_path() -> CheckResult:
    """Windows: the clone path must not contain spaces (docs/windows-support.md §5 #9)."""
    root = str(_CLONE_ROOT)
    if " " in root:
        return CheckResult(
            "clone-path",
            False,
            f"{root} contains spaces; prompts embed the fleet-agent path "
            "unquoted, so agents cannot run it — clone to a path without spaces",
            required=False,
        )
    return CheckResult("clone-path", True, root, required=False)


def _check_longpaths() -> CheckResult:
    """Windows: deep worktree paths need ``core.longpaths`` (§5 #14)."""
    fix = "run `git config --global core.longpaths true`"
    if not shutil.which("git"):
        return CheckResult("longpaths", True, "skipped (git not on PATH)", required=False)
    try:
        r = subprocess.run(
            ["git", "config", "--get", "core.longpaths"],
            cwd=_CLONE_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return CheckResult("longpaths", False, f"{type(e).__name__}; {fix}", required=False)
    value = r.stdout.strip().lower() if r.returncode == 0 else ""
    if value in ("true", "yes", "on", "1"):
        return CheckResult("longpaths", True, "core.longpaths=true", required=False)
    shown = value or "unset"
    return CheckResult(
        "longpaths",
        False,
        f"core.longpaths is {shown}; deep worktree paths may exceed MAX_PATH — {fix}",
        required=False,
    )


def _check_fleet_agent_cmd() -> CheckResult:
    """Windows: agents invoke the ``fleet-agent.cmd`` shim (§5 #9)."""
    shim = _CLONE_ROOT / "fleet-agent.cmd"
    if shim.is_file():
        return CheckResult("fleet-agent", True, shim.as_posix(), required=True)
    return CheckResult(
        "fleet-agent",
        False,
        f"{shim.as_posix()} missing (agents cannot signal the orchestrator)",
        required=True,
    )


def _resolve_cli(name: str) -> tuple[str | None, bool]:
    """Resolve ``name`` to an absolute path; return ``(path, via_fallback)``.

    On Windows, when ``PATH`` lookup fails, also try ``%USERPROFILE%/.local/bin``
    (the claude installer's target) and ``PATH`` entries starting with ``~``
    (expanded — Windows never expands them, so PowerShell / cmd miss them).
    """
    found = shutil.which(name)
    if found:
        return os.path.abspath(found), False
    if not _is_windows():
        return None, False
    for directory in _fallback_dirs():
        hit = shutil.which(name, path=directory)
        if hit:
            return os.path.abspath(hit), True
    return None, False


def _fallback_dirs() -> list[str]:
    dirs: list[str] = []
    profile = os.environ.get("USERPROFILE")
    if profile:
        dirs.append(os.path.join(profile, ".local", "bin"))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if entry.startswith("~"):
            dirs.append(os.path.expanduser(entry))
    seen: set[str] = set()
    unique: list[str] = []
    for d in dirs:
        key = os.path.normcase(os.path.normpath(d))
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def _check_agent_cli(name: str) -> CheckResult:
    """Optional agent CLI: report its resolved absolute path (and version)."""
    path, via_fallback = _resolve_cli(name)
    if path is None:
        return CheckResult(name, False, "not on PATH", required=False)
    result = _check_command(name, [path, "--version"], required=False, which=path)
    detail = f"{result.detail} ({path})"
    if result.ok and via_fallback:
        return result._replace(
            detail=f"{detail}; found only via fallback, not on PATH — "
            "agent panes may not find it (add its directory to PATH)",
            warn=True,
        )
    return result._replace(detail=detail)


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return f"signal {signum}"


def _check_command(
    name: str,
    version_argv: list[str],
    *,
    required: bool,
    which: str | None = None,
) -> CheckResult:
    if not (which or shutil.which(name)):
        return CheckResult(name, False, "not on PATH", required)
    cmd = " ".join([name, *version_argv[1:]])
    try:
        r = subprocess.run(
            version_argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except FileNotFoundError as e:
        return CheckResult(name, False, f"{type(e).__name__}", required)
    except subprocess.TimeoutExpired as e:
        timeout = f"{e.timeout:g}" if e.timeout is not None else "5"
        return CheckResult(
            name, False, f"timed out after {timeout}s running `{cmd}`", required
        )
    if r.returncode < 0:
        detail = f"killed by {_signal_name(-r.returncode)} running `{cmd}`"
        if sys.platform == "darwin":
            detail += (
                "; macOS may have blocked the binary (quarantine / code "
                "signing) — installing via Homebrew avoids this"
            )
        return CheckResult(name, False, detail, required)
    if r.returncode != 0:
        detail = f"exit {r.returncode} running `{cmd}`"
        stderr_line = (r.stderr or "").strip().splitlines()[0] if r.stderr else ""
        if stderr_line:
            detail += f": {stderr_line}"
        return CheckResult(name, False, detail, required)
    detail = (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr) else "found"
    return CheckResult(name, True, detail, required)


def _git_required_for_cwd(cwd: Path) -> bool:
    state_dir = state_mod.resolve_state_dir(cwd)
    if state_dir is None:
        return False
    try:
        return workspace_mod.load(state_dir) == "worktree"
    except Exception:
        return False


def _git_toplevel(cwd: Path) -> Path | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    root = r.stdout.strip()
    return Path(root).resolve() if root else None


def _check_codex_trust() -> CheckResult:
    if not shutil.which("codex"):
        return CheckResult(
            "codex-trust",
            True,
            "skipped (codex not on PATH)",
            required=False,
        )

    repo_root = _git_toplevel(Path.cwd())
    if repo_root is None:
        return CheckResult(
            "codex-trust",
            True,
            "skipped (not in a git repo)",
            required=False,
        )

    if agents_mod.codex_repo_trusted(repo_root):
        return CheckResult("codex-trust", True, f"trusted: {repo_root}", required=False)
    return CheckResult(
        "codex-trust",
        False,
        f"not trusted: {repo_root} (run `codex` here and approve)",
        required=False,
    )


def _check_codex_update() -> CheckResult:
    codex_path = shutil.which("codex")
    if not codex_path:
        return CheckResult(
            "codex-update",
            True,
            "skipped (codex not on PATH)",
            required=False,
        )

    current = _codex_version()
    if current is None:
        return CheckResult(
            "codex-update",
            False,
            "could not parse `codex --version`",
            required=False,
        )

    latest = _npm_latest_codex_version()
    npm_global = _npm_global_codex_version()

    details: list[str] = []
    ok = True
    if latest and _version_lt(current, latest):
        ok = False
        details.append(
            f"{current} < latest {latest}; update prompt may appear"
        )
    elif latest:
        details.append(f"{current} is current")
    else:
        details.append(f"{current}; latest unavailable")

    if npm_global and npm_global != current:
        ok = False
        details.append(
            f"npm global @openai/codex is {npm_global} but PATH runs {codex_path}"
        )

    if not ok:
        details.append("fleet launches codex with update checks disabled")
    return CheckResult("codex-update", ok, "; ".join(details), required=False)


def _codex_version() -> str | None:
    try:
        r = subprocess.run(
            ["codex", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    text = (r.stdout or r.stderr).strip().splitlines()
    if not text:
        return None
    return _extract_version(text[0])


def _npm_latest_codex_version() -> str | None:
    if not shutil.which("npm"):
        return None
    try:
        r = subprocess.run(
            ["npm", "view", "@openai/codex", "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return _extract_version(r.stdout.strip())


def _npm_global_codex_version() -> str | None:
    if not shutil.which("npm"):
        return None
    try:
        r = subprocess.run(
            ["npm", "root", "-g"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    root = r.stdout.strip()
    if not root:
        return None
    package_json = Path(root) / "@openai" / "codex" / "package.json"
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version")
    return _extract_version(version) if isinstance(version, str) else None


def _extract_version(text: str) -> str | None:
    for part in text.replace(",", " ").split():
        candidate = part.strip()
        if _version_tuple(candidate) is not None:
            return candidate
    return None


def _version_lt(left: str, right: str) -> bool:
    left_tuple = _version_tuple(left)
    right_tuple = _version_tuple(right)
    if left_tuple is None or right_tuple is None:
        return False
    return left_tuple < right_tuple


def _version_tuple(version: str) -> tuple[int, ...] | None:
    parts = version.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)
