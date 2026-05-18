"""Load and inspect team topology YAML files (design doc §6).

A *topology* describes which agent(s) handle a task and how they
hand off — solo, pair-review, multi-stage, race, or a project-defined
custom shape. Presets ship in ``src/fleet/presets/``; project-specific
custom ones live in ``<state>/topologies/<name>.yaml``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PRESETS_DIR = Path(__file__).resolve().parent / "presets"
CUSTOM_SUBDIR = "topologies"


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_presets() -> list[str]:
    if not PRESETS_DIR.is_dir():
        return []
    return sorted(p.stem for p in PRESETS_DIR.glob("*.yaml"))


def list_custom(state_dir: Path) -> list[str]:
    d = state_dir / CUSTOM_SUBDIR
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_preset(name: str) -> dict[str, Any]:
    path = PRESETS_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"no preset topology: {name}")
    return _load_yaml(path)


def load_custom(state_dir: Path, name: str) -> dict[str, Any]:
    path = state_dir / CUSTOM_SUBDIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"no custom topology: {name}")
    return _load_yaml(path)


def load(name: str, state_dir: Path | None = None) -> dict[str, Any]:
    """Resolve a topology by name. Custom (under ``state_dir``) wins over preset."""
    if state_dir is not None:
        custom_path = state_dir / CUSTOM_SUBDIR / f"{name}.yaml"
        if custom_path.is_file():
            return _load_yaml(custom_path)
    return load_preset(name)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(data: dict[str, Any]) -> None:
    """Run a minimal sanity check on a topology document.

    Requires:
      * ``name`` field
      * at least one of ``roles`` / ``stages`` / ``candidates``

    More elaborate schema enforcement is deferred until topology consumers
    (spawn / orchestrator) need richer guarantees.
    """
    if not isinstance(data, dict):
        raise ValueError("topology must be a YAML mapping at the top level")
    if "name" not in data or not data["name"]:
        raise ValueError("topology missing required field: name")
    shapes = ("roles", "stages", "candidates")
    present = [s for s in shapes if s in data]
    if not present:
        raise ValueError(
            f"topology must define one of {shapes}; got keys={sorted(data)}"
        )


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"topology file must be a YAML mapping: {path}")
    return data
