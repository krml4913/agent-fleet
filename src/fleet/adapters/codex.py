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
