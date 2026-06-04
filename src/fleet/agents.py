"""Agent vendor / model spec resolution.

Specs look like ``vendor:model`` (e.g. ``claude:sonnet``,
``codex:o4-mini``). The supported vendors and everything vendor-specific
(launch command, pane ready/gate detection) come from the adapter
registry in :mod:`fleet.adapters` — adding a vendor is one file there, not
edits scattered across this module.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from .adapters import REGISTRY


def __getattr__(name: str):
    # ``SUPPORTED_VENDORS`` derives from the registry keys and is computed
    # live so a vendor registered at runtime (e.g. in tests) is visible.
    if name == "SUPPORTED_VENDORS":
        return frozenset(REGISTRY)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def parse_spec(spec: str) -> tuple[str, str]:
    """Split ``vendor:model`` into a tuple. Raises ``ValueError`` on bad input."""
    if not isinstance(spec, str) or ":" not in spec:
        raise ValueError(f"agent spec must be 'vendor:model', got {spec!r}")
    vendor, _, model = spec.partition(":")
    vendor = vendor.strip()
    model = model.strip()
    if vendor not in REGISTRY:
        raise ValueError(
            f"unsupported vendor {vendor!r}; supported: {sorted(REGISTRY)}"
        )
    if not model:
        raise ValueError(f"empty model in agent spec: {spec!r}")
    return vendor, model


def cli_command(spec: str) -> list[str]:
    """Return the shell argv used to launch this agent inside a tmux pane.

    The argv comes from the vendor's adapter. Higher layers can append
    further flags (mode toggles, prompt paths) on top.
    """
    vendor, model = parse_spec(spec)
    return REGISTRY[vendor].cli_command(model)


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
