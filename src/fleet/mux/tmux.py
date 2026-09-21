"""tmux backend for :mod:`fleet.mux`.

Mechanism-only, like the rest of :mod:`fleet.mux`: it knows how to start
sessions, open windows, and send keys, but it does **not** know about fleet
concepts like driver, formation, or task.
"""
from __future__ import annotations

import os
import secrets
import shlex
import shutil
import subprocess
import tempfile
from typing import Sequence

from .base import Key, Mux, MuxError, PaneInfo, disabled_by_env, parse_key


class TmuxError(MuxError):
    """Raised when a tmux subprocess call returns non-zero."""


#: ``list-panes -F`` format: window id, window name, pane title (tab separated;
#: the title comes last so a title that itself holds a tab survives the split).
_PANE_FORMAT = "#{window_id}\t#{window_name}\t#{pane_title}"


def parse_panes(text: str) -> list[PaneInfo]:
    """:class:`PaneInfo` rows out of ``tmux list-panes -F`` (:data:`_PANE_FORMAT`) output."""
    panes: list[PaneInfo] = []
    for line in (text or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or not parts[0]:
            continue
        panes.append(PaneInfo(window=parts[1], window_id=parts[0], title=parts[2]))
    return panes


#: Normalized named key → tmux key name.
_TMUX_NAMED_KEYS = {
    "Enter": "Enter",
    "Escape": "Escape",
    "Tab": "Tab",
    "Backspace": "BSpace",
    "Space": "Space",
    "Up": "Up",
    "Down": "Down",
    "Left": "Left",
    "Right": "Right",
}

#: Normalized modifier → tmux modifier prefix.
_TMUX_MODIFIERS = {"Ctrl": "C-", "Alt": "M-"}

#: How many lines of history :meth:`TmuxMux.capture` returns.
CAPTURE_START = -200


def tmux_key(key: str | Key) -> str:
    """Translate a normalized key name (``"Ctrl-u"``) to tmux syntax (``"C-u"``)."""
    name = key.name if isinstance(key, Key) else key
    mod, base = parse_key(name)
    if mod is None:
        return _TMUX_NAMED_KEYS[base]
    return f"{_TMUX_MODIFIERS[mod]}{base}"


class TmuxMux(Mux):
    """The tmux backend (opt-in: ``FLEET_MUX=tmux`` or ``fleet config set mux tmux``)."""

    name = "tmux"

    # -- lifecycle -----------------------------------------------------------

    def available(self) -> bool:
        if disabled_by_env():
            return False
        return shutil.which("tmux") is not None

    def session_exists(self, session: str) -> bool:
        r = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True,
        )
        return r.returncode == 0

    def new_session(
        self,
        session: str,
        *,
        window: str | None = None,
        argv: Sequence[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Create a detached session with one shell window.

        With ``argv``, the command line is typed into that shell (so the pane
        keeps a shell after the agent exits), exactly like :meth:`new_window`.
        """
        args = ["tmux", "new-session", "-d", "-s", session]
        if window:
            args.extend(["-n", window])
        args.extend(_cwd_env_args(cwd, env))
        _run(args)
        if argv:
            target_window = window if window else ""
            self._type_command(session, target_window, argv)

    def new_window(
        self,
        session: str,
        window: str,
        *,
        argv: Sequence[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Open a detached shell window and type ``argv`` into it."""
        args = ["tmux", "new-window", "-d", "-t", session, "-n", window]
        args.extend(_cwd_env_args(cwd, env))
        _run(args)
        if argv:
            self._type_command(session, window, argv)

    def _type_command(self, session: str, window: str, argv: Sequence[str]) -> None:
        self.send_text(session, window, shlex.join(list(argv)), enter=True)

    def list_windows(self, session: str) -> list[str]:
        r = subprocess.run(
            ["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise TmuxError(r.stderr.strip())
        return [line for line in r.stdout.splitlines() if line]

    def kill_window(self, session: str, window: str) -> None:
        _run(["tmux", "kill-window", "-t", _target(session, window)])

    def kill_session(self, session: str) -> None:
        _run(["tmux", "kill-session", "-t", session])

    def list_panes(self, session: str) -> list[PaneInfo]:
        """Every pane of ``session`` (``-s``: all windows), with its window id and title."""
        r = subprocess.run(
            ["tmux", "list-panes", "-s", "-t", session, "-F", _PANE_FORMAT],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise TmuxError(r.stderr.strip())
        return parse_panes(r.stdout)

    def rename_window(self, session: str, window_id: str, new_name: str) -> None:
        # ``window_id`` (``@3``) is server-unique, so no session qualifier is needed.
        _run(["tmux", "rename-window", "-t", window_id, new_name])

    # -- input / output -------------------------------------------------------

    def send_text(
        self, session: str, window: str, text: str, *, enter: bool = True
    ) -> None:
        target = _target(session, window)
        if text:
            # ``-l``: type the text literally — never interpret it as a key
            # name. Key presses go through :meth:`send_key` explicitly.
            _run(["tmux", "send-keys", "-t", target, "-l", text])
        if enter:
            _run(["tmux", "send-keys", "-t", target, "Enter"])

    def send_key(self, session: str, window: str, key: str | Key) -> None:
        _run(["tmux", "send-keys", "-t", _target(session, window), tmux_key(key)])

    def paste(self, session: str, window: str, text: str) -> None:
        """Paste via a one-shot named buffer (``paste-buffer -d`` drops it)."""
        buffer_name = f"fleet-paste-{os.getpid()}-{secrets.token_hex(4)}"
        self._load_buffer(buffer_name, text)
        try:
            _run(
                [
                    "tmux",
                    "paste-buffer",
                    "-d",
                    "-t",
                    _target(session, window),
                    "-b",
                    buffer_name,
                ]
            )
        except TmuxError:
            self.drop_paste(buffer_name)
            raise

    def capture(self, session: str, window: str) -> str:
        r = subprocess.run(
            [
                "tmux",
                "capture-pane",
                "-p",
                "-J",
                "-S",
                str(CAPTURE_START),
                "-t",
                _target(session, window),
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise TmuxError(r.stderr.strip())
        return r.stdout

    # -- attach ---------------------------------------------------------------

    def attach(self, session: str, window: str | None = None) -> int:
        """Attach to ``session``; with ``window``, via a grouped view session.

        The grouped view session (Issue #76) gives this client an independent
        active window, so landing on ``window`` does not move other clients
        attached to the same session. On POSIX this ``exec``s tmux and never
        returns; raises :class:`TmuxError` if the view session cannot be set up.
        """
        if window is None:
            return _exec_tmux(["attach", "-t", session])

        _sweep_stale_view_sessions(session)
        view = f"{session}-view-{os.getpid()}"
        try:
            r = subprocess.run(
                ["tmux", "new-session", "-d", "-s", view, "-t", session],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                # If the view session already exists, retry with a random suffix.
                view = f"{session}-view-{os.getpid()}-{secrets.token_hex(4)}"
                r2 = subprocess.run(
                    ["tmux", "new-session", "-d", "-s", view, "-t", session],
                    capture_output=True,
                    text=True,
                )
                if r2.returncode != 0:
                    raise TmuxError(
                        f"failed to create view session: {r2.stderr.strip()}"
                    )

            r3 = subprocess.run(
                ["tmux", "select-window", "-t", f"{view}:{window}"],
                capture_output=True,
                text=True,
            )
            if r3.returncode != 0:
                subprocess.run(["tmux", "kill-session", "-t", view], capture_output=True)
                raise TmuxError(
                    f"failed to select window in view session: {r3.stderr.strip()}"
                )
        except FileNotFoundError:
            raise TmuxError("tmux not on PATH") from None

        return _exec_tmux(["attach", "-t", view])

    def attach_hint(self, session: str, window: str | None = None) -> str:
        target = session if window is None else _target(session, window)
        return f"tmux attach -t {target}"

    # -- optional -------------------------------------------------------------

    def preload_paste(self, name: str, text: str) -> str | None:
        """Load ``text`` into the named tmux buffer ``name`` (overwrites)."""
        self._load_buffer(name, text)
        return "inside the pane press C-b ], then Enter"

    def drop_paste(self, name: str) -> None:
        """Best-effort buffer cleanup. Missing buffer is not an error."""
        subprocess.run(
            ["tmux", "delete-buffer", "-b", name],
            capture_output=True,
        )

    def kill_session_hint(self, session: str) -> str:
        return f"tmux kill-session -t {session}"

    # -- internals ------------------------------------------------------------

    def _load_buffer(self, buffer_name: str, text: str) -> None:
        """Load ``text`` into a named tmux buffer via a temp file."""
        fd, path = tempfile.mkstemp(prefix="fleet-paste-", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            _run(["tmux", "load-buffer", "-b", buffer_name, "--", path])
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def _target(session: str, window: str) -> str:
    return f"{session}:{window}" if window else session


def _cwd_env_args(cwd: str | None, env: dict[str, str] | None) -> list[str]:
    args: list[str] = []
    if cwd:
        args.extend(["-c", cwd])
    if env:
        for k, v in env.items():
            args.extend(["-e", f"{k}={v}"])
    return args


def _exec_tmux(tmux_args: list[str]) -> int:
    argv = ["tmux", *tmux_args]
    if os.name == "nt":  # no real exec on Windows
        try:
            return subprocess.call(argv)
        except FileNotFoundError:
            raise TmuxError("tmux not on PATH") from None
    try:
        os.execvp("tmux", argv)
    except FileNotFoundError:
        raise TmuxError("tmux not on PATH") from None
    return 0  # pragma: no cover - execvp does not return


def _sweep_stale_view_sessions(session: str) -> None:
    """Kill old view sessions that have no client attached (best-effort)."""
    try:
        r = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name} #{session_attached}"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return
        prefix = f"{session}-view-"
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name, attached = parts[0], parts[1]
            if name.startswith(prefix) and attached == "0":
                subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
    except FileNotFoundError:
        pass


def _run(args: Sequence[str]) -> None:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise TmuxError(
            f"tmux command failed: {shlex.join(args)}: {r.stderr.strip()}"
        )


__all__ = ["TmuxMux", "TmuxError", "tmux_key", "parse_panes", "CAPTURE_START"]
