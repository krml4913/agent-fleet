"""Pane launcher: start an agent CLI in a multiplexer pane with fleet's env.

zellij has no per-pane environment (no ``tmux new-window -e``): every pane
inherits the zellij *server's* environment, i.e. the environment of whichever
process created the session (docs/windows-support.md §4.4). So instead of
running the agent CLI directly, a zellij pane runs::

    <python> -X utf8 <clone>/src/fleet/pane_launch.py --env-file <json> -- <agent argv…>

and this launcher

1. starts from the inherited environment, **strips** the agent-session
   markers a creating agent leaks into it (``CLAUDECODE``, ``CLAUDE_PID``,
   ``CLAUDE_EFFORT``, ``AI_AGENT``, ``CLAUDE_CODE_*`` except user configuration such as
   ``CLAUDE_CODE_USE_BEDROCK``; ``CLAUDE_CONFIG_DIR`` and ``ANTHROPIC_*``
   are kept),
2. applies the per-pane env from the JSON env file (``FLEET_*``, ``PATH``…),
   prefixes ``PATH`` with the clone root and sets ``PYTHONUTF8=1`` /
   ``MSYS_NO_PATHCONV=1``,
3. changes into the requested working directory,
4. resolves the agent CLI to an absolute path under that ``PATH`` (expanding
   ``~`` entries, then falling back to well-known install dirs such as
   ``~/.local/bin``) — on failure it prints a clear error and keeps the pane
   open until Enter is pressed,
5. runs the agent inheriting the console and returns its exit code.

With no agent argv the launcher starts the platform shell instead.

The module is **stdlib-only at import time** and is started by file path, so
it does not depend on ``PYTHONPATH`` in the zellij server's environment. When
run as a script it first swaps its own directory (``src/fleet``) on
``sys.path`` for ``src`` (and adds ``vendor``) so a stray module name in
``src/fleet`` can never shadow the standard library.
"""
from __future__ import annotations

import os
import sys

if __name__ == "__main__":  # pragma: no cover - exercised by the live tests
    _here = os.path.dirname(os.path.abspath(__file__))
    if sys.path and os.path.normcase(os.path.abspath(sys.path[0] or ".")) == os.path.normcase(_here):
        sys.path[0] = os.path.dirname(_here)
    sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(_here)), "vendor"))

import json  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Iterable, Mapping, Sequence  # noqa: E402

#: This file — what a pane command runs.
LAUNCHER_FILE = Path(__file__).resolve()
#: ``src/fleet/pane_launch.py`` → parents[0]=fleet, [1]=src, [2]=clone root.
CLONE_ROOT = LAUNCHER_FILE.parents[2]

ENV_FILE_VERSION = 1

#: Exit code when the agent CLI cannot be resolved (shell convention).
EXIT_NOT_FOUND = 127

#: Variables a parent agent session (Claude Code) sets for its own children.
#: Inherited by a pane, they make a claude started there think it is a child
#: session (``⚠ Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION``),
#: which breaks the usage accounting that reads claude's session JSONL.
#: ``CLAUDE_EFFORT`` is the creating session's effort level: inherited, it
#: silently forces that level onto every driver / leader pane. ``AI_AGENT``
#: names the creating agent (``claude-code_<version>_agent``) and would tell
#: tools in e.g. a codex pane that they run under that claude session.
MARKER_VARS: frozenset[str] = frozenset(
    {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "AI_AGENT"}
)
MARKER_PREFIXES: tuple[str, ...] = ("CLAUDE_CODE_",)

#: ``CLAUDE_CODE_*`` variables that are *user configuration*, not session
#: markers. They are kept so e.g. Bedrock / Vertex users or a custom Git Bash
#: path keep working in fleet panes.
KEEP_CLAUDE_CODE_VARS: frozenset[str] = frozenset(
    {
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
        "CLAUDE_CODE_SKIP_VERTEX_AUTH",
        "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_GIT_BASH_PATH",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
        "CLAUDE_CODE_CLIENT_CERT",
        "CLAUDE_CODE_CLIENT_KEY",
        "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE",
    }
)

#: Always set in the pane environment.
FIXED_ENV: dict[str, str] = {"PYTHONUTF8": "1", "MSYS_NO_PATHCONV": "1"}


def _is_windows() -> bool:
    return sys.platform == "win32"


# ---------------------------------------------------------------------------
# Building the pane command (called by the backend, inside the fleet process)
# ---------------------------------------------------------------------------


