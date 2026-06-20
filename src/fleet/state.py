"""State directory creation and read/write helpers.

File-based state is the source of truth. All writes go through
:func:`save_project` / :func:`save_task`, which use :mod:`fleet.locking` to
guarantee atomic, race-free updates and trigger a dashboard rebuild on
success (design doc §5.3-5.5).

This module is intentionally free of CLI concerns; subcommand modules
adapt it to argparse.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# PyYAML (vendored or system) for task.yaml and projects.yaml.
_VENDOR = Path(__file__).resolve().parent.parent.parent / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

import yaml as _yaml

from .events import utcnow_iso
from .locking import atomic_update, atomic_write
from . import simple_yaml  # kept for flat project.yaml


# Name of the central state directory inside the agent-fleet clone, and the
# global registry / per-project subdir within it (design doc §5, Issue #67).
STATE_HOME_NAME = "fleet-state"
REGISTRY_NAME = "projects.yaml"
PROJECTS_SUBDIR = "projects"
REGISTRY_VERSION = 1

# Reserved cross-project namespace under fleet-state/ (design doc §5.3, §6).
# ``global/`` holds concerns that span projects: the GLOBAL tier of two-tier
# leader memory (``global/leader-memory/``) and — reserved for later phases of
# Issue #166 — per-session leader state (``global/sessions/<label>/``).
GLOBAL_SUBDIR = "global"
LEADER_MEMORY_SUBDIR = "leader-memory"
SESSIONS_SUBDIR = "sessions"
SESSION_RECORD_NAME = "session.json"
SESSION_SCOPE_KEY = "scope"

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "prompts"

_MEMORY_INDEX_TEMPLATE = """\
# Memory Index

- *(no entries yet — add one when a task reveals something worth keeping)*
"""

_GLOBAL_MEMORY_INDEX_TEMPLATE = """\
# Leader Memory Index (global)

