"""Load and inspect team topology YAML files (design doc §6).

A *topology* describes which agent(s) handle a task and how they
hand off — solo, pair-review, multi-stage, or a project-defined
custom shape. Presets ship in ``src/fleet/presets/``; project-specific
custom ones live in ``<state>/topologies/<name>.yaml``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_VENDOR = Path(__file__).resolve().parent.parent.parent / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

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


def expand_stages(topo: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a topology definition into a stage list for task.yaml.

    Each stage receives ``status: pending``. The ``user_approval`` shorthand
    (``"required"``/``"optional"`` string) is normalised into object form:
    ``{required: bool, status: pending}``.
    """
    raw: list[dict[str, Any]] = []
    if topo.get("stages"):
        raw = [dict(s) for s in topo["stages"]]

    result: list[dict[str, Any]] = []
    for stage in raw:
        entry = dict(stage)
        entry["status"] = "pending"
        ua = entry.get("user_approval")
        if isinstance(ua, str):
            entry["user_approval"] = {
                "required": ua == "required",
                "status": "pending",
            }
        result.append(entry)
    return result


def validate(data: dict[str, Any]) -> None:
    """Sanity-check a topology document (design doc §6).

    Required fields:
      * ``name``
      * ``stages`` — non-empty list; each element must be a mapping with ``role``
    """
    if not isinstance(data, dict):
        raise ValueError("topology must be a YAML mapping at the top level")
    if "name" not in data or not data["name"]:
        raise ValueError("topology missing required field: name")
    if "stages" not in data:
        raise ValueError(
            f"topology must define 'stages'; got keys={sorted(data)}"
        )
    stages = data["stages"]
    if not isinstance(stages, list) or len(stages) == 0:
        raise ValueError("topology 'stages' must be a non-empty list")
    for i, stage in enumerate(stages):
        if not isinstance(stage, dict):
            raise ValueError(f"topology stages[{i}] must be a mapping, got {type(stage).__name__}")
        if "role" not in stage:
            raise ValueError(f"topology stages[{i}] missing required field: role")


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
