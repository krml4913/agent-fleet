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
from typing import Any

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


def session_name_launch_args(spec: str, name: str) -> list[str]:
    """argv to append at launch to set this agent's session display name.

    ``[]`` for vendors with no launch-time naming flag (use
    :func:`session_rename_keys` post-ready instead).
    """
    vendor, _model = parse_spec(spec)
    return REGISTRY[vendor].session_name_launch_args(name)


def session_rename_keys(spec: str, name: str) -> list[tuple[str, bool]]:
    """Post-ready keystroke steps that rename this agent's session.

    ``[]`` for vendors that name the session at launch (see
    :func:`session_name_launch_args`).
    """
    vendor, _model = parse_spec(spec)
    return REGISTRY[vendor].session_rename_keys(name)


def usage_from_session(
    spec: str, *, cwd, home: Path | None = None
) -> dict[str, Any] | None:
    """RAW token usage for the agent ``spec`` that ran in ``cwd``, or ``None``.

    Bridges to the vendor adapter (parallel to :func:`cli_command`). The
    adapter reads its own already-written session log; ``None`` means the
    vendor reported nothing (no implementation, or a missing/unparseable log).
    """
    vendor, _model = parse_spec(spec)
    return REGISTRY[vendor].usage_from_session(cwd=cwd, home=home)


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