- *(no entries yet — add one when something worth keeping spans projects: how the \
leader relates to the user, or a router operating rule)*
"""


# ---------------------------------------------------------------------------
# fleet_home — central state root
# ---------------------------------------------------------------------------


def fleet_home() -> Path:
    """Return the central ``fleet-state/`` directory.

    Resolution order:
    1. ``$FLEET_HOME`` env var (override, useful in tests and alternative setups).
    2. ``<agent-fleet clone>/fleet-state/`` — derived from ``__file__``.
    """
    env = os.environ.get("FLEET_HOME")
    if env:
        return Path(env).expanduser().resolve()
    # src/fleet/state.py → parents[0]=fleet, [1]=src, [2]=clone root
    clone_root = Path(__file__).resolve().parents[2]
    return clone_root / STATE_HOME_NAME


def registry_path() -> Path:
    """Return the path to the global ``projects.yaml`` registry."""
    return fleet_home() / REGISTRY_NAME


# ---------------------------------------------------------------------------
# Global (cross-project) namespace — design doc §5.3, §6
# ---------------------------------------------------------------------------


def global_dir() -> Path:
    """Return ``fleet-state/global/`` — the reserved cross-project namespace.

    A sibling of ``projects/`` inside the same self-contained state tree, so it
    honors ``$FLEET_HOME`` / clone-root resolution exactly like the per-project
    paths (it derives from :func:`fleet_home`). It does not violate the
    "nothing under the home directory" non-goal: "global" here means
    cross-project-within-the-tree (design doc §5.3).
    """
    return fleet_home() / GLOBAL_SUBDIR


def global_leader_memory_dir() -> Path:
    """Return ``global/leader-memory/`` — the GLOBAL tier of leader memory.

    The user-global / router-rule layer of the two-tier leader memory, loaded at
    every session start (design doc §6). May not exist yet; scaffold it with
    :func:`ensure_global_leader_memory`.
    """
    return global_dir() / LEADER_MEMORY_SUBDIR


def global_sessions_dir() -> Path:
    """Return ``global/sessions/`` — the per-session leader state namespace.

    Keyed by ``owner_session`` (the session label, Issue #166): each live/known
    leader session owns one ``<label>/`` dir holding its record, notification
    queue, and lock (design §5.3, §10.3). A session spans projects, so this state
    belongs to the session — not to any one project's ``state_dir``.
    """
    return global_dir() / SESSIONS_SUBDIR


def session_dir(label: str) -> Path:
    """Return ``global/sessions/<label>/`` — one leader session's state dir.

    Holds ``session.json`` (the leader record), ``leader-pending.jsonl`` (the
    notification queue), and ``leader-notifier.lock`` for the session labelled
    *label* (design §5.3). May not exist until ``fleet leader`` creates it.
    """
    return global_sessions_dir() / label


def session_record_path(label: str) -> Path:
    """Return ``global/sessions/<label>/session.json`` — the per-session record.

    The relocated successor of the old per-project ``leader-session.json``
    (Issue #166): label / agent spec / started_at / tmux pane for one session.
    """
    return session_dir(label) / SESSION_RECORD_NAME


# ---------------------------------------------------------------------------
# Session scope helpers (Issue #172)
# ---------------------------------------------------------------------------


def read_session_record(label: str) -> dict | None:
    """Parse global/sessions/<label>/session.json. None on missing/invalid."""
    path = session_record_path(label)
    if not path.is_file():
        return None
    try:
        import json as _json
        data = _json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def session_scope(label: str) -> list[str] | None:
    """Return the session's scope list, or None when unscoped.

    None means "no scope declared" — all projects. A present-but-empty list
    is normalised to None (we never persist []), so callers test ``is None``.
    """
    record = read_session_record(label)
    if record is None:
        return None
    raw = record.get(SESSION_SCOPE_KEY)
    if not isinstance(raw, list) or not raw:
        return None
    return [str(s) for s in raw if s]


def in_scope(label: str, project_name: str) -> bool:
    """True if the session is unscoped, or project_name is in its scope."""
    scope = session_scope(label)
    if scope is None:
        return True
    return project_name in scope


def validate_registered_projects(names: list[str] | None) -> None:
    """Raise ``ValueError`` if any of *names* is not a registered project.

    Shared by ``fleet scope`` and ``fleet leader --scope`` so an invalid project
    name can be rejected *before* any session / tmux side effects are created.
    Empty / falsy input is a no-op.
    """
    if not names:
        return
    reg = load_registry()
    known = set(reg.get("projects", {}).keys())
    unknown = [n for n in names if n not in known]
    if unknown:
        listed = ", ".join(sorted(known)) if known else "(none)"
        raise ValueError(
            f"unknown project name(s): {', '.join(unknown)}\n"
            f"registered projects: {listed}"
        )


def set_session_scope(
    label: str,
    names: list[str] | None,
    *,
    mode: str = "set",
) -> list[str] | None:
    """Atomically upsert the scope field on session.json (flock + atomic RMW).

    Creates a minimal record {label, scope} when the dir/record is absent.
    Validates incoming names against the registry for set/add (raises ValueError
    on unknown names). mode="clear" deletes the key. Returns the new scope (or
    None when cleared/empty).
    """
    import json as _json

    rec_path = session_record_path(label)

    def _mutate(old_text: str) -> str:
        record: dict
        if old_text.strip():
            try:
                record = _json.loads(old_text)
                if not isinstance(record, dict):
                    record = {}
            except ValueError:
                record = {}
        else:
            record = {}

        record.setdefault("label", label)

        if mode == "clear":
            record.pop(SESSION_SCOPE_KEY, None)
            return _json.dumps(record, indent=2)

        current: list[str] = []
        raw = record.get(SESSION_SCOPE_KEY)
        if isinstance(raw, list):
            current = [str(s) for s in raw if s]

        if mode == "set":
            new_scope = list(dict.fromkeys(names or []))
        elif mode == "add":
            new_scope = list(dict.fromkeys(current + list(names or [])))
        elif mode == "rm":
            remove_set = set(names or [])
            new_scope = [n for n in current if n not in remove_set]
        else:
            raise ValueError(f"unknown mode: {mode!r}")

        if new_scope:
            record[SESSION_SCOPE_KEY] = sorted(set(new_scope))
        else:
            record.pop(SESSION_SCOPE_KEY, None)
        return _json.dumps(record, indent=2)

    if mode in ("set", "add"):
        validate_registered_projects(names)

    sdir = session_dir(label)
    sdir.mkdir(parents=True, exist_ok=True)
    atomic_update(rec_path, _mutate)
    return session_scope(label)


# ---------------------------------------------------------------------------
# Registry read/write
# ---------------------------------------------------------------------------


def _empty_registry() -> dict:
    return {"version": REGISTRY_VERSION, "projects": {}}


def load_registry() -> dict:
    """Load (or synthesise) the global registry.

    Returns ``{"version": 1, "projects": {...}}``; never raises on absent file.
    """
    path = registry_path()
    if not path.exists():
        return _empty_registry()
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return _empty_registry()
    data = _yaml.safe_load(text) or {}
    data.setdefault("version", REGISTRY_VERSION)
    data.setdefault("projects", {})
    return data


def _dump_registry(registry: dict) -> str:
    return _yaml.safe_dump(
        registry,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def register_project(name: str, repo: Path) -> None:
    """Register *name* → *repo* in the global registry (flock + atomic RMW).

    Raises ``ValueError`` on name collision or repo-path collision.
    """
    repo_resolved = Path(repo).resolve()

    def _mutate(old_text: str) -> str:
        reg = _yaml.safe_load(old_text) if old_text.strip() else None
        if reg is None:
            reg = _empty_registry()
        reg.setdefault("projects", {})

        if name in reg["projects"]:
            raise ValueError(
                f"project name {name!r} already registered "
                f"(repo: {reg['projects'][name].get('repo')}); "
                f"use --name <other> to pick a different name"
            )
        for existing_name, entry in reg["projects"].items():
            if Path(entry.get("repo", "")).resolve() == repo_resolved:
                raise ValueError(
                    f"repo path {repo_resolved} already registered as {existing_name!r}"
                )

        reg["projects"][name] = {
            "repo": str(repo_resolved),
            "created_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        }
        return _dump_registry(reg)

    atomic_update(registry_path(), _mutate)


def unregister_project(name: str) -> None:
    """Remove *name* from the global registry (flock + atomic RMW).

    Raises ``KeyError`` when *name* is not registered.
    """
    def _mutate(old_text: str) -> str:
        reg = _yaml.safe_load(old_text) if old_text.strip() else None
        if reg is None or name not in reg.get("projects", {}):
            raise KeyError(f"project {name!r} not found in registry")
        del reg["projects"][name]
        return _dump_registry(reg)

    atomic_update(registry_path(), _mutate)


# ---------------------------------------------------------------------------
# Project state-dir helpers
# ---------------------------------------------------------------------------


def project_state_dir(name: str) -> Path:
    """Return ``fleet_home()/projects/<name>`` (may not exist yet)."""
    return fleet_home() / PROJECTS_SUBDIR / name


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _path_contains(ancestor: Path, descendant: Path) -> bool:
    """Return True when *descendant* equals *ancestor* or is under it."""
    return descendant == ancestor or ancestor in descendant.parents


def resolve_state_dir(cwd: Path, *, project_name: str | None = None) -> Path | None:
    """Resolve which project's state dir applies to *cwd*.

    Priority:
    1. *project_name* explicit → registry lookup by name.
    2. *cwd* is inside the fleet-state tree
       (``fleet_home()/projects/<name>/…``) → the enclosing project.
    3. *cwd* is under a registered repo path → longest-path-match wins.

    Returns ``None`` when no project can be determined.
    """
    cwd_r = Path(cwd).resolve()

    # 1. Explicit name
    if project_name is not None:
        reg = load_registry()
        if project_name not in reg.get("projects", {}):
            return None
        return project_state_dir(project_name)

    # 2. cwd inside fleet-state/projects/<name>/
    projects_root = fleet_home() / PROJECTS_SUBDIR
    if _path_contains(projects_root, cwd_r):
        try:
            rel = cwd_r.relative_to(projects_root)
        except ValueError:
            pass
        else:
            if rel.parts:
                name = rel.parts[0]
                candidate = projects_root / name
                if candidate.is_dir():
                    return candidate

    # 3. Longest-path-match against registered repo paths
    reg = load_registry()
    best: Path | None = None
    best_len: int = -1
    for name, entry in reg.get("projects", {}).items():
        repo = Path(entry.get("repo", "")).resolve()
        if _path_contains(repo, cwd_r) and len(repo.parts) > best_len:
            best = project_state_dir(name)
            best_len = len(repo.parts)

    return best


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def init_state(state_dir: Path, *, name: str, repo: Path | None = None) -> None:
    """Create a fresh project state directory tree.

    Caller is expected to verify ``state_dir`` does not exist; this function
    will let ``FileExistsError`` propagate if it does.
    """
    state_dir.mkdir(parents=True)
    (state_dir / "tasks").mkdir()
    (state_dir / "events.jsonl").touch()

    _init_memory(state_dir)

    project_data: dict[str, str] = {
        "name": name,
        "created_at": utcnow_iso(),
        "version": "0.0.1",
    }
    if repo is not None:
        project_data["repo"] = str(Path(repo).resolve())

    save_project(state_dir, project_data)


def _init_memory(state_dir: Path) -> None:
    """Populate ``memory/`` with MEMORY.md and GUIDE.md."""
    memory_dir = state_dir / "memory"
    memory_dir.mkdir(exist_ok=True)

    memory_index = memory_dir / "MEMORY.md"
    if not memory_index.exists():
        memory_index.write_text(_MEMORY_INDEX_TEMPLATE, encoding="utf-8")

    guide_dest = memory_dir / "GUIDE.md"
    if not guide_dest.exists():
        guide_src = _PROMPTS_DIR / "fleet-memory-guide.md"
        if guide_src.exists():
            guide_dest.write_text(guide_src.read_text(encoding="utf-8"), encoding="utf-8")


def ensure_global_leader_memory() -> Path:
    """Idempotently scaffold ``global/leader-memory/`` with MEMORY.md + GUIDE.md.

    The GLOBAL tier of two-tier leader memory (design doc §6) — the same shape
    and discipline as the per-project store (:func:`_init_memory`), one tier up.
    It is **project-independent**: it lives under ``fleet-state/global/`` (the
    reserved cross-project namespace, design doc §5.3), not under any project's
    state, so it is created from a project-agnostic call site (``fleet init``).

    Idempotent: safe to call on every init. Existing ``MEMORY.md`` / ``GUIDE.md``
    content is never clobbered — only missing files are written, so an operator's
    edits and saved memories survive repeated calls. Mirrors ``_init_memory``'s
    plain-``write_text``-under-``not exists`` convention.

    Returns the ``leader-memory`` directory path.
    """
    memory_dir = global_leader_memory_dir()
    memory_dir.mkdir(parents=True, exist_ok=True)

    memory_index = memory_dir / "MEMORY.md"
    if not memory_index.exists():
        memory_index.write_text(_GLOBAL_MEMORY_INDEX_TEMPLATE, encoding="utf-8")

    guide_dest = memory_dir / "GUIDE.md"
    if not guide_dest.exists():
        guide_src = _PROMPTS_DIR / "leader-memory-guide.md"
        if guide_src.exists():
            guide_dest.write_text(guide_src.read_text(encoding="utf-8"), encoding="utf-8")

    return memory_dir


# ---------------------------------------------------------------------------
# Project (`project.yaml`)
# ---------------------------------------------------------------------------


def project_path(state_dir: Path) -> Path:
    return state_dir / "project.yaml"


def load_project(state_dir: Path) -> dict[str, str]:
    p = project_path(state_dir)
    if not p.exists():
        raise FileNotFoundError(f"project.yaml missing in {state_dir}")
    return simple_yaml.load(p.read_text(encoding="utf-8"))


def save_project(state_dir: Path, data: dict[str, str]) -> None:
    text = simple_yaml.dump(data)
    with atomic_write(project_path(state_dir)) as f:
        f.write(text)
    _maybe_rebuild_dashboard(state_dir)


# ---------------------------------------------------------------------------
# Tasks (`tasks/task-<id>/task.yaml`)
# ---------------------------------------------------------------------------


def task_owner_session(task: dict) -> str:
    """Return a task's owner-session label, defaulting to ``"main"`` when absent.

    The owner session is the leader session that spawned the task — stamped onto
    ``task.yaml`` by ``fleet-agent start`` from ``FLEET_SESSION`` (design §10.3).
    It keys both driver-window placement (``fleet-<label>``, §5.2) and leader
    notification routing (§10.3). A missing / empty value is treated as ``"main"``
    — the defensive default for tasks in flight at the Issue #166 cutover (``main``
    is the default session label), so routing never dead-ends.
    """
    label = task.get("owner_session")
    return label if isinstance(label, str) and label else "main"


def task_dir(state_dir: Path, task_id: str) -> Path:
    return state_dir / "tasks" / f"task-{task_id}"


def task_yaml_path(state_dir: Path, task_id: str) -> Path:
    return task_dir(state_dir, task_id) / "task.yaml"


def list_tasks(state_dir: Path) -> list[dict]:
    tasks_dir = state_dir / "tasks"
    if not tasks_dir.is_dir():
        return []
    out: list[dict] = []
    for child in sorted(tasks_dir.iterdir()):
        if not child.is_dir() or not child.name.startswith("task-"):
            continue
        task_yaml_file = child / "task.yaml"
        if not task_yaml_file.is_file():
            continue
        data = _yaml.safe_load(task_yaml_file.read_text(encoding="utf-8")) or {}
        data.setdefault("id", child.name[len("task-"):])
        out.append(data)
    return out


def load_task(state_dir: Path, task_id: str) -> dict:
    path = task_yaml_path(state_dir, task_id)
    if not path.exists():
        raise FileNotFoundError(f"task.yaml missing for task-{task_id}")
    data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("id", task_id)
    return data


def save_task(state_dir: Path, task_id: str, data: dict) -> None:
    """Persist a task; ensures the task directory exists and rebuilds dashboard."""
    data = dict(data)
    data.setdefault("id", task_id)
    tdir = task_dir(state_dir, task_id)
    tdir.mkdir(parents=True, exist_ok=True)
    text = _yaml.safe_dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    with atomic_write(task_yaml_path(state_dir, task_id)) as f:
        f.write(text)
    _maybe_rebuild_dashboard(state_dir)


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------


def derive_task_status(stages: list[dict]) -> str:
    """Compute task-level status from stage list."""
    if not stages:
        return "completed"
    statuses = [s.get("status", "pending") for s in stages]
    if all(s == "done" for s in statuses):
        return "completed"
    if any(s in ("running", "done") for s in statuses):
        return "running"
    return "spawning"


def get_current_stage_index(stages: list[dict]) -> int:
    """Return index of the first non-done stage, or last index if all done."""
    for i, stage in enumerate(stages):
        if stage.get("status") != "done":
            return i
    return max(len(stages) - 1, 0)


# ---------------------------------------------------------------------------
# Dashboard hook
# ---------------------------------------------------------------------------


def _maybe_rebuild_dashboard(state_dir: Path) -> None:
    from . import dashboard  # local import: cycle break

    dashboard.rebuild(state_dir)
