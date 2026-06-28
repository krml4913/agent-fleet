"""The vendor adapter interface.

A ``VendorAdapter`` captures everything vendor-specific about launching
and driving an agent CLI inside a tmux pane. Subclass it once per vendor
in ``<vendor>.py`` and register the class in ``fleet.adapters.REGISTRY``.
Adding a vendor must mean adding one file plus one registry line — nothing
scattered across ``agents.py`` / ``prompt_deliverer.py`` / the launchers.
"""
from __future__ import annotations

import re
from pathlib import Path


class VendorAdapter:
    """Base class for a single vendor's adapter.

    Subclasses set the class attributes below and override
    :meth:`cli_command`. Adapters are used as classes (not instances):
    ``REGISTRY[vendor].cli_command(model)`` / ``REGISTRY[vendor].ready``.
    """

    #: Vendor key used in ``vendor:model`` specs (e.g. ``"claude"``).
    name: str

    #: Matches a captured pane when the CLI is at its input prompt and
    #: ready to receive the driver prompt.
    ready: re.Pattern[str]

    #: Matches a captured pane when the CLI is blocked on a boot gate
    #: (login / update prompt / directory-trust menu) a human must clear.
    gate: re.Pattern[str]

    #: Whether the CLI shows an update-check prompt on startup that the
    #: launch command suppresses (folded into :meth:`cli_command`).
    suppress_update_check: bool = False

    #: Extra submit-Enter retries the prompt deliverer makes while waiting for
    #: the ``inbox_seen`` ack. Some TUIs intermittently drop a bare Enter sent
    #: right after a paste or a menu/popup transition, leaving the pasted prompt
    #: sitting in the composer unsubmitted; re-pressing Enter recovers it.
    #: ``0`` means submit exactly once — the right value when a single Enter
    #: reliably submits (claude). A vendor that needs the safety net overrides
    #: this with a positive count (codex).
    submit_retries: int = 0

    #: Seconds between submit-Enter retries (only consulted when
    #: :attr:`submit_retries` is positive).
    submit_retry_interval_seconds: float = 2.0

    @classmethod
    def cli_command(cls, model: str) -> list[str]:
        """Return the argv used to launch this vendor's CLI for ``model``.

        Higher layers may append further flags (mode toggles, prompt
        paths) on top of the returned list.
        """
        raise NotImplementedError

    @classmethod
    def session_name_launch_args(cls, name: str) -> list[str]:
        """argv appended at launch to set the session display name.

        ``[]`` when the vendor has no launch-time naming flag — then the
        session is named post-boot via :meth:`session_rename_keys` instead.
        """
        return []

    @classmethod
    def session_rename_keys(cls, name: str) -> list[tuple[str, bool]]:
        """Post-ready keystroke steps to rename the session.

        For vendors with no launch-time naming flag. Each step is
        ``(text, press_enter)``; the driver sends them in order once the
        pane is ready. ``[]`` when naming happens at launch (see
        :meth:`session_name_launch_args`).
        """
        return []

    @classmethod
    def usage_from_session(
        cls, *, cwd: str | Path, home: Path | None = None
    ) -> dict | None:
        """Return RAW token usage for the agent that ran in ``cwd``, or ``None``.

        Read-side only: the adapter parses this vendor's OWN already-written
        session/usage log at the terminal transition — there is no live stream
        and no polling. ``cwd`` is the directory the agent ran in (a task's
        worktree, unique per task); ``home`` overrides the home directory for
        tests (``None`` means :meth:`Path.home`).

        Returns ``{"input_tokens": int, "output_tokens": int}`` (an optional
        approximate ``"cost"`` may be added by a vendor that can compute one).
        The base default returns ``None`` — a vendor that cannot report usage
        simply contributes nothing rather than erroring, and a missing or
        unparseable log degrades to ``None`` rather than raising.
        """
        return None
