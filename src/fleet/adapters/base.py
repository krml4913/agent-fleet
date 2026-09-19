"""The vendor adapter interface.

A ``VendorAdapter`` captures everything vendor-specific about launching
and driving an agent CLI inside a multiplexer pane. Subclass it once per vendor
in ``<vendor>.py`` and register the class in ``fleet.adapters.REGISTRY``.
Adding a vendor must mean adding one file plus one registry line — nothing
scattered across ``agents.py`` / ``prompt_deliverer.py`` / the launchers.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..mux.base import Key

#: One post-ready keystroke step: ``(text, press_enter)``. ``text`` is typed
#: literally; a :class:`~fleet.mux.base.Key` (e.g. ``Key("Ctrl-u")``) is
#: pressed as a key, translated to each multiplexer's syntax by the backend.
KeystrokeStep = tuple[str | Key, bool]


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

    #: The cursor glyph the CLI draws in front of the highlighted option of a
    #: selection menu (claude ``❯``, codex ``›``). Used by :meth:`is_dialog` to
    #: spot un-numbered menus. ``""`` disables the cursor/sibling heuristic.
    menu_cursor: str = ""

    #: How many trailing non-blank pane lines :meth:`is_dialog` inspects. The
    #: live dialog / input prompt is always at the bottom of the capture, so
    #: dialog-looking text further up (conversation history) is ignored.
    dialog_tail_lines: int = 10

    #: A selection-menu / dialog footer line (``Enter to confirm · Esc to …``).
    #: Anchored at line start so prose mentioning these words does not match;
    #: ``esc to interrupt`` is claude's busy hint, not a dialog.
    dialog_footer: re.Pattern[str] = re.compile(
        r"(?i)^\s*(?:enter to (?:confirm|select|continue|submit)\b|"
        r"esc to (?!interrupt)\w|press enter to continue\b)"
    )

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
    def is_dialog(cls, pane: str) -> bool:
        """Whether the bottom of ``pane`` shows a selection menu / dialog.

        Structural, not keyword-based: only the last :attr:`dialog_tail_lines`
        non-blank lines are inspected, and they must contain either a
        :attr:`dialog_footer` line or an un-numbered, *indented* cursor option
        (``  ❯ No, keep …``) whose next non-blank line is a sibling option
        aligned with the option text (``    Yes, use …``). The CLI's own input
        prompt sits at column 0, so it never looks like a cursor option.
        """
        return _dialog_in(_tail(pane.splitlines(), cls.dialog_tail_lines), cls)

    @classmethod
    def is_ready(cls, pane: str) -> bool:
        """Whether ``pane`` is at the input prompt, ready to receive text.

        :attr:`ready` must match, and no dialog structure may appear from the
        last ``ready`` match downwards (within the bottom tail). A selection
        menu's cursor line (``  ❯ No, keep browser tools off``) matches the
        bare ``ready`` regex, so without this veto the deliverer would paste
        into the dialog.
        """
        last = None
        for last in cls.ready.finditer(pane):
            pass
        if last is None:
            return False
        line_start = pane.rfind("\n", 0, last.start()) + 1
        below = _tail(pane[line_start:].splitlines(), cls.dialog_tail_lines)
        return not _dialog_in(below, cls)

    @classmethod
    def is_gated(cls, pane: str) -> bool:
        """Whether ``pane`` is blocked on a boot gate a human must clear.

        :attr:`gate` (login / trust / update wording, numbered menus) or a
        structural selection dialog at the bottom (:meth:`is_dialog`).
        """
        return bool(cls.gate.search(pane)) or cls.is_dialog(pane)

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
    def session_rename_keys(cls, name: str) -> list[KeystrokeStep]:
        """Post-ready keystroke steps to rename the session.

        For vendors with no launch-time naming flag. Each step is
        ``(text, press_enter)`` where ``text`` is either a string typed
        literally or a :class:`~fleet.mux.base.Key` pressed as a key
        (``Key("Ctrl-u")``); the driver sends them in order once the
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


def _tail(lines: list[str], n: int) -> list[str]:
    """The last ``n`` non-blank lines of ``lines``, in order."""
    return [line for line in lines if line.strip()][-n:] if n > 0 else []


_NUMBERED_OPTION = re.compile(r"\d+\.")


def _dialog_in(lines: list[str], adapter: type[VendorAdapter]) -> bool:
    """Whether ``lines`` (already non-blank) contain selection-dialog structure."""
    cursor = adapter.menu_cursor
    for i, line in enumerate(lines):
        if adapter.dialog_footer.match(line):
            return True
        if not cursor or i + 1 >= len(lines):
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        # An indented cursor followed by an un-numbered option label.
        if indent == 0 or not stripped.startswith(cursor):
            continue
        label = stripped[len(cursor):]
        text = label.lstrip()
        if not text or _NUMBERED_OPTION.match(text):
            continue  # numbered menus are the ``gate`` regex's job
        text_col = indent + len(cursor) + (len(label) - len(text))
        sibling = lines[i + 1]
        sib_text = sibling.lstrip()
        if len(sibling) - len(sib_text) == text_col and sib_text[:1].isalnum():
            return True
    return False
