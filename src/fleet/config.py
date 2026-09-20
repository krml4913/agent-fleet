"""Global (cross-project) config: ``fleet-state/global/config.yaml``.

Project settings live in ``projects/<name>/project.yaml``; this file holds the
few settings that are not per-project. The only key today is ``mux`` (the
multiplexer backend, ``tmux`` | ``zellij``)::

    mux: tmux

Reads are tolerant, like :func:`fleet.notify.load_config`: a missing file means
"defaults", and an unreadable / malformed file or an invalid value only prints a
warning to stderr (once per process) and is ignored — a bad config never crashes
a command. Writes (:func:`set_value`, behind ``fleet config set``) go through
the locked atomic-write helpers and validate the key and value.

Selection precedence for a key is env > this file > the built-in default; the
env layer belongs to the consumer (see :func:`fleet.mux.backend_selection`).
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import state as state_mod
from .locking import atomic_update
from .mux import BACKENDS, DEFAULT_BACKEND

import yaml  # noqa: E402  (after ``state`` has put the vendored PyYAML on sys.path)

CONFIG_FILE = "config.yaml"

#: Known keys → the values each accepts.
KEYS: dict[str, tuple[str, ...]] = {"mux": BACKENDS}

#: Built-in defaults (used when the key is absent from the file).
DEFAULTS: dict[str, str] = {"mux": DEFAULT_BACKEND}

_cache: tuple[Path, dict[str, str]] | None = None


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
    if key not in KEYS:
        raise ConfigError(
            f"unknown config key: {key!r} (known keys: {', '.join(KEYS)})"
        )
    canonical = str(value).strip().lower()
    if canonical not in KEYS[key]:
        raise ConfigError(
            f"invalid value for {key}: {value!r} (expected one of: {', '.join(KEYS[key])})"
        )
    return canonical


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
    """The valid, explicitly configured values (``{}`` when the file is absent).

    Cached per process (keyed by path, so a different ``$FLEET_HOME`` reloads);
    :func:`reset_cache` and :func:`set_value` drop the cache. Never raises: an
    unreadable file, malformed YAML or an invalid value warns and is ignored.
    """
    global _cache
    path = config_path()
    if _cache is not None and _cache[0] == path:
        return _cache[1]
    values: dict[str, str] = {}
    if path.is_file():
        try:
            raw = _read_raw(path, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ConfigError) as e:
            print(f"warn: global config unreadable, using defaults: {e}", file=sys.stderr)
            raw = {}
        for key, value in raw.items():
            if key not in KEYS:
                continue  # unknown keys are ignored (forward compatible)
            try:
                values[key] = normalize(key, value)
            except ConfigError as e:
                print(f"warn: {path}: {e}; ignoring", file=sys.stderr)
    _cache = (path, values)
    return values


def reset_cache() -> None:
    """Forget the cached config so the next :func:`load` re-reads the file."""
    global _cache
    _cache = None


def get(key: str) -> tuple[str, str]:
    """``(value, source)`` for a known ``key``; source is ``config`` or ``default``."""
    if key not in KEYS:
        raise ConfigError(
            f"unknown config key: {key!r} (known keys: {', '.join(KEYS)})"
        )
    configured = load().get(key)
    if configured is not None:
        return configured, "config"
    return DEFAULTS[key], "default"


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
        return yaml.safe_dump(
            data, default_flow_style=False, sort_keys=False, allow_unicode=True
        )

    try:
        atomic_update(path, _mutate)
    finally:
        reset_cache()
    return canonical
