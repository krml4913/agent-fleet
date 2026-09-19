"""Shipped seeds for formations and roles: locate, hint at, and copy them.

Shipped files (``src/fleet/templates/*.yaml``, ``docs/prompts/roles/*.md``) are
*seed sources*, never a runtime resolution tier (Issues #225, #227): a name that
is missing from the project and global tiers is a hard error. This module only
helps the human recover — it names the shipped seed behind a missing name and the
exact next step — and provides the non-interactive copy behind
``fleet formation seed`` / ``fleet role seed``.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import state as state_mod

_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
SHIPPED_ROLES_DIR = _ROOT / "docs" / "prompts" / "roles"

KINDS = ("formation", "role")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class SeedError(ValueError):
    """Raised when a seed request cannot be honored."""


def _suffix(kind: str) -> str:
    return ".yaml" if kind == "formation" else ".md"


def seed_source_dir(kind: str) -> Path:
    return TEMPLATES_DIR if kind == "formation" else SHIPPED_ROLES_DIR


def seed_source(kind: str, name: str) -> Path | None:
    """Return the shipped seed file for ``name``, or ``None`` when none ships."""
    if kind not in KINDS or not NAME_RE.fullmatch(name):
        return None
    path = seed_source_dir(kind) / f"{name}{_suffix(kind)}"
    return path if path.is_file() else None


def target_path(kind: str, name: str, *, state_dir: Path | None) -> Path:
    """Runtime path for ``name`` in the project tier (``state_dir``) or global tier.

    ``state_dir=None`` selects the global tier.
    """
    subdir = state_mod.FORMATIONS_SUBDIR if kind == "formation" else state_mod.ROLES_SUBDIR
    base = state_mod.global_dir() / subdir if state_dir is None else Path(state_dir) / subdir
    return base / f"{name}{_suffix(kind)}"


def project_label(state_dir: Path) -> str:
    """Registry name to pass as ``--project`` (mirrors ``fleet-agent start``)."""
    try:
        return str(state_mod.load_project(state_dir).get("name") or state_dir.name)
    except Exception:
        return state_dir.name


def missing_hint(kind: str, name: str, state_dir: Path | str | None) -> str:
    """Multi-line hint appended to a "no <kind> named ..." error.

    Empty when ``name`` is not a shipped seed (nothing to suggest).
    """
    source = seed_source(kind, name)
    if source is None:
        return ""
    project_dir = Path(state_dir) if state_dir is not None else None
    lines = [
        f"{name!r} is a shipped seed ({source}) but has not been seeded into a "
        f"runtime tier. Seed it with one of:",
    ]
    if project_dir is not None:
        lines.append(
            f"  fleet {kind} seed {name} --project {project_label(project_dir)}"
            f"    -> {target_path(kind, name, state_dir=project_dir)}"
        )
    lines.append(
        f"  fleet {kind} seed {name} --global"
        f"    -> {target_path(kind, name, state_dir=None)}"
    )
    lines.append(
        "or run `fleet edit` and create it with mode 'seed shipped'."
    )
    return "\n".join(lines)


def with_hint(message: str, kind: str, name: str, state_dir: Path | str | None) -> str:
    hint = missing_hint(kind, name, state_dir)
    return f"{message}\n{hint}" if hint else message


def seed(kind: str, name: str, *, state_dir: Path | None, force: bool = False) -> Path:
    """Copy the shipped seed for ``name`` into the project (or global) tier.

    Refuses to overwrite an existing file unless ``force``. Returns the target.
    """
    if kind not in KINDS:
        raise SeedError(f"kind must be one of {', '.join(KINDS)}")
    if not NAME_RE.fullmatch(name):
        raise SeedError(f"invalid {kind} name {name!r}: must match [A-Za-z0-9_-]+")
    source = seed_source(kind, name)
    if source is None:
        shipped = sorted(p.stem for p in seed_source_dir(kind).glob(f"*{_suffix(kind)}"))
        available = ", ".join(shipped) if shipped else "(none)"
        raise SeedError(
            f"no shipped {kind} seed named {name!r}. Shipped seeds: {available}"
        )
    target = target_path(kind, name, state_dir=state_dir)
    if target.exists() and not force:
        raise SeedError(
            f"{target} already exists; pass --force to overwrite it"
        )
    text = source.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target
