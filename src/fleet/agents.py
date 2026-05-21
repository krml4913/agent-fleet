"""Agent vendor / model spec resolution.

Specs look like ``vendor:model`` (e.g. ``claude:sonnet``,
``codex:o4-mini``). MVP supports claude + codex; OpenAI / Gemini /
local LLMs are out of scope per design doc §13.2.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

SUPPORTED_VENDORS: frozenset[str] = frozenset({"claude", "codex"})


def parse_spec(spec: str) -> tuple[str, str]:
    """Split ``vendor:model`` into a tuple. Raises ``ValueError`` on bad input."""
    if not isinstance(spec, str) or ":" not in spec:
        raise ValueError(f"agent spec must be 'vendor:model', got {spec!r}")
    vendor, _, model = spec.partition(":")
    vendor = vendor.strip()
    model = model.strip()
    if vendor not in SUPPORTED_VENDORS:
        raise ValueError(
            f"unsupported vendor {vendor!r}; supported: {sorted(SUPPORTED_VENDORS)}"
        )
    if not model:
        raise ValueError(f"empty model in agent spec: {spec!r}")
    return vendor, model


def cli_command(spec: str) -> list[str]:
    """Return the shell argv used to launch this agent inside a tmux pane.

    Both ``claude`` and ``codex`` CLIs accept ``--model``. Higher layers
    can append further flags (mode toggles, prompt paths) on top.
    """
    vendor, model = parse_spec(spec)
    if vendor == "claude":
        return ["claude", "--dangerously-skip-permissions", "--model", model]
    if vendor == "codex":
        return ["codex", "--dangerously-bypass-approvals-and-sandbox", "-m", model]
    raise AssertionError("unreachable; parse_spec already filtered vendor")


def codex_repo_trusted(repo_root, *, config_path=None) -> bool:
    """Return True if codex trusts ``repo_root`` (read-only check)."""
    config = (
        Path(config_path).expanduser()
        if config_path
        else Path.home() / ".codex" / "config.toml"
    )
    try:
        with config.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False

    repo_key = str(Path(repo_root).expanduser().resolve())
    project = data.get("projects", {}).get(repo_key, {})
    return project.get("trust_level") == "trusted"