def write_env_file(
    path: str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None,
    cwd: str | None,
) -> Path:
    """Write the launcher's JSON env file and return its path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": ENV_FILE_VERSION,
        "env": {str(k): str(v) for k, v in (env or {}).items()},
        "cwd": str(cwd) if cwd else None,
    }
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def launcher_argv(env_file: str | os.PathLike[str], argv: Sequence[str] | None) -> list[str]:
    """The pane command: this launcher run by the current interpreter."""
    return [
        _python_executable(),
        "-X",
        "utf8",
        str(LAUNCHER_FILE),
        "--env-file",
        str(env_file),
        "--",
        *[str(a) for a in (argv or [])],
    ]


def _python_executable() -> str:
    exe = sys.executable or "python"
    # A pane needs a console interpreter; ``pythonw.exe`` has no console.
    if _is_windows():
        base = os.path.basename(exe).lower()
        if base == "pythonw.exe":
            candidate = os.path.join(os.path.dirname(exe), "python.exe")
            if os.path.isfile(candidate):
                return candidate
    return exe


# ---------------------------------------------------------------------------
# Environment handling (inside the pane)
# ---------------------------------------------------------------------------


def is_agent_marker(name: str) -> bool:
    """True for an inherited agent-session marker the launcher must strip."""
    key = name.upper() if _is_windows() else name
    if key in MARKER_VARS:
        return True
    if key in KEEP_CLAUDE_CODE_VARS:
        return False
    return any(key.startswith(prefix) for prefix in MARKER_PREFIXES)


def strip_agent_markers(env: Mapping[str, str]) -> dict[str, str]:
    """Return ``env`` without the inherited agent-session markers."""
    return {k: v for k, v in env.items() if not is_agent_marker(k)}


def _env_get(env: Mapping[str, str], name: str) -> tuple[str | None, str | None]:
    """Case-insensitive (on Windows) lookup → ``(actual_key, value)``."""
    if name in env:
        return name, env[name]
    if _is_windows():
        upper = name.upper()
        for k, v in env.items():
            if k.upper() == upper:
                return k, v
    return None, None


def _env_set(env: dict[str, str], name: str, value: str) -> None:
    key, _ = _env_get(env, name)
    if key is not None and key != name:
        del env[key]
    env[name] = value


def split_path(value: str | None) -> list[str]:
    """Split a ``PATH`` value into entries, expanding ``~`` in each entry."""
    if not value:
        return []
    out: list[str] = []
    for entry in value.split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry:
            continue
        out.append(os.path.expanduser(entry))
    return out


def _same_dir(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def build_env(
    inherited: Mapping[str, str],
    overrides: Mapping[str, str] | None,
    *,
    clone_root: str | os.PathLike[str] = CLONE_ROOT,
) -> dict[str, str]:
    """The agent's environment: inherited − markers + overrides + fixed vars.

    ``PATH`` ends up with the clone root first (once), ``~`` entries expanded,
    and empty entries dropped.
    """
    env = strip_agent_markers(inherited)
    for k, v in (overrides or {}).items():
        _env_set(env, str(k), str(v))
    for k, v in FIXED_ENV.items():
        _env_set(env, k, v)
    _key, path_value = _env_get(env, "PATH")
    root = str(clone_root)
    entries = [e for e in split_path(path_value) if not _same_dir(e, root)]
    _env_set(env, "PATH", os.pathsep.join([root, *entries]))
    return env


def fallback_dirs(env: Mapping[str, str] | None = None) -> list[str]:
    """Well-known agent-CLI install dirs that may be missing from ``PATH``.

    E.g. the native claude installer uses ``~/.local/bin``, which on Windows
    is often absent from the Windows ``PATH`` (or present only as a literal
    ``~/.local/bin`` that ``cmd`` / PowerShell cannot expand).
    """
    src = env if env is not None else os.environ
    home = os.path.expanduser("~")
    dirs = [os.path.join(home, ".local", "bin"), os.path.join(home, ".bun", "bin")]
    if _is_windows():
        _k, appdata = _env_get(src, "APPDATA")
        _k, localappdata = _env_get(src, "LOCALAPPDATA")
        if appdata:
            dirs.append(os.path.join(appdata, "npm"))
        if localappdata:
            dirs.append(os.path.join(localappdata, "Microsoft", "WinGet", "Links"))
    else:
        dirs.extend(["/usr/local/bin", "/opt/homebrew/bin"])
    return dirs


def resolve_command(
    name: str,
    path_value: str | None,
    *,
    extra_dirs: Iterable[str] = (),
) -> str | None:
    """Resolve ``name`` to an absolute executable path, or ``None``.

    Tries ``PATH`` (with ``~`` expanded) first, then ``extra_dirs``. A name
    that already contains a directory part is checked as given.
    """
    if os.path.dirname(name):
        found = shutil.which(name)
        return os.path.abspath(found) if found else None
    search = split_path(path_value)
    found = shutil.which(name, path=os.pathsep.join(search)) if search else None
    if found:
        return os.path.abspath(found)
    for d in extra_dirs:
        if not d or not os.path.isdir(d):
            continue
        found = shutil.which(name, path=d)
        if found:
            return os.path.abspath(found)
    return None


def default_shell(env: Mapping[str, str]) -> list[str]:
    if _is_windows():
        _k, comspec = _env_get(env, "COMSPEC")
        return [comspec or "cmd.exe"]
    return [env.get("SHELL") or "/bin/sh"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _hold(message: str) -> None:
    """Print ``message`` and keep the pane open until Enter (or EOF)."""
    print(message, file=sys.stderr, flush=True)
    print("[fleet] press Enter to close this pane.", file=sys.stderr, flush=True)
    try:
        input()
    except (EOFError, KeyboardInterrupt, OSError):
        pass


def _parse_args(argv: Sequence[str]) -> tuple[str | None, list[str]]:
    args = list(argv)
    env_file: str | None = None
    agent: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            agent = args[i + 1:]
            break
        if a == "--env-file" and i + 1 < len(args):
            env_file = args[i + 1]
            i += 2
            continue
        if a.startswith("--env-file="):
            env_file = a.split("=", 1)[1]
            i += 1
            continue
        # Anything else before ``--`` starts the agent argv.
        agent = args[i:]
        break
    return env_file, agent


def load_env_file(path: str | os.PathLike[str]) -> tuple[dict[str, str], str | None]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("env file must hold a JSON object")
    env = data.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError("env file: 'env' must be an object")
    cwd = data.get("cwd")
    return {str(k): str(v) for k, v in env.items()}, (str(cwd) if cwd else None)


def _ignore_sigint() -> None:
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):  # pragma: no cover - not the main thread
        pass


def main(argv: Sequence[str] | None = None) -> int:
    env_file, agent = _parse_args(sys.argv[1:] if argv is None else argv)

    overrides: dict[str, str] = {}
    cwd: str | None = None
    if env_file:
        try:
            overrides, cwd = load_env_file(env_file)
        except (OSError, ValueError) as e:
            _hold(f"[fleet] error: cannot read pane env file {env_file}: {e}")
            return 1

    env = build_env(os.environ, overrides)

    if cwd:
        try:
            os.chdir(cwd)
        except OSError as e:
            _hold(f"[fleet] error: cannot change to working directory {cwd}: {e}")
            return 1

    if not agent:
        agent = default_shell(env)

    _k, path_value = _env_get(env, "PATH")
    extra = fallback_dirs(env)
    exe = resolve_command(agent[0], path_value, extra_dirs=extra)
    if exe is None:
        _hold(
            f"[fleet] error: agent CLI not found: {agent[0]!r}\n"
            f"        searched PATH and: {', '.join(extra)}\n"
            f"        install it or add its directory to PATH, then restart the task."
        )
        return EXIT_NOT_FOUND
    exe_dir = os.path.dirname(exe)
    if not any(_same_dir(exe_dir, e) for e in split_path(path_value)):
        # Found in a fallback dir: let the agent's own children find siblings.
        _env_set(env, "PATH", f"{path_value}{os.pathsep}{exe_dir}" if path_value else exe_dir)

    # Ctrl+C belongs to the agent (same console / terminal): the launcher just
    # waits. On POSIX an ignored signal stays ignored across exec, so ignore it
    # only once the agent runs — otherwise the agent, and every command it
    # starts, would never see SIGINT (a shell in the pane could not be
    # interrupted). Windows keeps ignoring it before the spawn.
    if _is_windows():
        _ignore_sigint()
    try:
        proc = subprocess.Popen([exe, *agent[1:]], env=env)
    except OSError as e:
        _hold(f"[fleet] error: cannot start {exe}: {e}")
        return 1
    if not _is_windows():
        _ignore_sigint()
    if env_file:
        write_pid_file(pid_file_for(env_file), launcher_pid=os.getpid(), agent_pid=proc.pid)
    return proc.wait()


def pid_file_for(env_file: str | os.PathLike[str]) -> Path:
    """``<env file>.pids`` — the launcher / agent pids of that pane."""
    p = Path(env_file)
    return p.with_name(p.name + ".pids")


def write_pid_file(path: Path, *, launcher_pid: int, agent_pid: int) -> None:
    """Record the pane's pids so the backend can wait for them on close."""
    try:
        path.write_text(
            json.dumps({"launcher": launcher_pid, "agent": agent_pid}), encoding="utf-8"
        )
    except OSError:
        pass


def read_pid_file(path: str | os.PathLike[str]) -> list[int]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return [int(v) for v in data.values() if isinstance(v, int) and v > 0]


def pid_alive(pid: int) -> bool:
    """Whether process ``pid`` is still running (best-effort, never kills)."""
    if pid <= 0:
        return False
    if _is_windows():
        import ctypes
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        WAIT_TIMEOUT = 0x102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
