"""Terminal-multiplexer abstraction.

Every tmux / zellij interaction goes through the process-wide
backend returned by :func:`get`. The backend is mechanism-only; the helpers in
this module add the one piece of naming convention fleet needs at this layer —
a task's windows are named ``<task>`` or ``<task>·<role>`` — on top of
:meth:`Mux.list_windows`.

Backend selection, first hit wins: ``FLEET_MUX`` (``tmux`` | ``zellij``), then
the ``mux`` key of the global config (``fleet-state/global/config.yaml``, see
:mod:`fleet.config`), then the built-in default ``zellij`` (on every platform).
``FLEET_NO_MUX`` (alias ``FLEET_NO_TMUX``) makes :meth:`Mux.available` false.
"""
from __future__ import annotations

import os
from typing import Sequence

from .base import Key, Mux, MuxError, disabled_by_env, parse_key

#: Built-in default on every platform (was tmux off Windows before #299).
DEFAULT_BACKEND = "zellij"
BACKENDS: tuple[str, ...] = ("tmux", "zellij")

_backend: Mux | None = None


def default_backend_name() -> str:
    """The built-in default backend (``zellij``), ignoring env and config."""
    return DEFAULT_BACKEND


def backend_selection() -> tuple[str, str]:
    """``(name, source)``: the selected backend and where the choice came from.

    ``source`` is ``env`` (``FLEET_MUX``), ``config`` (global config ``mux``) or
    ``default``. Does not validate an env value or instantiate anything —
    :func:`get` does. A bad config file only warns and falls through (see
    :mod:`fleet.config`).
    """
    explicit = (os.environ.get("FLEET_MUX") or "").strip().lower()
    if explicit:
        return explicit, "env"
    from .. import config

    return config.get("mux")


def backend_name() -> str:
    """The selected backend name (env > global config > built-in default)."""
    return backend_selection()[0]


def get() -> Mux:
    """Return the process-wide multiplexer backend, creating it on first use."""
    global _backend
    if _backend is None:
        _backend = _create(backend_name())
    return _backend


def set_backend(backend: Mux | None) -> Mux | None:
    """Install ``backend`` as the process-wide backend; return the previous one.

    ``None`` drops the cached backend so the next :func:`get` re-selects from
    the environment. Intended for tests (a fake backend) and for resetting.
    """
    global _backend
    previous = _backend
    _backend = backend
    return previous


def _create(name: str) -> Mux:
    if name == "tmux":
        from .tmux import TmuxMux

        return TmuxMux()
    if name == "zellij":
        from .zellij import ZellijMux

        return ZellijMux()
    raise MuxError(
        f"unknown multiplexer backend: FLEET_MUX={name!r} "
        f"(expected one of: {', '.join(BACKENDS)})"
    )


# ---------------------------------------------------------------------------
# Window-name helpers (``<task>·<role>`` convention)
# ---------------------------------------------------------------------------


def matching_task_window_names(window_names: Sequence[str], task_id: str) -> list[str]:
    """Filter ``window_names`` to entries that belong to ``task_id``."""
    prefix = f"{task_id}·"
    return [
        name
        for name in window_names
        if name == task_id or name.startswith(prefix)
    ]


def task_window_names(session: str, task_id: str, *, backend: Mux | None = None) -> list[str]:
    """Return the windows in ``session`` that belong to ``task_id``."""
    m = backend or get()
    return matching_task_window_names(m.list_windows(session), task_id)


def kill_task_windows(session: str, task_id: str, *, backend: Mux | None = None) -> None:
    """Kill all windows in ``session`` that belong to ``task_id``."""
    m = backend or get()
    for window in task_window_names(session, task_id, backend=m):
        m.kill_window(session, window)


# ---------------------------------------------------------------------------
# Keystroke steps
# ---------------------------------------------------------------------------


def send_step(
    session: str,
    window: str,
    step: str | Key,
    *,
    enter: bool,
    backend: Mux | None = None,
) -> None:
    """Send one ``(text | Key, press_enter)`` step (see ``session_rename_keys``).

    A :class:`Key` is pressed as a key; a ``str`` is typed literally. Either
    way Enter follows when ``enter`` is true.
    """
    m = backend or get()
    if isinstance(step, Key):
        m.send_key(session, window, step)
        if enter:
            m.send_key(session, window, "Enter")
    else:
        m.send_text(session, window, step, enter=enter)


__all__ = [
    "BACKENDS",
    "DEFAULT_BACKEND",
    "Key",
    "Mux",
    "MuxError",
    "backend_name",
    "backend_selection",
    "default_backend_name",
    "disabled_by_env",
    "get",
    "kill_task_windows",
    "matching_task_window_names",
    "parse_key",
    "send_step",
    "set_backend",
    "task_window_names",
]
