"""Adapter for Anthropic's ``claude`` CLI."""
from __future__ import annotations

import json
import re
from pathlib import Path

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

    @classmethod
    def usage_from_session(
        cls, *, cwd: str | Path, home: Path | None = None
    ) -> dict | None:
        """Sum token usage from claude's session JSONL for ``cwd``.

        claude stores one session-log dir per working directory under
        ``~/.claude/projects/<escaped-cwd>/``, where the dir name is the cwd
        with every non-alphanumeric character replaced by ``-``. Each
        assistant line carries ``message.usage`` with ``input_tokens`` /
        ``output_tokens`` (plus cache fields). Input tokens are reported
        *excluding* cache, so the RAW input total is
        ``input_tokens + cache_creation_input_tokens + cache_read_input_tokens``.

        Sums across every ``*.jsonl`` in the dir (all sessions for this task's
        worktree). Returns ``None`` when the dir is missing or no usage is
        found; never raises.
        """
        proj = (home or Path.home()) / ".claude" / "projects" / _escape_cwd(cwd)
        if not proj.is_dir():
            return None
        input_tokens = 0
        output_tokens = 0
        found = False
        try:
            log_files = sorted(proj.glob("*.jsonl"))
        except OSError:
            return None
        for log_file in log_files:
            try:
                text = log_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                message = record.get("message") if isinstance(record, dict) else None
                usage = message.get("usage") if isinstance(message, dict) else None
                if not isinstance(usage, dict):
                    continue
                inp = (
                    _as_int(usage.get("input_tokens"))
                    + _as_int(usage.get("cache_creation_input_tokens"))
                    + _as_int(usage.get("cache_read_input_tokens"))
                )
                out = _as_int(usage.get("output_tokens"))
                if inp or out:
                    found = True
                input_tokens += inp
                output_tokens += out
        if not found:
            return None
        return {"input_tokens": input_tokens, "output_tokens": output_tokens}


def _escape_cwd(cwd: str | Path) -> str:
    """Map a working dir to claude's ``~/.claude/projects`` dir name."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def _as_int(value: object) -> int:
    """Return ``value`` as a non-negative int, or ``0`` when not a usable int."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    return 0
