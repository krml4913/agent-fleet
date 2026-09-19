"""The vendor adapter interface.

A ``VendorAdapter`` captures everything vendor-specific about launching
and driving an agent CLI inside a multiplexer pane. Subclass it once per vendor
in ``<vendor>.py`` and register the class in ``fleet.adapters.REGISTRY``.
Adding a vendor must mean adding one file plus one registry line — nothing
scattered across ``agents.py`` / ``prompt_deliverer.py`` / the launchers.
"""
from __future__ import annotations

import json
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

    #: Matches the CLI's "a turn is running" indicator (claude's spinner status
    #: line / ``esc to interrupt`` hint). ``ready`` alone cannot tell idle from
    #: busy: these TUIs keep the input composer visible while working. ``None``
    #: means the vendor has no known busy indicator, so :meth:`is_busy` is
    #: always ``False`` — the conservative default for a poller (an unknown
    #: pattern must never strand a queue behind a permanent "busy").
    busy: re.Pattern[str] | None = None

    #: How many trailing non-blank pane lines :meth:`is_busy` inspects. Wider
    #: than :attr:`dialog_tail_lines`: the spinner line sits above the composer
    #: box and any task list / queued-message lines, not directly on it.
    busy_tail_lines: int = 16

    #: Matches the placeholder a CLI collapses a long paste into (claude's
    #: ``[Pasted text #1 +3 lines]``), for :meth:`composer_holds`. ``None``
    #: when the vendor does not collapse.
    pasted_marker: re.Pattern[str] | None = None

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
    def is_busy(cls, pane: str) -> bool:
        """Whether ``pane`` shows a running turn (spinner / interrupt hint).

        Only the last :attr:`busy_tail_lines` non-blank lines are inspected, so
        a busy hint quoted further up in the conversation is ignored. Always
        ``False`` for a vendor without a :attr:`busy` pattern.
        """
        if cls.busy is None:
            return False
        tail = "\n".join(_tail(pane.splitlines(), cls.busy_tail_lines))
        return cls.busy.search(tail) is not None

    @classmethod
    def is_idle(cls, pane: str) -> bool:
        """Whether ``pane`` is at a real turn boundary: ready for input AND not busy.

        The bar for *unsolicited* injection into a pane a human or leader agent
        is working in (the leader notifier). :meth:`is_ready` alone is the right
        bar for a freshly booted driver pane (the prompt deliverer).
        """
        return cls.is_ready(pane) and not cls.is_busy(pane)

    @classmethod
    def composer_holds(cls, pane: str, text: str) -> bool:
        """Whether ``text`` is still sitting, unsubmitted, in the input composer.

        The composer is the last ``ready`` line and everything below it. Matching
        squeezes all whitespace out of both sides (the composer wraps long text
        across lines and a dump can drop the space after the prompt glyph) and
        compares only the head of ``text``. A message already submitted is echoed
        into the history *above* the composer, so it does not count.
        """
        last = None
        for last in cls.ready.finditer(pane):
            pass
        if last is None:
            return False
        composer = pane[pane.rfind("\n", 0, last.start()) + 1:]
        probe = _squeeze(text)[:_COMPOSER_PROBE_CHARS]
        if probe and probe in _squeeze(composer):
            return True
        return cls.pasted_marker is not None and cls.pasted_marker.search(composer) is not None

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
        cls,
        *,
        cwd: str | Path,
        home: Path | None = None,
        pointer: str | None = None,
    ) -> dict | None:
        """Return RAW token usage for the agent that ran in ``cwd``, or ``None``.

        Read-side only: the adapter parses this vendor's OWN already-written
        session/usage log at the terminal transition — there is no live stream
        and no polling. ``cwd`` is the directory the agent ran in (a task's
        worktree, unique per task); ``home`` overrides the home directory for
        tests (``None`` means :meth:`Path.home`).

        ``pointer`` is for a ``cwd`` shared with other sessions (a
        workspace=none task runs in the project root, next to the leader and
        other tasks): the prompt-pointer text fleet pasted into this task's
        pane (see :func:`fleet.prompt_pointer.pointer_text`). When given, only
        sessions that were started by that pointer count (see
        :func:`session_started_by_pointer`); ``None`` counts every session in
        ``cwd``.

        Returns ``{"input_tokens": int, "output_tokens": int}`` (an optional
        approximate ``"cost"`` may be added by a vendor that can compute one).
        The base default returns ``None`` — a vendor that cannot report usage
        simply contributes nothing rather than erroring, and a missing or
        unparseable log degrades to ``None`` rather than raising.
        """
        return None


# The fixed lead-in of ``prompt_pointer.pointer_text`` — how a pasted pointer is
# recognised without knowing the task it names.
POINTER_LEAD = "Read the prompt file at this path before doing anything else"


def parse_jsonl_records(text: str) -> list:
    """Parse a JSONL log into records, skipping blank and malformed lines."""
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except (ValueError, TypeError):
            continue
    return records


def _strings(value: object):
    """Yield every string in a parsed-JSON value (vendor-log-format agnostic)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def session_started_by_pointer(records: list, pointer: str) -> bool:
    """Whether the session whose parsed log ``records`` is the one ``pointer`` started.

    A driver's first input is the pointer fleet pastes into its pane, so the
    FIRST record in the log that carries the pointer lead-in must carry this
    task's exact ``pointer`` text. Later mentions (a leader reading the
    prompt file, a session grepping fleet's own source) are ignored — only the
    first counts — so a shared project root can't attribute another session's
    tokens to the task.
    """
    for record in records:
        for text in _strings(record):
            if POINTER_LEAD in text:
                return pointer in text
    return False


def _tail(lines: list[str], n: int) -> list[str]:
    """The last ``n`` non-blank lines of ``lines``, in order."""
    return [line for line in lines if line.strip()][-n:] if n > 0 else []


#: How much of an injected text's head :meth:`VendorAdapter.composer_holds`
#: looks for (after whitespace is squeezed out) — enough to be unique, short
#: enough to sit on the composer's first wrapped line.
_COMPOSER_PROBE_CHARS = 40


def _squeeze(s: str) -> str:
    """``s`` without any whitespace (incl. the NBSP claude draws after its prompt glyph)."""
    return re.sub(r"\s+", "", s)


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
