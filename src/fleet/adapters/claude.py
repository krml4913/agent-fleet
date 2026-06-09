"""Adapter for Anthropic's ``claude`` CLI."""
from __future__ import annotations

import re

from .base import VendorAdapter


class ClaudeAdapter(VendorAdapter):
    name = "claude"

    ready = re.compile(r"(?m)^\s*❯(?!\s*\d+\.)")
    gate = re.compile(
        r"(?im)"
        r"(?:login|log in|sign in|authentication|authenticate|"
        r"^\s*(?:[›❯]\s*)?1\.\s*Update now\b|"
        r"^\s*[›❯]\s*\d+\.\s*(?:yes|continue|proceed|allow|trust|sign in|log in)\b|"
        r"trust (?:this )?(?:folder|directory|workspace)|do you trust|continue\?)"
    )

    @classmethod
    def cli_command(cls, model: str) -> list[str]:
        return ["claude", "--dangerously-skip-permissions", "--model", model]

    @classmethod
    def session_name_launch_args(cls, name: str) -> list[str]:
        # claude has a launch flag for the resumable session display name.
        return ["--name", name]
