"""Build the initial prompt that a freshly-spawned driver reads.

Kept intentionally small. Design doc §10.2 calls out claude-forge's bloated
1000-line driver-prompts as the root cause of boot timeouts; this module
must resist accumulating optional context. New context belongs in a
plugin hook, not here.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from . import state as state_mod
from .paths import fleet_agent_bin  # re-exported: shared by leader_prompt

_CLONE_ROOT = Path(__file__).resolve().parent.parent.parent
_PROMPTS_DIR = _CLONE_ROOT / "docs" / "prompts"
_TEMPLATE_PATH = _PROMPTS_DIR / "driver-base.md"

__all__ = [
    "RoleResolutionError",
    "fleet_agent_bin",
    "render",
    "validate_formation_roles",
]


class RoleResolutionError(ValueError):
    """Raised when a role prompt fragment cannot be resolved."""


def _load_base() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _role_paths(role: str, state_dir: Path | str | None) -> list[Path]:
    paths: list[Path] = []
    if state_dir is not None:
        paths.append(Path(state_dir) / state_mod.ROLES_SUBDIR / f"{role}.md")
    paths.append(state_mod.global_roles_dir() / f"{role}.md")
    return paths


def _load_role_fragment(role: str, state_dir: Path | str | None) -> str:
    if "/" in role or "\\" in role:
        raise RoleResolutionError(
            f"malformed role name {role!r}: role names must not contain '/' or '\\'"
        )
    looked = _role_paths(role, state_dir)
    for path in looked:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    tiers = "\n".join(f"  - {path}" for path in looked)
    raise RoleResolutionError(f"no role named {role!r}. Looked in:\n{tiers}")


def _iter_formation_roles(formation_data: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    for stage in formation_data.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        role = stage.get("role")
        if role:
            roles.append(str(role))
        peer_review = stage.get("peer_review")
        if isinstance(peer_review, dict) and peer_review.get("role"):
            roles.append(str(peer_review["role"]))
    return roles


def validate_formation_roles(
    formation_data: dict[str, Any],
    state_dir: Path | str | None,
) -> None:
    """Validate that every role referenced by a formation resolves now.

    This is called by ``fleet-agent start`` after formation schema validation
    and before task creation, so an unseeded or misspelled later-stage role does
    not fail inside ``fleet-agent done`` after state has already advanced.
    """
    seen: set[str] = set()
    for role in _iter_formation_roles(formation_data):
        if role in seen:
            continue
        seen.add(role)
        _load_role_fragment(role, state_dir)


def _memory_index_section(state_dir: Path | str | None) -> str:
    """Return the ``MEMORY.md`` index wrapped as a prompt section, or ``""``.

    Loads only the index (``<state>/memory/MEMORY.md``) — not every memory body —
    so any vendor driver sees the accumulated project knowledge at task start.
    Returns an empty string when no state dir is given or the index is absent,
    keeping the prompt lightweight (design direction, Issue #114).
    """
    if state_dir is None:
        return ""
    index_path = Path(state_dir) / "memory" / "MEMORY.md"
    if not index_path.is_file():
        return ""
    content = index_path.read_text(encoding="utf-8").strip()
    if not content:
        return ""
    return "## Project memory (index)\n\n" + content


def render(
    *,
    task_id: str,
    description: str,
    formation_name: str,
    role: str,
    agent: str,
    fleet_bin: str | None = None,
    state_dir: Path | str | None = None,
) -> str:
    """Return the prompt string to send to the driver.

    ``fleet-agent`` references in the fleet-managed prompt text (base + role
    fragment) are rewritten to an absolute path so the driver can run lifecycle
    commands regardless of whether ``fleet-agent`` is on ``PATH`` — see
    :func:`fleet_agent_bin` for why a bare ``fleet-agent`` is unreliable. The
    user-supplied ``description`` is left untouched.

    When ``state_dir`` is given and ``<state>/memory/MEMORY.md`` exists, its
    index is injected so any vendor driver starts with the shared project
    knowledge (Issue #114). The injected index is project content, so it is not
    subject to the ``fleet-agent`` path rewrite.
    """
    bin_path = fleet_bin if fleet_bin is not None else fleet_agent_bin()
    base = _load_base()
    parts = [base.rstrip()]
    role_fragment = _load_role_fragment(role, state_dir).strip()
    parts.append(role_fragment)
    body = "\n\n".join(parts)
    body = body.replace("fleet-agent", shlex.quote(bin_path))
    memory_section = _memory_index_section(state_dir)
    if memory_section:
        body = body + "\n\n" + memory_section
    return (
        body
        + "\n---\n"
        + f"task id:   task-{task_id}\n"
        + f"formation:  {formation_name}\n"
        + f"role:      {role}\n"
        + f"agent:     {agent}\n"
        + "---\n\n"
        + "Task description:\n\n"
        + description.rstrip()
        + "\n"
    )
