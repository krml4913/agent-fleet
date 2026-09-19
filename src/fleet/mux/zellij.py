"""zellij backend for :mod:`fleet.mux` (docs/windows-support.md §3, §4, §6).

Mechanism-only, like the tmux backend. Layout (§6.2 A): one zellij session per
``fleet-<label>``; fleet's "windows" are zellij **tabs** addressed by name.

What differs from tmux, and how this backend copes:

* ``zellij action`` exits 0 on most failures (missing session sometimes,
  missing pane always) → every operation checks existence explicitly
  (``list-sessions -n`` / ``action list-panes -a -j`` / ``list-tabs -j``) and
  raises :class:`ZellijError` itself (§4.1).
* No per-pane environment → every pane runs :mod:`fleet.pane_launch` with a
  JSON env file (§4.4, §6.3).
* Creating a session from a process with redirected stdio kills the first
  pane on Windows → sessions are created from a new, hidden console (§4.2).
* zellij 0.45.x discards tabs created while no client is attached (#5594) →
  a hidden temporary client is attached around ``new-tab`` (§4.3).
* Killed sessions stay listed as ``(EXITED - attach to resurrect)`` → ignored
  by :meth:`ZellijMux.session_exists`; :meth:`ZellijMux.kill_session` also
  deletes them (§4.6).
* Tab / pane ids are reused → resolved by name on every call, never cached
  (§4.8).
* The zellij binary is resolved once to an absolute path (``FLEET_ZELLIJ``
  overrides): different versions share one session namespace (§6.6).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from .base import Key, Mux, MuxError, disabled_by_env, parse_key


class ZellijError(MuxError):
    """Raised when a zellij operation fails (detected explicitly, §4.1)."""


#: Normalized named key → zellij ``send-keys`` key name (verified on 0.45.1:
#: ``Escape`` is rejected, ``Esc`` works).
_ZELLIJ_NAMED_KEYS = {
    "Enter": "Enter",
    "Escape": "Esc",
    "Tab": "Tab",
    "Backspace": "Backspace",
    "Space": "Space",
    "Up": "Up",
    "Down": "Down",
    "Left": "Left",
    "Right": "Right",
}

#: Minimum supported version (``new-tab --no-focus``, §6.6).
MIN_VERSION = (0, 45, 0)
#: First version assumed to carry the #5594 fix (tabs created with no client
#: attached get a real size). Below it the temp-client workaround is used.
FIXED_DETACHED_TAB_VERSION = (0, 46, 0)

#: Pause between typed text and Enter: claude's composer may treat an Enter
#: that arrives in the same burst as the text as part of a paste (Phase 0
#: used 0.25 s).
TEXT_ENTER_GAP_SECONDS = 0.25

SESSION_START_TIMEOUT = 15.0
CLIENT_ATTACH_TIMEOUT = 10.0
CLIENT_DETACH_TIMEOUT = 5.0
TAB_CONFIRM_TIMEOUT = 5.0
PROCESS_EXIT_TIMEOUT = 10.0
ATTACH_FOCUS_TIMEOUT = 20.0
POLL_INTERVAL = 0.1

# Windows process-creation flags (numeric fallbacks keep this importable and
# testable on POSIX).
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
STARTF_USESHOWWINDOW = getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
SW_HIDE = 0

_EXITED_MARK = "(EXITED"
_NOT_FOUND_RE = re.compile(r"Session '([^']*)' not found")
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _is_windows() -> bool:
    return sys.platform == "win32"


def zellij_key(key: str | Key) -> str:
    """Translate a normalized key name (``"Ctrl-u"``) to zellij (``"Ctrl u"``)."""
    name = key.name if isinstance(key, Key) else key
    mod, base = parse_key(name)
    if mod is None:
        return _ZELLIJ_NAMED_KEYS[base]
    return f"{mod} {base}"


def parse_version(text: str) -> tuple[int, int, int] | None:
    """``"zellij 0.45.1"`` → ``(0, 45, 1)``; ``None`` when unparseable."""
    m = _VERSION_RE.search(text or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def parse_sessions(text: str) -> list[str]:
    """Live session names from ``zellij list-sessions -n`` output.

    Drops ``(EXITED - attach to resurrect)`` entries and the
    ``No active zellij sessions found.`` message.
    """
    names: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or _EXITED_MARK in line or line.startswith("No active zellij sessions"):
            continue
        name = line.split(" [Created", 1)[0].strip() if " [Created" in line else line.split()[0]
        if name:
            names.append(name)
    return names


def parse_exited_sessions(text: str) -> list[str]:
    """Names of the resurrectable (``EXITED``) entries in ``list-sessions -n``."""
    names: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if _EXITED_MARK not in line:
            continue
        name = line.split(" [Created", 1)[0].strip() if " [Created" in line else line.split()[0]
        if name:
            names.append(name)
    return names


def parse_clients(text: str) -> list[str]:
    """Client ids from ``action list-clients`` (first column only, §4.3)."""
    ids: list[str] = []
    for raw in (text or "").splitlines():
        parts = raw.split()
        if not parts or parts[0] == "CLIENT_ID":
            continue
        ids.append(parts[0])
    return ids


def _parse_json_list(text: str, what: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError as e:
        raise ZellijError(f"cannot parse zellij {what} output: {e}") from None
    if not isinstance(data, list):
        raise ZellijError(f"unexpected zellij {what} output (not a list)")
    return [d for d in data if isinstance(d, dict)]


def terminal_panes(panes: list[dict[str, Any]], tab_name: str) -> list[dict[str, Any]]:
    """Terminal (non-plugin) panes of the tab(s) named ``tab_name``."""
    return [
        p for p in panes
        if not p.get("is_plugin") and p.get("tab_name") == tab_name
    ]


#: Variables zellij sets inside its panes. A ``zellij attach S`` that sees
#: ``ZELLIJ_SESSION_NAME=S`` panics ("You are trying to attach to the current
#: session … This is not supported", exit 101), so a fleet command run from a
#: pane of ``S`` (the leader's ``start``, a driver's ``done``) could not attach
#: the #5594 temp client.
ZELLIJ_PANE_VARS: tuple[str, ...] = ("ZELLIJ", "ZELLIJ_SESSION_NAME", "ZELLIJ_PANE_ID")


def client_env(env: dict[str, str] | os._Environ) -> dict[str, str]:
    """``env`` without the pane markers in :data:`ZELLIJ_PANE_VARS`."""
    drop = {v.upper() for v in ZELLIJ_PANE_VARS}
    return {k: v for k, v in env.items() if k.upper() not in drop}


def _safe_filename(name: str) -> str:
    out = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return out or "_"


class ZellijMux(Mux):
    """The zellij backend (the default on Windows)."""

    name = "zellij"

    @property
    def window_close_kills_caller(self) -> bool:  # type: ignore[override]
        """Windows: ``close-tab-by-id`` ends every process on the tab's console,
        including a ``fleet-agent done`` run from that tab (verified in the
        two-stage E2E). Not observed on POSIX, which keeps tmux's behavior."""
        return _is_windows()

    def __init__(
        self,
        binary: str | None = None,
        *,
        pane_env_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._binary_arg = binary
        self._binary: str | None = None
        self._binary_resolved = False
        self._version: tuple[int, int, int] | None = None
        self._version_probed = False
        self._pane_env_dir = Path(pane_env_dir) if pane_env_dir else None

    # -- binary / version -----------------------------------------------------

    @property
    def binary(self) -> str | None:
        """Absolute path of the zellij binary (resolved once), or ``None``."""
        if not self._binary_resolved:
            self._binary = self._resolve_binary()
            self._binary_resolved = True
        return self._binary

    def _resolve_binary(self) -> str | None:
        candidate = self._binary_arg or os.environ.get("FLEET_ZELLIJ") or "zellij"
        found = shutil.which(candidate)
        if found:
            return os.path.abspath(found)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
        return None

    @property
    def version(self) -> tuple[int, int, int] | None:
        """Parsed ``zellij --version`` (cached), or ``None`` if unknown."""
        if not self._version_probed:
            self._version_probed = True
            if self.binary:
                try:
                    r = self._run([self.binary, "--version"], timeout=10)
                    self._version = parse_version(r.stdout or r.stderr)
                except (OSError, subprocess.SubprocessError):
                    self._version = None
        return self._version

    def needs_temp_client(self) -> bool:
        """Whether ``new-tab`` needs the #5594 temp-client workaround."""
        forced = os.environ.get("FLEET_ZELLIJ_TEMP_CLIENT")
        if forced is not None and forced.strip() != "":
            return forced.strip().lower() not in ("0", "false", "no", "off")
        v = self.version
        return v is None or v < FIXED_DETACHED_TAB_VERSION

    def _bin(self) -> str:
        if disabled_by_env():
            # A hard guard (not just ``available() == False``): some callers
            # go straight to new_session, and tests must never leak a real
            # session.
            raise ZellijError("zellij disabled by FLEET_NO_MUX")
        b = self.binary
        if not b:
            raise ZellijError("zellij not on PATH (set FLEET_ZELLIJ to its path)")
        return b

    # -- subprocess plumbing (patched in tests) -------------------------------

    def _run(self, argv: Sequence[str], *, timeout: float | None = 30) -> subprocess.CompletedProcess:
        """Run a short zellij command, capturing UTF-8 output.

        On Windows ``CREATE_NO_WINDOW`` keeps a console-less caller (the
        detached deliverer / notifier) from flashing a console window.
        """
        kwargs: dict[str, Any] = {}
        if _is_windows():
            kwargs["creationflags"] = CREATE_NO_WINDOW
        try:
            return subprocess.run(
                list(argv),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                **kwargs,
            )
        except subprocess.TimeoutExpired:
            raise ZellijError(f"zellij command timed out: {' '.join(argv[1:])}") from None
        except FileNotFoundError:
            raise ZellijError(f"zellij binary not found: {argv[0]}") from None

    def _spawn_client(self, argv: Sequence[str], *, cwd: str | None = None) -> subprocess.Popen:
        """Start a zellij process that needs a terminal (``attach``).

        Windows: a new, hidden console, **no** std-handle redirection (§4.2);
        breakaway from the caller's job when allowed. POSIX: detached from
        our stdio.
        """
        args = [str(a) for a in argv]
        env = client_env(os.environ)
        if _is_windows():
            si = subprocess.STARTUPINFO()
            si.dwFlags |= STARTF_USESHOWWINDOW
            si.wShowWindow = SW_HIDE
            try:
                return subprocess.Popen(
                    args,
                    cwd=cwd,
                    env=env,
                    creationflags=CREATE_NEW_CONSOLE | CREATE_BREAKAWAY_FROM_JOB,
                    startupinfo=si,
                )
            except OSError:
                return subprocess.Popen(
                    args, cwd=cwd, env=env, creationflags=CREATE_NEW_CONSOLE, startupinfo=si
                )
        return subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _call_attach(self, argv: Sequence[str]) -> int:
        """Run an interactive ``zellij attach`` in the current terminal."""
        return subprocess.call(list(argv))

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _now(self) -> float:
        return time.monotonic()

    def _wait_for(self, predicate: Callable[[], bool], timeout: float) -> bool:
        deadline = self._now() + timeout
        while True:
            if predicate():
                return True
            if self._now() >= deadline:
                return False
            self._sleep(POLL_INTERVAL)

    # -- zellij queries --------------------------------------------------------

    def _list_sessions_raw(self) -> str:
        r = self._run([self._bin(), "list-sessions", "-n"])
        # "No active zellij sessions found." exits 1 — not an error for us.
        return r.stdout or ""

    def _sessions(self) -> list[str]:
        return parse_sessions(self._list_sessions_raw())

    def _action(self, session: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """``zellij -s <session> action …`` with explicit failure detection."""
        r = self._run([self._bin(), "-s", session, "action", *args])
        if check:
            combined = f"{r.stdout or ''}\n{r.stderr or ''}"
            m = _NOT_FOUND_RE.search(combined)
            if m and m.group(1) == session:
                raise ZellijError(f"zellij session not found: {session}")
            if r.returncode != 0:
                detail = (r.stderr or r.stdout or "").strip()
                raise ZellijError(
                    f"zellij action {args[0] if args else ''} failed "
                    f"(session {session}): {detail}"
                )
        return r

    def _panes(self, session: str) -> list[dict[str, Any]]:
        r = self._action(session, "list-panes", "-a", "-j")
        return _parse_json_list(r.stdout, "list-panes")

    def _tabs(self, session: str) -> list[dict[str, Any]]:
        r = self._action(session, "list-tabs", "-j")
        tabs = _parse_json_list(r.stdout, "list-tabs")
        return sorted(tabs, key=lambda t: t.get("position", 0))

    def _clients(self, session: str) -> list[str]:
        r = self._action(session, "list-clients")
        return parse_clients(r.stdout)

    def _pane_id(self, session: str, window: str) -> str:
        """Resolve ``(session, tab name)`` → ``terminal_<id>`` (never cached)."""
        if not self.session_exists(session):
            raise ZellijError(f"zellij session not found: {session}")
        panes = terminal_panes(self._panes(session), window)
        if not panes:
            raise ZellijError(f"zellij tab not found (or has no terminal pane): {session}:{window}")
        return f"terminal_{panes[0].get('id')}"

    def _tab_ready(self, session: str, window: str, exclude: set[str] | frozenset[str] = frozenset()) -> bool:
        """A tab named ``window`` (not one of ``exclude`` ids) has a terminal pane."""
        try:
            return any(
                str(p.get("tab_id")) not in exclude
                for p in terminal_panes(self._panes(session), window)
            )
        except ZellijError:
            return False

    # -- lifecycle -----------------------------------------------------------

    def available(self) -> bool:
        if disabled_by_env():
            return False
        return self.binary is not None

    def session_exists(self, session: str) -> bool:
        if not self.binary:
            return False
        try:
            return session in self._sessions()
        except ZellijError:
            return False

    def pane_env_dir(self) -> Path:
        if self._pane_env_dir is None:
            from .. import state as state_mod  # lazy: keep mux import-light

            self._pane_env_dir = state_mod.global_dir() / "pane-env"
        return self._pane_env_dir

    def _env_path(self, session: str, window: str) -> Path:
        return self.pane_env_dir() / _safe_filename(session) / f"{_safe_filename(window)}.json"

    def _pane_command(
        self,
        session: str,
        window: str,
        argv: Sequence[str] | None,
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> list[str]:
        """Write the pane env file and return the launcher command (§6.3)."""
        from .. import pane_launch

        path = self._env_path(session, window)
        pane_launch.pid_file_for(path).unlink(missing_ok=True)
        pane_launch.write_env_file(path, env=env, cwd=cwd)
        return pane_launch.launcher_argv(path, argv)

    def new_session(
        self,
        session: str,
        *,
        window: str | None = None,
        argv: Sequence[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Create a background session whose first tab runs the launcher.

        The first tab is renamed to ``window`` (when given). Without ``argv``
        the launcher starts the platform shell, so the per-window ``env`` /
        ``cwd`` still apply.
        """
        zellij = self._bin()
        raw = self._list_sessions_raw()
        if session in parse_sessions(raw):
            raise ZellijError(f"duplicate session: {session}")
        if session in parse_exited_sessions(raw):
            # Otherwise ``attach -b`` would resurrect the dead session.
            self._run([zellij, "delete-session", session])

        first_window = window or "0"
        command = self._pane_command(session, first_window, argv, cwd, env)
        proc = self._spawn_client([zellij, "attach", "-b", session, "--", *command], cwd=cwd)
        try:
            proc.wait(timeout=SESSION_START_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass  # the server is independent; keep polling below

        if not self._wait_for(lambda: session in self._sessions(), SESSION_START_TIMEOUT):
            raise ZellijError(f"zellij session did not start: {session}")
        if not self._wait_for(lambda: self._first_terminal_tab(session) is not None, SESSION_START_TIMEOUT):
            raise ZellijError(f"zellij session has no terminal pane: {session}")
        if window:
            tab_id = self._first_terminal_tab(session)
            self._action(session, "rename-tab-by-id", str(tab_id), window)
            if not self._wait_for(lambda: window in self._tab_names(session), TAB_CONFIRM_TIMEOUT):
                raise ZellijError(f"could not name the first tab {window!r} in {session}")

    def _first_terminal_tab(self, session: str) -> int | None:
        try:
            for p in self._panes(session):
                if not p.get("is_plugin") and p.get("tab_id") is not None:
                    return int(p["tab_id"])
        except (ZellijError, ValueError, TypeError):
            return None
        return None

    def _tab_names(self, session: str) -> list[str]:
        try:
            return [str(t.get("name", "")) for t in self._tabs(session)]
        except ZellijError:
            return []

    def new_window(
        self,
        session: str,
        window: str,
        *,
        argv: Sequence[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Open tab ``window`` (no focus change) running the launcher.

        With no client attached on an affected zellij (#5594), a hidden
        temporary client is attached around ``new-tab`` and always killed
        afterwards. The tab is confirmed (by name, with a terminal pane) both
        before and after the temp client goes away; the whole sequence is
        retried once if it is missing.
        """
        zellij = self._bin()
        if not self.session_exists(session):
            raise ZellijError(f"zellij session not found: {session}")
        command = self._pane_command(session, window, argv, cwd, env)
        new_tab = ["new-tab", "--name", window, "--no-focus"]
        if cwd:
            new_tab += ["--cwd", str(cwd)]
        new_tab += ["--", *command]

        last_error = "tab missing after creation"
        for _attempt in range(2):
            before = self._tab_ids_named(session, window)
            temp: subprocess.Popen | None = None
            ok = False
            try:
                if self.needs_temp_client() and not self._clients(session):
                    temp = self._attach_temp_client(zellij, session)
                r = self._action(session, *new_tab)
                created = (r.stdout or "").strip()
                ok = self._wait_for(
                    lambda: self._tab_ready(session, window, before), TAB_CONFIRM_TIMEOUT
                )
                last_error = f"tab {window!r} missing after new-tab (id {created or '?'})"
            except ZellijError as e:
                last_error = str(e)
            finally:
                if temp is not None:
                    self._kill_temp_client(session, temp)
            if ok and temp is not None:
                # Re-confirm: the one Phase 0 loss happened at client detach.
                ok = self._tab_ready(session, window, before)
            if ok:
                return
            # Drop a pane-less leftover of this attempt before retrying.
            for tab_id in self._tab_ids_named(session, window) - before:
                self._action(session, "close-tab-by-id", tab_id, check=False)
        raise ZellijError(f"zellij new-tab failed in {session}: {last_error}")

    def _tab_ids_named(self, session: str, window: str) -> set[str]:
        try:
            return {str(t.get("tab_id")) for t in self._tabs(session) if t.get("name") == window}
        except ZellijError:
            return set()

    def _attach_temp_client(self, zellij: str, session: str) -> subprocess.Popen:
        before = set(self._clients(session))
        proc = self._spawn_client([zellij, "attach", session])
        try:
            appeared = self._wait_for(
                lambda: bool(set(self._clients(session)) - before), CLIENT_ATTACH_TIMEOUT
            )
        except BaseException:
            self._kill_proc(proc)
            raise
        if not appeared:
            self._kill_proc(proc)
            raise ZellijError(f"temporary zellij client did not attach to {session}")
        proc._fleet_client_ids = set(self._clients(session)) - before  # type: ignore[attr-defined]
        return proc

    def _kill_proc(self, proc: subprocess.Popen) -> None:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=CLIENT_DETACH_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError):
            pass

    def _kill_temp_client(self, session: str, proc: subprocess.Popen) -> None:
        ids = getattr(proc, "_fleet_client_ids", set())
        self._kill_proc(proc)

        def gone() -> bool:
            try:
                return not (set(self._clients(session)) & ids)
            except ZellijError:
                return True

        self._wait_for(gone, CLIENT_DETACH_TIMEOUT)

    def list_windows(self, session: str) -> list[str]:
        if not self.session_exists(session):
            raise ZellijError(f"zellij session not found: {session}")
        return [str(t.get("name", "")) for t in self._tabs(session)]

    def kill_window(self, session: str, window: str) -> None:
        """Close tab(s) ``window`` and wait for its agent process to end.

        Waiting matters on Windows: callers (cleanup / merge) delete the
        agent's cwd (its worktree) right after, which fails while the
        process is still exiting.
        """
        from .. import pane_launch

        if not self.session_exists(session):
            raise ZellijError(f"zellij session not found: {session}")
        ids = [str(t.get("tab_id")) for t in self._tabs(session) if t.get("name") == window]
        if not ids:
            raise ZellijError(f"zellij tab not found: {session}:{window}")
        pids = pane_launch.read_pid_file(pane_launch.pid_file_for(self._env_path(session, window)))
        for tab_id in ids:
            self._action(session, "close-tab-by-id", tab_id)

        def closed() -> bool:
            if not self.session_exists(session):
                return True
            names = self._tab_names(session)
            # list-tabs can come back empty for a moment while a tab closes.
            return bool(names) and window not in names

        if not self._wait_for(closed, TAB_CONFIRM_TIMEOUT):
            raise ZellijError(f"zellij tab did not close: {session}:{window}")
        self._wait_for(
            lambda: not any(self._pid_alive(p) for p in pids), PROCESS_EXIT_TIMEOUT
        )
        self._remove_env_file(session, window)

    def _pid_alive(self, pid: int) -> bool:
        from .. import pane_launch

        return pane_launch.pid_alive(pid)

    def kill_session(self, session: str) -> None:
        zellij = self._bin()
        if not self.session_exists(session):
            raise ZellijError(f"zellij session not found: {session}")
        self._run([zellij, "kill-session", session])
        # Drop the resurrectable entry too (§4.6); --force covers a server
        # that is still shutting down.
        self._run([zellij, "delete-session", "--force", session])

        def gone() -> bool:
            raw = self._list_sessions_raw()
            return session not in parse_sessions(raw) and session not in parse_exited_sessions(raw)

        if not self._wait_for(gone, SESSION_START_TIMEOUT):
            if session in self._sessions():
                raise ZellijError(f"zellij session did not stop: {session}")
            self._run([zellij, "delete-session", session])
        self._remove_env_dir(session)

    def _remove_env_file(self, session: str, window: str) -> None:
        from .. import pane_launch

        path = self._env_path(session, window)
        for p in (path, pane_launch.pid_file_for(path)):
            try:
                p.unlink()
            except OSError:
                pass

    def _remove_env_dir(self, session: str) -> None:
        try:
            shutil.rmtree(self.pane_env_dir() / _safe_filename(session), ignore_errors=True)
        except OSError:
            pass

    # -- input / output -------------------------------------------------------

    def send_text(
        self, session: str, window: str, text: str, *, enter: bool = True
    ) -> None:
        pane = self._pane_id(session, window)
        if text:
            # ``--``: text starting with "-" must not be parsed as an option.
            self._action(session, "write-chars", "-p", pane, "--", text)
        if enter:
            if text:
                self._sleep(TEXT_ENTER_GAP_SECONDS)
            self._action(session, "send-keys", "-p", pane, "Enter")

    def send_key(self, session: str, window: str, key: str | Key) -> None:
        zkey = zellij_key(key)
        pane = self._pane_id(session, window)
        self._action(session, "send-keys", "-p", pane, zkey)

    def paste(self, session: str, window: str, text: str) -> None:
        pane = self._pane_id(session, window)
        self._action(session, "paste", "-p", pane, "--", text)

    def capture(self, session: str, window: str) -> str:
        pane = self._pane_id(session, window)
        r = self._action(session, "dump-screen", "-p", pane)
        return r.stdout or ""

    # -- attach ---------------------------------------------------------------

    def tab_position(self, session: str, window: str) -> int | None:
        """1-based position of tab ``window`` (for "Ctrl t, then N" hints)."""
        for i, t in enumerate(self._tabs(session)):
            if t.get("name") == window:
                return int(t.get("position", i)) + 1
        return None

    def attach(self, session: str, window: str | None = None) -> int:
        """Attach this terminal (subprocess ``zellij attach``; §6.5).

        With ``window`` and no other client attached, a background helper
        waits for our client to show up in ``list-clients`` and then runs
        ``go-to-tab-name`` (which drives the lowest-id client — ours, since
        it is the only one). With another client attached it only prints the
        tab position: ``go-to-tab-name`` would move the *other* client.
        """
        zellij = self._bin()
        if not self.session_exists(session):
            raise ZellijError(f"zellij session not found: {session}")
        if window is not None:
            if self._clients(session):
                pos = self.tab_position(session, window)
                where = f"tab {pos}" if pos else f"tab {window!r}"
                print(
                    f"note: another client is attached to {session}; task window "
                    f"{window!r} is {where} — press Ctrl t, then {pos or '<n>'}.",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                self.start_focus_helper(session, window)
        return self._call_attach([zellij, "attach", session])

    def start_focus_helper(
        self, session: str, window: str, *, timeout: float = ATTACH_FOCUS_TIMEOUT
    ) -> threading.Thread:
        """Background thread: once a client is attached, focus tab ``window``."""

        def run() -> None:
            try:
                if self._wait_for(lambda: bool(self._clients(session)), timeout):
                    self._action(session, "go-to-tab-name", window, check=False)
            except MuxError:
                pass

        t = threading.Thread(target=run, name="fleet-zellij-focus", daemon=True)
        t.start()
        return t

    def attach_hint(self, session: str, window: str | None = None) -> str:
        zellij = "zellij"
        if window is None:
            return f"{zellij} attach {session}"
        return f"{zellij} attach {session}   (then open tab {window!r})"

    def kill_session_hint(self, session: str) -> str:
        return f"zellij delete-session --force {session}"


__all__ = [
    "ZellijMux",
    "ZellijError",
    "zellij_key",
    "parse_version",
    "parse_sessions",
    "parse_exited_sessions",
    "parse_clients",
    "terminal_panes",
    "MIN_VERSION",
    "FIXED_DETACHED_TAB_VERSION",
]
