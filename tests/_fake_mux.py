"""A recording fake :class:`fleet.mux.Mux` backend for unit tests.

Install it process-wide with :func:`use_fake_mux` (a context manager) so every
``fleet.mux.get()`` call in the code under test sees it::

    with use_fake_mux(sessions={"fleet-main": ["leader"]}) as fake:
        run_something()
    fake.calls_named("new_window")

The fake keeps a tiny in-memory model (sessions → window names) so
``session_exists`` / ``list_windows`` / ``kill_window`` behave coherently, and
records every call as ``(method_name, args, kwargs)`` in ``fake.calls``.
Failures are injected with ``fake.fail[method_name] = MuxError(...)`` and side
effects with ``fake.on[method_name] = callable``.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Same hermetic guard as tests/_fleet_test_helpers.py: never touch a real
# multiplexer unless live tests are opted into.
if not (os.environ.get("FLEET_LIVE_TMUX") or os.environ.get("FLEET_LIVE_ZELLIJ")):
    os.environ.setdefault("FLEET_NO_MUX", "1")

from fleet import mux  # noqa: E402
from fleet.mux.base import Key, Mux, MuxError, parse_key  # noqa: E402


class FakeMux(Mux):
    # Reports itself as tmux (and mirrors tmux's hint strings) so user-facing
    # messages asserted by command tests read exactly as on the default backend.
    name = "tmux"

    def __init__(
        self,
        *,
        available: bool = True,
        sessions: dict[str, list[str]] | None = None,
        capture: str | list[str] = "",
        attach_rc: int = 0,
        preload_hint: str | None = "inside the pane press C-b ], then Enter",
    ) -> None:
        self._available = available
        self.sessions: dict[str, list[str]] = {
            k: list(v) for k, v in (sessions or {}).items()
        }
        # A str is returned on every capture; a list is consumed one per call
        # (the last entry repeats).
        self._capture = capture
        self.attach_rc = attach_rc
        self.preload_hint = preload_hint
        self.calls: list[tuple[str, tuple, dict]] = []
        self.fail: dict[str, BaseException] = {}
        # Per-method side-effect hooks, called with the method's arguments
        # after the call is recorded (e.g. to append an ack event on Enter).
        self.on: dict[str, Any] = {}
        self.preloaded: dict[str, str] = {}

    # -- recording helpers ----------------------------------------------------

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((method, args, kwargs))
        exc = self.fail.get(method)
        if exc is not None:
            raise exc
        hook = self.on.get(method)
        if hook is not None:
            hook(*args, **kwargs)

    def calls_named(self, method: str) -> list[tuple[tuple, dict]]:
        return [(a, k) for (m, a, k) in self.calls if m == method]

    def method_names(self) -> list[str]:
        return [m for (m, _a, _k) in self.calls]

    def sent(self) -> list[tuple[str, str, str]]:
        """Input events in order: ``("text"|"key"|"paste", window, payload)``.

        ``send_text(..., enter=True)`` expands to a text event (when non-empty)
        followed by an ``Enter`` key event — the same stream a real pane sees.
        """
        out: list[tuple[str, str, str]] = []
        for m, a, k in self.calls:
            if m == "send_text":
                _session, window, text = a
                if text:
                    out.append(("text", window, text))
                if k.get("enter", True):
                    out.append(("key", window, "Enter"))
            elif m == "send_key":
                _session, window, key = a
                out.append(("key", window, key.name if isinstance(key, Key) else key))
            elif m == "paste":
                _session, window, text = a
                out.append(("paste", window, text))
        return out

    # -- Mux ------------------------------------------------------------------

    def available(self) -> bool:
        self._record("available")
        return self._available

    def session_exists(self, session: str) -> bool:
        self._record("session_exists", session)
        return session in self.sessions

    def new_session(self, session, *, window=None, argv=None, cwd=None, env=None):
        self._record("new_session", session, window=window, argv=argv, cwd=cwd, env=env)
        self.sessions[session] = [window or "0"]

    def new_window(self, session, window, *, argv=None, cwd=None, env=None):
        self._record("new_window", session, window, argv=argv, cwd=cwd, env=env)
        self.sessions.setdefault(session, []).append(window)

    def list_windows(self, session: str) -> list[str]:
        self._record("list_windows", session)
        if session not in self.sessions:
            raise MuxError(f"can't find session: {session}")
        return list(self.sessions[session])

    def kill_window(self, session: str, window: str) -> None:
        self._record("kill_window", session, window)
        windows = self.sessions.get(session, [])
        if window not in windows:
            raise MuxError(f"can't find window: {session}:{window}")
        windows.remove(window)

    def kill_session(self, session: str) -> None:
        self._record("kill_session", session)
        if self.sessions.pop(session, None) is None:
            raise MuxError(f"can't find session: {session}")

    def send_text(self, session, window, text, *, enter=True):
        self._record("send_text", session, window, text, enter=enter)

    def send_key(self, session, window, key):
        parse_key(key.name if isinstance(key, Key) else key)  # validate
        self._record("send_key", session, window, key)

    def paste(self, session, window, text):
        self._record("paste", session, window, text)

    def capture(self, session, window):
        self._record("capture", session, window)
        if isinstance(self._capture, list):
            if len(self._capture) > 1:
                return self._capture.pop(0)
            return self._capture[0] if self._capture else ""
        return self._capture

    def attach(self, session, window=None):
        self._record("attach", session, window)
        return self.attach_rc

    def attach_hint(self, session, window=None):
        target = session if window is None else f"{session}:{window}"
        return f"tmux attach -t {target}"

    def preload_paste(self, name, text):
        self._record("preload_paste", name, text)
        self.preloaded[name] = text
        return self.preload_hint

    def drop_paste(self, name):
        self._record("drop_paste", name)
        self.preloaded.pop(name, None)

    def kill_session_hint(self, session):
        return f"tmux kill-session -t {session}"


@contextlib.contextmanager
def use_fake_mux(fake: FakeMux | None = None, **kwargs: Any) -> Iterator[FakeMux]:
    """Install a :class:`FakeMux` as the process-wide backend for the block."""
    fake = fake if fake is not None else FakeMux(**kwargs)
    previous = mux.set_backend(fake)
    try:
        yield fake
    finally:
        mux.set_backend(previous)
