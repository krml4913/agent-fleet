"""Load and inspect team formation YAML files (design doc §6).

A *formation template* (``src/fleet/templates/*.yaml``) is a fleet-shipped
starter; it can be copied into a project via ``fleet init`` or
``fleet formation init --from``.  Direct execution is not supported.

A *formation* lives in ``<state>/formations/<name>.yaml`` — that is the
runtime source of truth.  Templates and formations are distinct: templates
are read-only starters; once copied, a formation is fully independent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_VENDOR = Path(__file__).resolve().parent.parent.parent / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

import yaml

from . import state as state_mod

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
CUSTOM_SUBDIR = "formations"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class ResolutionError(ValueError):
    """Raised when a formation cannot be resolved for start."""


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_templates() -> list[str]:
    if not TEMPLATES_DIR.is_dir():
        return []
    return sorted(p.stem for p in TEMPLATES_DIR.glob("*.yaml"))


def list_custom(state_dir: Path) -> list[str]:
    d = state_dir / CUSTOM_SUBDIR
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_template(name: str) -> dict[str, Any]:
    path = TEMPLATES_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"no formation template: {name}")
    return _load_yaml(path)


def load_custom(state_dir: Path, name: str) -> dict[str, Any]:
    path = state_dir / CUSTOM_SUBDIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"no custom formation: {name}")
    return _load_yaml(path)


def load_formation(name: str, state_dir: Path) -> dict[str, Any]:
    """Load a formation strictly from ``<state>/formations/<name>.yaml``.

    No template fallback — templates are not directly executable.
    Raises FileNotFoundError when the formation is missing.
    """
    path = state_dir / CUSTOM_SUBDIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(
            f"no formation named {name!r} in {state_dir / CUSTOM_SUBDIR}.\n"
            f"  Run: fleet formation init --from <template>"
        )
    return _load_yaml(path)


# ---------------------------------------------------------------------------
# Formation resolution for ``fleet-agent start``
# ---------------------------------------------------------------------------


def resolve_formation(
    state_dir: Path,
    requested: str | None,
    *,
    owner_session: str = "main",
) -> tuple[str, dict[str, Any]]:
    """Return ``(effective_name, formation_dict)`` for start.

    Rules:
    - ``requested`` given: load strictly from formations/; missing → error.
    - ``requested`` is None + 0 customs: synthesise leader-solo from the owner
      session's agent (``global/sessions/<owner_session>/session.json``).
    - ``requested`` is None + 1 custom: use that one (unambiguous).
    - ``requested`` is None + 2+ customs: ambiguous → error.
    """
    if requested is not None:
        try:
            data = load_formation(requested, state_dir)
        except FileNotFoundError as e:
            raise ResolutionError(str(e)) from e
        return (requested, data)

    customs = list_custom(state_dir)
    if len(customs) == 0:
        return synth_leader_solo(owner_session)
    if len(customs) == 1:
        return (customs[0], load_formation(customs[0], state_dir))
    raise ResolutionError(
        f"multiple formations in {state_dir / CUSTOM_SUBDIR}: "
        f"{', '.join(customs)}. Pass --formation <name> to choose."
    )


def synth_leader_solo(owner_session: str) -> tuple[str, dict[str, Any]]:
    """Synthesise a single-stage solo formation from the owner session's agent.

    Reads the spawning session's record (Issue #166: the per-session leader
    record relocated from per-project ``leader-session.json`` to
    ``global/sessions/<label>/session.json``).
    """
    session = read_leader_session(owner_session)
    if session is None:
        raise ResolutionError(
            f"no formations defined and no leader session record for "
            f"{owner_session!r}. Either run `fleet leader` first, or pass "
            f"--formation <name>."
        )
    agent = session.get("agent")
    if not agent or not _agent_spec_valid(agent):
        raise ResolutionError(
            f"invalid agent in session record for {owner_session!r}: {agent!r}"
        )
    import sys as _sys
    print(f"warn: no formation specified, falling back to leader agent ({agent})", file=_sys.stderr)
    data: dict[str, Any] = {
        "name": "_leader_solo",
        "description": "Synthesized 1-stage solo (no formations defined; using leader agent).",
        "stages": [{"role": "driver", "agent": agent}],
    }
    return ("_leader_solo", data)


def read_leader_session(label: str) -> dict[str, Any] | None:
    """Read ``global/sessions/<label>/session.json``.  Returns None on any error.

    The owner session's leader record (label / agent spec / started_at / pane),
    written by ``fleet leader`` (Issue #166). Used to pick the vendor ``ready``
    regex for notifier routing (§10.3) and the fallback agent for ``_leader_solo``.
    """
    path = state_mod.session_record_path(label)
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        agent = data.get("agent")
        if not agent:
            return None
        return data
    except Exception:
        return None


def _agent_spec_valid(spec: str) -> bool:
    try:
        from .agents import parse_spec
        parse_spec(spec)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def expand_stages(formation_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a formation definition into a stage list for task.yaml.

    Each stage receives ``status: pending``. The ``user_approval`` shorthand
    (``"required"``/``"optional"`` string) is normalised into object form:
    ``{required: bool, status: pending}``.
    """
    raw: list[dict[str, Any]] = []
    if formation_data.get("stages"):
        raw = [dict(s) for s in formation_data["stages"]]

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
    """Sanity-check a formation document (design doc §6).

    Required fields:
      * ``name``
      * ``stages`` — non-empty list; each element must be a mapping with ``role``
    """
    if not isinstance(data, dict):
        raise ValueError("formation must be a YAML mapping at the top level")
    if "name" not in data or not data["name"]:
        raise ValueError("formation missing required field: name")
    if "stages" not in data:
        raise ValueError(
            f"formation must define 'stages'; got keys={sorted(data)}"
        )
    stages = data["stages"]
    if not isinstance(stages, list) or len(stages) == 0:
        raise ValueError("formation 'stages' must be a non-empty list")
    for i, stage in enumerate(stages):
        if not isinstance(stage, dict):
            raise ValueError(f"formation stages[{i}] must be a mapping, got {type(stage).__name__}")
        if "role" not in stage:
            raise ValueError(f"formation stages[{i}] missing required field: role")


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"formation file must be a YAML mapping: {path}")
    return data
