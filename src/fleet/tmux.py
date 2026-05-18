"""Thin tmux wrapper.

We deliberately keep this module mechanism-only: it knows how to start
sessions, open windows, and send keys, but it does **not** know about
fleet concepts like driver, topology, or task. Higher layers compose
those abstractions on top.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
from typing import Sequence


class TmuxError(RuntimeError):
    """Raised when a tmux subprocess call returns non-zero."""


def available() -> bool:
    return shutil.which("tmux") is not None


def session_exists(session: str) -> bool:
    r = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
    )
    return r.returncode == 0


def new_session(session: str, *, window_name: str = "leader") -> None:
    """Create a detached tmux session with one initial window."""
    _run(["tmux", "new-session", "-d", "-s", session, "-n", window_name])


def kill_session(session: str) -> None:
    _run(["tmux", "kill-session", "-t", session])


def new_window(
    session: str,
    window_name: str,
    *,
    start_command: str | None = None,
    cwd: str | None = None,
) -> None:
    args = ["tmux", "new-window", "-t", session, "-n", window_name]
    if cwd:
        args.extend(["-c", cwd])
    if start_command:
        args.append(start_command)
    _run(args)


def kill_window(session: str, window_name: str) -> None:
    _run(["tmux", "kill-window", "-t", f"{session}:{window_name}"])


def send_keys(session: str, window: str, text: str, *, enter: bool = True) -> None:
    """Type ``text`` into the target window. If ``enter`` is true, press Enter after."""
    target = f"{session}:{window}"
    _run(["tmux", "send-keys", "-t", target, text])
    if enter:
        _run(["tmux", "send-keys", "-t", target, "Enter"])


def list_windows(session: str) -> list[str]:
    r = subprocess.run(
        ["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise TmuxError(r.stderr.strip())
    return [line for line in r.stdout.splitlines() if line]


def _run(args: Sequence[str]) -> None:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise TmuxError(
            f"tmux command failed: {shlex.join(args)}: {r.stderr.strip()}"
        )
