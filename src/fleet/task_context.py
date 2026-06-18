"""Resolve which task a driver-side CLI call is acting on.

Task-id resolution priority:
  1. Explicit ``--task-id`` argument (most explicit, wins)
  2. ``FLEET_TASK_ID`` environment variable (set by spawn into the pane)
  3. ``cwd`` inspection — walk parents looking for ``<state>/tasks/task-<id>/``

State-dir resolution:
  - ``--project <name>`` (``project_name``) wins outright: resolve the project
    by registry name, **ignoring** ``FLEET_STATE_DIR`` and cwd. This is the
    leader path — a project-agnostic leader pane names its project explicitly
    (design §5.3 priority 1; Issue #166 Phase 6).
  - Otherwise ``FLEET_STATE_DIR`` (always set in spawned driver panes) wins
    over cwd-based discovery, so driver commands work from any working
    directory — **except** when it points at a project-agnostic leader session
    dir (``global/sessions/<label>/``), which carries no ``tasks/``: that is the
    signal of a leader pane, so we refuse rather than dead-end and tell the
    caller to pass ``--project``.
  - Failing both, fall back to registry resolution from cwd (ad-hoc human use).

If none of those produce an id, :class:`TaskNotFound` is raised.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import state as state_mod


class TaskNotFound(RuntimeError):
    """Raised when a task id can't be determined from any source."""


class ProjectNotFound(RuntimeError):
    """Raised when a project state dir can't be resolved (no task id needed)."""


class _Unresolved(Exception):
    """Internal: raised by _resolve_state_dir_core when state-dir resolution fails."""


def resolve(
    *,
    explicit_id: str | None = None,
    cwd: Path | None = None,
    project_name: str | None = None,
) -> tuple[Path, str]:
    """Return ``(state_dir, task_id)`` for the current invocation.

    *project_name* (from ``--project``) selects the project by registry name and
    overrides every cwd / ``FLEET_STATE_DIR`` heuristic — the leader path. When
    absent, the driver / ad-hoc fallbacks apply (``FLEET_STATE_DIR`` then cwd).
    """
    here = Path(cwd) if cwd is not None else Path.cwd()
    state_dir = _resolve_state_dir(here, project_name)

    task_id = explicit_id or os.environ.get("FLEET_TASK_ID")
    if task_id is None:
        task_id = _from_cwd(here, state_dir)
    if task_id is None:
        raise TaskNotFound(
            "could not determine task id "
            "(pass --task-id, set FLEET_TASK_ID, or run inside a task dir)"
        )
    return state_dir, task_id


def _resolve_state_dir_core(here: Path, project_name: str | None) -> Path:
    """Shared 3-step state-dir resolution used by task- and project-centric commands.

    Raises :class:`_Unresolved` when nothing resolves. The message is reused
    verbatim by both :func:`_resolve_state_dir` (raises :class:`TaskNotFound`)
    and :func:`resolve_project_state_dir` (raises :class:`ProjectNotFound`).
    """
    # 1. Explicit --project: registry by name, ignoring FLEET_STATE_DIR / cwd
    #    (the leader path — design §5.3 priority 1; Issue #166 Phase 6).
    if project_name is not None:
        state_dir = state_mod.resolve_state_dir(here, project_name=project_name)
        if state_dir is None:
            raise _Unresolved(
                f"no registered project named {project_name!r} "
                f"(see `fleet status --all`)"
            )
        return state_dir

    # 2. FLEET_STATE_DIR (the driver pane's project state dir).
    env_state_dir = os.environ.get("FLEET_STATE_DIR")
    if env_state_dir:
        candidate = Path(env_state_dir)
        if _is_session_dir(candidate):
            # A project-agnostic leader pane points FLEET_STATE_DIR at its
            # session dir, which has no tasks/. The cwd fallbacks must not apply
            # for a leader — name the project explicitly instead.
            raise _Unresolved(
                f"FLEET_STATE_DIR is the leader session dir {candidate.name!r}, "
                f"which owns no tasks; pass --project <name> to name the target "
                f"project"
            )
        if candidate.is_dir():
            return candidate

    # 3. Ad-hoc human use: resolve from cwd via the registry.
    state_dir = state_mod.resolve_state_dir(here)
    if state_dir is None:
        raise _Unresolved(
            f"no registered project found for {here.resolve()} "
            f"(pass --project <name>)"
        )
    return state_dir


def _resolve_state_dir(here: Path, project_name: str | None) -> Path:
    """Resolve the project state dir for a task-centric command.

    Raises :class:`TaskNotFound` with a message that points at ``--project``
    when nothing resolves.
    """
    try:
        return _resolve_state_dir_core(here, project_name)
    except _Unresolved as exc:
        raise TaskNotFound(str(exc)) from exc


def resolve_project_state_dir(
    *,
    project_name: str | None = None,
    cwd: Path | None = None,
) -> Path:
    """Resolve a project's state dir for a *project-centric* command.

    Session-aware, mirroring :func:`resolve`'s state-dir logic without
    requiring a task id:

    1. ``project_name`` (from ``--project``) → registry by name, ignoring
       ``FLEET_STATE_DIR`` / cwd. Unknown name → :class:`ProjectNotFound`.
    2. ``FLEET_STATE_DIR`` — if it is a leader **session dir**, refuse and
       point at ``--project``; else, if a real dir, use it.
    3. cwd via the registry (ad-hoc human use), else :class:`ProjectNotFound`
       pointing at ``--project``.

    Never returns ``None`` and never silently resolves to the ``fleet``
    project from a leader pane.
    """
    here = Path(cwd) if cwd is not None else Path.cwd()
    try:
        return _resolve_state_dir_core(here, project_name)
    except _Unresolved as exc:
        raise ProjectNotFound(str(exc)) from exc


def _is_session_dir(path: Path) -> bool:
    """Return True when *path* is a ``global/sessions/<label>/`` session dir.

    Detected structurally (parent is ``global/sessions/``) so it holds even
    before ``session.json`` is written. This is how a project-agnostic leader
    pane is told apart from a driver pane: the leader's ``FLEET_STATE_DIR`` is
    its session dir, not a project state dir.
    """
    try:
        return path.resolve().parent == state_mod.global_sessions_dir().resolve()
    except (OSError, ValueError):
        return False


def _from_cwd(cwd: Path, state_dir: Path) -> str | None:
    cwd_r = cwd.resolve()
    tasks_dir = (state_dir / "tasks").resolve()
    try:
        rel = cwd_r.relative_to(tasks_dir)
    except ValueError:
        return None
    if not rel.parts:
        return None
    head = rel.parts[0]
    if head.startswith("task-"):
        return head[len("task-"):]
    return None
