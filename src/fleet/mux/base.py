"""The terminal-multiplexer backend interface.

A :class:`Mux` backend knows how to start sessions, open windows, type text,
press keys, paste, capture a pane and attach a client. Like the old
``fleet.tmux`` module it is **mechanism-only**: it does not know about fleet
concepts such as driver, formation or task. Higher layers compose those on top
(see the window-name helpers in :mod:`fleet.mux`).

Addressing is always ``(session, window)`` by *name*. A backend that addresses
panes by id internally resolves the name on every call.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence


class MuxError(RuntimeError):
    """Raised when a multiplexer operation fails."""


def disabled_by_env() -> bool:
    """True when ``FLEET_NO_MUX`` (or its old alias ``FLEET_NO_TMUX``) is set.

    Lets tests (and the subprocesses they spawn) refuse to touch a real
    multiplexer, so the suite never leaks a stray ``fleet-<label>`` session.
    Mirrors the ``FLEET_NO_NOTIFY`` guard in ``notify.py``.
    """
    return bool(os.environ.get("FLEET_NO_MUX") or os.environ.get("FLEET_NO_TMUX"))


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

#: Named (non-character) keys every backend must translate.
NAMED_KEYS: frozenset[str] = frozenset(
    {
        "Enter",
        "Escape",
        "Tab",
        "Backspace",
        "Space",
        "Up",
        "Down",
        "Left",
        "Right",
    }
)

#: Modifier prefixes accepted in normalized key names (``Ctrl-u``, ``Alt-x``).
MODIFIERS: tuple[str, ...] = ("Ctrl", "Alt")


@dataclass(frozen=True)
class Key:
    """An explicit key press, as opposed to text to type.

    ``name`` is a normalized, backend-neutral key name: one of
    :data:`NAMED_KEYS` (``"Enter"``), or a modifier plus one character
    (``"Ctrl-u"``, ``"Alt-b"``). Each backend translates it to its own syntax
    (tmux ``C-u``, zellij ``Ctrl u``). Validated on construction.
    """

    name: str

    def __post_init__(self) -> None:
        parse_key(self.name)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.name


def parse_key(name: str) -> tuple[str | None, str]:
    """Split a normalized key name into ``(modifier, base)``.

    ``"Enter"`` → ``(None, "Enter")``; ``"Ctrl-u"`` → ``("Ctrl", "u")``.
    Raises :class:`MuxError` for anything that is not a normalized key name.
    """
    if name in NAMED_KEYS:
        return None, name
    for mod in MODIFIERS:
        prefix = f"{mod}-"
        if name.startswith(prefix):
            base = name[len(prefix):]
            if len(base) == 1 and base.isprintable() and not base.isspace():
                return mod, base
            break
    raise MuxError(
        f"unknown key name: {name!r} "
        f"(expected one of {sorted(NAMED_KEYS)} or Ctrl-<char> / Alt-<char>)"
    )


@dataclass(frozen=True)
class PaneInfo:
    """One terminal pane, as reported by :meth:`Mux.list_panes`.

    ``window`` is the name of the window (zellij: tab) that holds the pane and
    ``window_id`` its backend id — the way to address that window when its name
    is unknown or not unique (:meth:`Mux.rename_window`). ``title`` is the pane
    title: whatever the program in the pane set via the terminal title escape
    (``claude --name X`` shows up here), else the backend's default.
    """

    window: str
    window_id: str
    title: str


# ---------------------------------------------------------------------------
# Backend interface
# ---------------------------------------------------------------------------


class Mux:
    """Base class for a multiplexer backend.

    Required operations raise :class:`NotImplementedError` here. The optional
    ones (:meth:`preload_paste`, :meth:`drop_paste`, :meth:`kill_session_hint`)
    have safe defaults so a backend without the concept can ignore them.
    Every failing operation raises :class:`MuxError` (or a subclass).
    """

    #: Backend name, e.g. ``"tmux"`` / ``"zellij"``.
    name: str = "?"

    #: Whether :meth:`kill_window` also ends a ``fleet-agent`` process that
    #: was started from *inside* that window (e.g. a driver's ``done``).
    #: When true, a cross-stage advance run from a task pane is deferred to a
    #: detached helper (:mod:`fleet.deferred_launch`) so the caller is not
    #: killed before the next stage's window exists. False for tmux (the
    #: existing behavior is kept); true for zellij on Windows, where closing a
    #: tab ends every process attached to its console.
    window_close_kills_caller: bool = False

    # -- lifecycle -----------------------------------------------------------

    def available(self) -> bool:
        """Whether the backend binary is usable (and not disabled by env)."""
        raise NotImplementedError

    def session_exists(self, session: str) -> bool:
        raise NotImplementedError

    def new_session(
        self,
        session: str,
        *,
        window: str | None = None,
        argv: Sequence[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Create a detached session with one initial window.

        ``window`` names the first window (backend default when ``None``).
        When ``argv`` is given the window runs that command; ``env`` is the
        per-window environment and ``cwd`` its working directory.
        """
        raise NotImplementedError

    def new_window(
        self,
        session: str,
        window: str,
        *,
        argv: Sequence[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Open a window in ``session`` without focusing it, running ``argv``."""
        raise NotImplementedError

    def list_windows(self, session: str) -> list[str]:
        raise NotImplementedError

    def kill_window(self, session: str, window: str) -> None:
        raise NotImplementedError

    def kill_session(self, session: str) -> None:
        raise NotImplementedError

    def list_panes(self, session: str) -> list[PaneInfo]:
        """The terminal panes of every window in ``session`` (plugin panes excluded)."""
        raise NotImplementedError

    def rename_window(self, session: str, window_id: str, new_name: str) -> None:
        """Rename the window with backend id ``window_id`` (from :meth:`list_panes`)."""
        raise NotImplementedError

    # -- input / output -------------------------------------------------------

    def send_text(
        self, session: str, window: str, text: str, *, enter: bool = True
    ) -> None:
        """Type ``text`` literally, then press Enter when ``enter`` is true.

        An empty ``text`` with ``enter=True`` presses Enter only.
        """
        raise NotImplementedError

    def send_key(self, session: str, window: str, key: str | Key) -> None:
        """Press one key given by its normalized name (``"Enter"``, ``"Ctrl-u"``)."""
        raise NotImplementedError

    def paste(self, session: str, window: str, text: str) -> None:
        """Paste ``text`` into the pane as a paste (not typed keystrokes)."""
        raise NotImplementedError

    def capture(self, session: str, window: str) -> str:
        """Return the pane's recent visible/history text."""
        raise NotImplementedError

    # -- attach ---------------------------------------------------------------

    def attach(self, session: str, window: str | None = None) -> int:
        """Attach the current terminal to ``session`` (landing on ``window``).

        May replace the current process (tmux on POSIX) and then never
        returns. Otherwise returns the attach client's exit code.
        """
        raise NotImplementedError

    def attach_hint(self, session: str, window: str | None = None) -> str:
        """The shell command a human would run to attach (for messages)."""
        raise NotImplementedError

    # -- optional -------------------------------------------------------------

    def preload_paste(self, name: str, text: str) -> str | None:
        """Stage ``text`` under ``name`` for a later *manual* paste by a human.

        Returns a short instruction telling the human how to paste it (e.g.
        tmux's named buffer → ``"press C-b ]"``), or ``None`` when the backend
        has no such concept (the default).
        """
        return None

    def drop_paste(self, name: str) -> None:
        """Best-effort removal of what :meth:`preload_paste` staged. Default no-op."""
        return None

    def kill_session_hint(self, session: str) -> str:
        """The shell command a human would run to kill ``session`` (for messages)."""
        return f"{self.name} kill-session {session}"
