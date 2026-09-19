"""Portable process helpers.

:func:`spawn_detached` starts a background helper (prompt deliverer, leader
notifier) that must outlive the ``fleet`` / ``fleet-agent`` command that
spawned it and must not be tied to that command's terminal.

* POSIX: ``start_new_session=True`` (setsid), exactly as before.
* Windows: ``start_new_session`` is silently ignored, so use creation flags
  instead: ``DETACHED_PROCESS`` (no console), ``CREATE_NEW_PROCESS_GROUP``
  (no Ctrl+C from the parent console) and ``CREATE_BREAKAWAY_FROM_JOB`` (not
  killed when the parent's job object closes, e.g. a terminal tab). Breakaway
  is refused with an ``OSError`` when the parent's job forbids it, so retry
  without it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

# Numeric fallbacks keep the flag construction testable on POSIX, where the
# subprocess module does not define these constants.
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)

WINDOWS_DETACH_FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB


def _is_windows() -> bool:
    return sys.platform == "win32"


def spawn_detached(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    env: Mapping[str, str] | None,
    log_path: str | os.PathLike[str],
) -> subprocess.Popen:
    """Start ``argv`` fully detached, appending its stdout/stderr to ``log_path``.

    stdin is ``DEVNULL``; no shell is involved. Returns the ``Popen`` object
    (callers normally ignore it — the child is not waited on).
    """
    args = [str(a) for a in argv]
    child_env = dict(env) if env is not None else None
    base_kwargs: dict = {
        "cwd": str(cwd),
        "env": child_env,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    with Path(log_path).open("ab") as log:
        base_kwargs["stdout"] = log
        base_kwargs["stderr"] = log
        if not _is_windows():
            return subprocess.Popen(  # noqa: S603 - argv is constructed, no shell.
                args, start_new_session=True, **base_kwargs
            )
        if child_env is not None:
            # Python children: never fall back to the cp932/ANSI locale.
            child_env.setdefault("PYTHONUTF8", "1")
        try:
            return subprocess.Popen(  # noqa: S603
                args, creationflags=WINDOWS_DETACH_FLAGS, **base_kwargs
            )
        except OSError:
            # The parent's job object does not allow breakaway.
            return subprocess.Popen(  # noqa: S603
                args,
                creationflags=WINDOWS_DETACH_FLAGS & ~CREATE_BREAKAWAY_FROM_JOB,
                **base_kwargs,
            )
