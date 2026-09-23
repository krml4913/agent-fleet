"""Global (cross-project) config: ``fleet-state/global/config.yaml``.

Project settings live in ``projects/<name>/project.yaml``; this file holds the
settings that are not per-project::

    mux: tmux
    leader_agent: claude:claude-opus-5-5
    agent_aliases:
      fast: claude:sonnet
      deep: claude:opus

``mux`` (the multiplexer backend, ``tmux`` | ``zellij``) is an *enumerable*
key: any value not in :data:`KEYS` is rejected. ``leader_agent`` (the default
agent spec for ``fleet leader``, §"Free-form keys" below) is *free-form*: it
is validated with the same ``vendor:model`` parser as ``fleet leader
--agent`` (:func:`fleet.agents.parse_spec`) rather than against an enumerable
set — see :data:`FREEFORM`. It may also name an agent alias.

``agent_aliases`` is the one map-valued key: alias name → full ``vendor:model``
spec, usable anywhere an agent spec is accepted (formation ``agent`` /
``peer_review.agent``, ``--agent``, ``leader_agent``). An alias name must match
:data:`ALIAS_NAME_RE` (so never contains ``:`` and never collides with a real
spec) and its target must be a full spec — alias-to-alias chains are rejected.
Aliases are resolved by :func:`fleet.agents.resolve_spec` once, when the spec
enters task / leader state. It is managed as dotted keys
(``fleet config set agent_aliases.<name> <spec>`` / ``unset``); see
:func:`set_alias` / :func:`unset_alias`.

Reads are tolerant, like :func:`fleet.notify.load_config`: a missing file means
"defaults", and an unreadable / malformed file or an invalid value only prints a
warning to stderr (once per process) and is ignored — a bad config never crashes
a command. Writes (:func:`set_value`, behind ``fleet config set``) go through
the locked atomic-write helpers and validate the key and value.

Selection precedence for a key is env > this file > the built-in default; the
env layer belongs to the consumer (see :func:`fleet.mux.backend_selection`).
``leader_agent`` has no env layer: its precedence is ``fleet leader --agent``
(the consumer's own flag) > this file > the built-in default.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Callable

from . import agents as agents_mod
from . import state as state_mod
from .locking import atomic_update
from .mux import BACKENDS, DEFAULT_BACKEND

import yaml  # noqa: E402  (after ``state`` has put the vendored PyYAML on sys.path)

CONFIG_FILE = "config.yaml"

#: Built-in default for ``leader_agent`` (also ``fleet leader``'s built-in
#: ``--agent`` default when neither the flag nor this config key is set).
DEFAULT_LEADER_AGENT = "claude:opus"

#: The map-valued key holding agent aliases (``name: vendor:model``).
ALIASES_KEY = "agent_aliases"

#: Valid alias names: no ``:`` (so an alias never collides with a spec) and no
#: ``.`` (so ``agent_aliases.<name>`` parses unambiguously).
ALIAS_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

#: Enumerable keys → the values each accepts.
KEYS: dict[str, tuple[str, ...]] = {"mux": BACKENDS}

#: Free-form keys: validated by calling the function here (it raises
#: ``ValueError`` on a bad spec, like :func:`fleet.agents.parse_spec` does)
#: rather than checked against an enumerable set of values. The second tuple
#: element is a short human description of the accepted format, for help text.
FREEFORM: dict[str, tuple[Callable[[str], None], str]] = {
    "leader_agent": (
        # Resolved against the configured aliases (like --agent): a full spec
        # or a known alias name.
        lambda value: agents_mod.resolve_spec(value),
        "vendor:model or an agent alias (validated like --agent, e.g. claude:opus)",
    ),
}

#: Every known key, enumerable or free-form, in display order.
ALL_KEYS: tuple[str, ...] = (*KEYS, *FREEFORM)

#: Built-in defaults (used when the key is absent from the file).
DEFAULTS: dict[str, str] = {"mux": DEFAULT_BACKEND, "leader_agent": DEFAULT_LEADER_AGENT}

_cache: tuple[Path, dict[str, str], dict[str, str]] | None = None


class ConfigError(ValueError):
    """An invalid key / value, or a config file that cannot be rewritten."""


def config_path() -> Path:
    """``fleet-state/global/config.yaml`` (may not exist)."""
    return state_mod.global_dir() / CONFIG_FILE


def normalize(key: str, value: str) -> str:
    """Validate ``key`` / ``value`` and return the canonical value.

    Raises :class:`ConfigError` listing the valid keys / values.
    """
    key = str(key).strip()
    if key in KEYS:
        canonical = str(value).strip().lower()
        if canonical not in KEYS[key]:
            raise ConfigError(
                f"invalid value for {key}: {value!r} (expected one of: {', '.join(KEYS[key])})"
            )
        return canonical
    if key in FREEFORM:
        validate, _help = FREEFORM[key]
        canonical = str(value).strip()
        try:
            validate(canonical)
        except ValueError as e:
            raise ConfigError(f"invalid value for {key}: {value!r} ({e})") from e
        return canonical
    raise ConfigError(
        f"unknown config key: {key!r} (known keys: {', '.join(ALL_KEYS)})"
    )


def _read_raw(path: Path, text: str) -> dict:
    """Parse ``text`` (the contents of ``path``) into a mapping, or raise ConfigError."""
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping of key: value")
    return data


def load() -> dict[str, str]:
    """The valid, explicitly configured scalar values (``{}`` when the file is absent).

    Cached per process (keyed by path, so a different ``$FLEET_HOME`` reloads);
    :func:`reset_cache` and :func:`set_value` drop the cache. Never raises: an
    unreadable file, malformed YAML or an invalid value warns and is ignored.
    ``agent_aliases`` is not included; see :func:`load_aliases`.
    """
    return _load()[1]


def load_aliases() -> dict[str, str]:
    """The valid configured agent aliases (name → ``vendor:model``), tolerant like :func:`load`."""
    return _load()[2]


def _load() -> tuple[Path, dict[str, str], dict[str, str]]:
    global _cache
    path = config_path()
    if _cache is not None and _cache[0] == path:
        return _cache
    values: dict[str, str] = {}
    aliases: dict[str, str] = {}
    if path.is_file():
        try:
            raw = _read_raw(path, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ConfigError) as e:
            print(f"warn: global config unreadable, using defaults: {e}", file=sys.stderr)
            raw = {}
        raw_aliases = raw.get(ALIASES_KEY)
        if raw_aliases is not None and not isinstance(raw_aliases, dict):
            print(
                f"warn: {path}: {ALIASES_KEY} must be a mapping of name: vendor:model; ignoring",
                file=sys.stderr,
            )
        elif raw_aliases:
            for name, target in raw_aliases.items():
                try:
                    name, target = normalize_alias(name, target)
                except ConfigError as e:
                    print(f"warn: {path}: {e}; ignoring", file=sys.stderr)
                    continue
                aliases[name] = target
        # Publish the aliases before validating the scalar keys: leader_agent
        # may name an alias (FREEFORM → agents.resolve_spec → load_aliases()).
        _cache = (path, values, aliases)
        for key, value in raw.items():
            if key not in ALL_KEYS:
                continue  # unknown keys are ignored (forward compatible)
            try:
                values[key] = normalize(key, value)
            except ConfigError as e:
                print(f"warn: {path}: {e}; ignoring", file=sys.stderr)
    _cache = (path, values, aliases)
    return _cache


def reset_cache() -> None:
    """Forget the cached config so the next :func:`load` re-reads the file."""
    global _cache
    _cache = None


def get(key: str) -> tuple[str, str]:
    """``(value, source)`` for a known ``key``; source is ``config`` or ``default``."""
    if key not in ALL_KEYS:
        raise ConfigError(
            f"unknown config key: {key!r} (known keys: {', '.join(ALL_KEYS)})"
        )
    configured = load().get(key)
    if configured is not None:
        return configured, "config"
    return DEFAULTS[key], "default"


def normalize_alias(name: str, target: str) -> tuple[str, str]:
    """Validate an alias ``name`` / ``target`` and return them canonical.

    The name must match :data:`ALIAS_NAME_RE`; the target must be a full
    ``vendor:model`` spec (checked by :func:`fleet.agents.parse_spec`), so an
    alias pointing at another alias is rejected.
    """
    canonical_name = str(name).strip()
    if not ALIAS_NAME_RE.match(canonical_name):
        raise ConfigError(
            f"invalid agent alias name {name!r}: use letters, digits, '_' or '-' "
            f"(no ':' — an alias must not look like a vendor:model spec)"
        )
    if not isinstance(target, str):
        raise ConfigError(
            f"invalid target for agent alias {canonical_name!r}: {target!r} "
            f"(expected a vendor:model spec)"
        )
    canonical_target = target.strip()
    if ":" not in canonical_target:
        raise ConfigError(
            f"invalid target for agent alias {canonical_name!r}: {target!r} "
            f"(an alias must map to a full vendor:model spec; alias-to-alias "
            f"chains are not supported)"
        )
    try:
        agents_mod.parse_spec(canonical_target)
    except ValueError as e:
        raise ConfigError(
            f"invalid target for agent alias {canonical_name!r}: {target!r} ({e})"
        ) from e
    return canonical_name, canonical_target


def get_alias(name: str) -> str:
    """The configured target of alias ``name``; :class:`ConfigError` if unknown."""
    aliases = load_aliases()
    name = str(name).strip()
    if name not in aliases:
        raise ConfigError(unknown_alias_message(name, aliases))
    return aliases[name]


def unknown_alias_message(name: str, aliases: dict[str, str]) -> str:
    """Error text for an unknown alias, naming the known ones."""
    if aliases:
        known = f"known aliases: {', '.join(sorted(aliases))}"
    else:
        known = (
            "no aliases are defined; add one with "
            f"`fleet config set {ALIASES_KEY}.<name> <vendor:model>`"
        )
    return f"unknown agent alias {name!r} ({known})"


def set_alias(name: str, target: str) -> tuple[str, str]:
    """Validate and persist ``agent_aliases.<name>: <target>``; return both canonical."""
    name, target = normalize_alias(name, target)
    path = config_path()

    def _mutate(old_text: str) -> str:
        data = _read_raw(path, old_text)
        aliases = data.get(ALIASES_KEY)
        if aliases is None:
            aliases = {}
        if not isinstance(aliases, dict):
            raise ConfigError(f"{path}: {ALIASES_KEY} must be a mapping of name: vendor:model")
        aliases[name] = target
        data[ALIASES_KEY] = aliases
        return _dump(data)

    try:
        atomic_update(path, _mutate)
    finally:
        reset_cache()
    return name, target


def unset_alias(name: str) -> None:
    """Remove ``agent_aliases.<name>``; :class:`ConfigError` if it is not set.

    Refused while ``leader_agent`` names the alias: dropping it would silently
    turn the configured leader agent into a warning + built-in default.
    """
    name = str(name).strip()
    path = config_path()

    def _mutate(old_text: str) -> str:
        data = _read_raw(path, old_text)
        aliases = data.get(ALIASES_KEY)
        if not isinstance(aliases, dict) or name not in aliases:
            raise ConfigError(f"agent alias {name!r} is not set")
        if str(data.get("leader_agent", "")).strip() == name:
            raise ConfigError(
                f"leader_agent uses agent alias {name!r}; change leader_agent first "
                f"(fleet config set leader_agent <vendor:model>)"
            )
        del aliases[name]
        if aliases:
            data[ALIASES_KEY] = aliases
        else:
            del data[ALIASES_KEY]
        return _dump(data)

    try:
        atomic_update(path, _mutate)
    finally:
        reset_cache()


def unset_value(key: str) -> None:
    """Remove a scalar ``key`` from the file (back to the built-in default).

    A key that is not in the file is a no-op; an unknown key is refused.
    """
    key = str(key).strip()
    if key not in ALL_KEYS:
        raise ConfigError(
            f"unknown config key: {key!r} (known keys: {', '.join(ALL_KEYS)})"
        )
    path = config_path()

    def _mutate(old_text: str) -> str:
        data = _read_raw(path, old_text)
        data.pop(key, None)
        return _dump(data)

    if not path.is_file():
        return
    try:
        atomic_update(path, _mutate)
    finally:
        reset_cache()


def _dump(data: dict) -> str:
    if not data:
        return ""
    return yaml.safe_dump(
        data, default_flow_style=False, sort_keys=False, allow_unicode=True
    )


def set_value(key: str, value: str) -> str:
    """Validate and persist ``key: value``; return the canonical value.

    Read-modify-write under the atomic-write lock; other keys in the file are
    kept. A file that is not a valid mapping is refused (never silently
    overwritten).
    """
    canonical = normalize(key, value)
    key = str(key).strip()
    path = config_path()

    def _mutate(old_text: str) -> str:
        data = _read_raw(path, old_text)
        data[key] = canonical
        return _dump(data)

    try:
        atomic_update(path, _mutate)
    finally:
        reset_cache()
    return canonical
