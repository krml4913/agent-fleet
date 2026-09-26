"""Agent vendor / model spec resolution.

Specs look like ``vendor:model`` (e.g. ``claude:sonnet``,
``codex:o4-mini``). The supported vendors and everything vendor-specific
(launch command, pane ready/gate detection) come from the adapter
registry in :mod:`fleet.adapters` — adding a vendor is one file there, not
edits scattered across this module.

Anywhere a spec is accepted, an *agent alias* from the global config
(``agent_aliases`` in ``fleet-state/global/config.yaml``) may be written
instead; :func:`resolve_spec` turns it into the full spec once, where the spec
enters task / leader state, so everything downstream only ever sees a
``vendor:model`` spec.
"""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from .adapters import REGISTRY, KeystrokeStep


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


def is_alias(spec: str) -> bool:
    """True when ``spec`` is written as an alias name (no ``:``), not a full spec."""
    return isinstance(spec, str) and ":" not in spec and bool(spec.strip())


def resolve_spec(spec: str, aliases: dict[str, str] | None = None) -> str:
    """Resolve an agent alias to its full ``vendor:model`` spec and validate it.

    A value containing ``:`` is a spec and is validated with :func:`parse_spec`
    (returned stripped). Anything else is looked up in ``aliases`` (default:
    the global config's ``agent_aliases``); an unknown alias raises
    ``ValueError`` naming the known aliases. Alias targets are full specs by
    construction (:func:`fleet.config.normalize_alias`), so there are no chains.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError(f"agent spec must be 'vendor:model' or an agent alias, got {spec!r}")
    spec = spec.strip()
    if ":" in spec:
        parse_spec(spec)
        return spec
    from . import config as config_mod  # config imports this module

    if aliases is None:
        aliases = config_mod.load_aliases()
    if spec not in aliases:
        raise ValueError(config_mod.unknown_alias_message(spec, aliases))
    target = aliases[spec]
    parse_spec(target)
    return target


def cli_command(spec: str) -> list[str]:
    """Return the shell argv used to launch this agent inside a multiplexer pane.

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


def session_rename_keys(spec: str, name: str) -> list[KeystrokeStep]:
    """Post-ready keystroke steps that rename this agent's session.

    ``[]`` for vendors that name the session at launch (see
    :func:`session_name_launch_args`).
    """
    vendor, _model = parse_spec(spec)
    return REGISTRY[vendor].session_rename_keys(name)


def usage_from_session(
    spec: str, *, cwd, home: Path | None = None, pointer: str | None = None
) -> dict[str, Any] | None:
    """RAW token usage for the agent ``spec`` that ran in ``cwd``, or ``None``.

    Bridges to the vendor adapter (parallel to :func:`cli_command`). The
    adapter reads its own already-written session log; ``None`` means the
    vendor reported nothing (no implementation, or a missing/unparseable log).
    ``pointer`` narrows a shared ``cwd`` to the sessions one task's prompt
    pointer started (see ``VendorAdapter.usage_from_session``).
    """
    vendor, _model = parse_spec(spec)
    return REGISTRY[vendor].usage_from_session(cwd=cwd, home=home, pointer=pointer)


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


def claude_config_path() -> Path:
    """claude's global config: ``~/.claude.json``, or under ``$CLAUDE_CONFIG_DIR``."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(override).expanduser() if override else Path.home()
    return base / ".claude.json"


def claude_repo_trusted(repo_root, *, config_path=None) -> bool | None:
    """Whether claude has accepted its workspace-trust dialog for ``repo_root``.

    Read-only, like :func:`codex_repo_trusted`: fleet never writes claude's
    config. claude records the answer as
    ``projects["<repo root>"].hasTrustDialogAccepted`` in its global config,
    keyed by the git repo root — a task worktree resolves to its repo's key
    (worktrees never get an entry of their own), so ``repo_root`` is the
    project's ``repo``, not the worktree. Only that exact key counts: a trusted
    ancestor directory did not stop the dialog on a fresh worktree.

    ``None`` means "cannot tell" (config missing / unreadable / not a JSON
    object), so a caller must stay quiet rather than warn on a guess.
    """
    config = Path(config_path).expanduser() if config_path else claude_config_path()
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return False
    resolved = Path(repo_root).expanduser().resolve()
    for key in (str(resolved), resolved.as_posix()):
        entry = projects.get(key)
        if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True:
            return True
    return False


def claude_trust_hint(repo_root) -> str:
    """The one-time instruction for a repo claude has not trusted yet."""
    return (
        "claude has not accepted its workspace trust prompt for this repo yet, so the "
        "first claude driver will stop at \"Is this a project you created or one you "
        "trust?\" until someone chooses \"Yes, I trust this folder\" (fleet never "
        "answers it for you). Trust it once: run `claude` in "
        f"{repo_root} and accept the prompt."
    )
