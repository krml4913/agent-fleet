"""Captured (and derived) agent-CLI pane screens shared by the busy/idle tests.

``CLAUDE_BUSY`` is a real ``zellij action dump-screen`` of a claude pane in the
middle of a turn (claude 2.x on Windows, 160 cols; history trimmed and paths
scrubbed). The other claude screens are derived from it by changing only what
differs between the states, so they keep the exact real chrome (the NBSP after
``❯``, the titled composer rule, the footer):

* ``CLAUDE_IDLE`` — the same screen at a turn boundary (spinner line replaced by
  claude's finished-turn line, the busy-only tip line dropped).
* ``claude_stuck_composer`` — text typed into the composer of a busy claude whose
  submit Enter was swallowed. Per docs/windows-support.md §4.9 a dump of typed
  composer text has no space after ``❯`` and wraps at the pane width.

The codex screens are hand-written from codex's documented TUI (not captured).
"""
from __future__ import annotations

RULE = "─" * 160
TITLED_RULE = "─" * 128 + " agent-fleet-example-implementer ─"
FOOTER = "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"

_HISTORY = (
    "❯ Fix the flaky test in the parser module\n"
    "  Read 4 files, ran 2 shell commands\n"
    "● I found the race in the tokenizer; patching it now.\n"
    "  Searched for 1 pattern, read 2 files\n"
)

# Real capture: turn running, composer visible, spinner status line above it.
CLAUDE_BUSY = (
    _HISTORY
    + "\n✢ Frosting… (48s · ↓ 4.0k tokens)\n"
    "  ⎿ \xa0Tip: Use /btw to ask a quick side question without interrupting Claude's current work\n"
    f"\n{TITLED_RULE}\n❯\xa0\n{RULE}\n{FOOTER}\n"
)

# Older / narrower layout: the interrupt hint instead of the token counter.
CLAUDE_BUSY_ESC_HINT = (
    _HISTORY
    + "\n✻ Thinking… (esc to interrupt)\n"
    f"\n{TITLED_RULE}\n❯\xa0\n{RULE}\n{FOOTER}\n"
)

# Turn boundary: finished-turn line, empty composer.
CLAUDE_IDLE = (
    _HISTORY
    + "\n✻ Cooked for 48s\n"
    f"\n{TITLED_RULE}\n❯\xa0\n{RULE}\n{FOOTER}\n"
)


def claude_stuck_composer(text: str, *, width: int = 100) -> str:
    """A busy claude with ``text`` typed into (not submitted from) the composer."""
    typed = "❯" + text  # dump: no space after the glyph while text is in the composer
    rows = [typed[:width]] + [
        "  " + typed[i:i + width - 2] for i in range(width, len(typed), width - 2)
    ]
    return (
        _HISTORY
        + "\n✢ Frosting… (52s · ↓ 4.3k tokens)\n"
        + f"\n{TITLED_RULE}\n" + "\n".join(rows) + f"\n{RULE}\n{FOOTER}\n"
    )


def claude_submitted_echo(text: str) -> str:
    """The same text after a successful submit: echoed into history, composer empty."""
    return (
        _HISTORY
        + f"❯ {text}\n"
        + "\n✢ Frosting… (1s)\n"
        + f"\n{TITLED_RULE}\n❯\xa0\n{RULE}\n{FOOTER}\n"
    )


CODEX_BUSY = (
    "• I'll look at the failing test first.\n\n"
    "• Working (12s • esc to interrupt)\n\n"
    "› Find and fix a bug in @filename\n\n"
    "  gpt-5.5 high · ~/proj\n"
)

CODEX_IDLE = (
    "• Fixed the race; tests pass.\n\n"
    "› Find and fix a bug in @filename\n\n"
    "  gpt-5.5 high · ~/proj\n"
)
