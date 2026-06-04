"""Vendor adapter registry: 1 vendor = 1 file.

Adding a vendor means adding ``<vendor>.py`` here and one line to
``REGISTRY`` — nothing else. ``agents.parse_spec`` / ``agents.cli_command``
and the prompt deliverer all read this map, so there is no vendor list to
keep in sync anywhere else. Registration is an explicit import dict (no
entry_points / plugin magic) so the supported vendors are obvious here.
"""
from __future__ import annotations

from .base import VendorAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter

REGISTRY: dict[str, type[VendorAdapter]] = {
    ClaudeAdapter.name: ClaudeAdapter,
    CodexAdapter.name: CodexAdapter,
}

__all__ = ["VendorAdapter", "ClaudeAdapter", "CodexAdapter", "REGISTRY"]
