"""Adapter for OpenAI's ``codex`` CLI."""
from __future__ import annotations

import re

from .base import VendorAdapter


class CodexAdapter(VendorAdapter):
    name = "codex"

    ready = re.compile(r"(?m)^\s*›(?!\s*\d+\.)")
    gate = re.compile(
        r"(?im)"
        r"(?:^\s*(?:[›❯]\s*)?1\.\s*Update now\b|"
        r"^\s*[›❯]\s*\d+\.\s*(?:yes|continue|proceed|allow|trust|sign in|log in)\b|"
        r"do you trust the contents of this directory|yes,\s*continue|"
        r"sign in|login|log in|authentication|authenticate|api key)"
    )

    # codex prints an update prompt on startup; the launch command
    # suppresses it via ``check_for_update_on_startup=false``.
    suppress_update_check = True

    # codex intermittently drops a bare Enter sent right after a paste (the
    # same swallow the /rename dance works around — see session_rename_keys),
    # so a single submit Enter can leave the pasted pointer in the composer,
    # unsubmitted, until the deliverer times out and the task is marked failed
    # (Issue #179). Re-pressing Enter until the inbox_seen ack lands recovers
    # it. Pressing Enter again once the prompt has already submitted is a
    # harmless no-op — codex ignores a bare Enter on an empty composer whether
    # idle or mid-turn (verified live) — so this never double-submits.
    submit_retries = 6
    submit_retry_interval_seconds = 2.0

    @classmethod
    def cli_command(cls, model: str) -> list[str]:
        return [
            "codex",
            "-c",
            "check_for_update_on_startup=false",
            "--dangerously-bypass-approvals-and-sandbox",
            "-m",
            model,
        ]

    @classmethod
    def session_rename_keys(cls, name: str) -> list[tuple[str, bool]]:
        # codex (cli 0.132) has no launch-time naming flag; it renames the
        # session via the TUI `/rename` slash command. Each step is
        # (text, press_enter); the deliverer settles between steps.
        #
        # Verified live (see PR #145 / outbox) — two non-obvious findings:
        #  1. Typing `/rename` and Enter back-to-back does NOT open the popup
        #     (the Enter is swallowed by the autocomplete menu). The text and
        #     the Enter must be separate steps so a settle lands between them.
        #  2. The rename popup pre-fills with the thread's current name, so a
        #     `C-u` (kill-line) is needed to clear the field before typing —
        #     otherwise the new name is appended to the old one.
        return [
            ("/rename", False),  # type the slash command; let the menu render
            ("", True),          # Enter opens the rename popup
            ("C-u", False),      # clear any pre-filled text in the field
            (name, False),       # type the new name
            ("", True),          # Enter confirms
        ]
