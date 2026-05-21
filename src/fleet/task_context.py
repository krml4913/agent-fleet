"""Resolve which task a driver-side CLI call is acting on.

Resolution priority:
  1. Explicit ``--task-id`` argument (most explicit, wins)
  2. ``FLEET_TASK_ID`` environment variable (set by spawn into the pane)
  3. ``cwd`` inspection — walk parents looking for ``<state>/tasks/task-<id>/``

State-dir resolution:
  - ``FLEET_STATE_DIR`` env var (always set in spawned driver panes) wins
    over cwd-based discovery, so commands work from any working directory.

If none of those produce an id, :class:`TaskNotFound` is raised.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import state as state_mod


class TaskNotFound(RuntimeError):
    """Raised when a task id can't be determined from any source."""


def resolve(
    *,
    explicit_id: str | None = None,
    cwd: Path | None = None,
) -> tuple[Path, str]:
    """Return ``(state_dir, task_id)`` for the current invocation."""
    here = Path(cwd) if cwd is not None else Path.cwd()

    # FLEET_STATE_DIR wins (always set in spawned driver panes).
    state_dir: Path | None = None
    env_state_dir = os.environ.get("FLEET_STATE_DIR")
    if env_state_dir:
        candidate = Path(env_state_dir)
        state_dir = candidate if candidate.is_dir() else None

    # Fall back to registry-based resolution from cwd.
    if state_dir is None:
        state_dir = state_mod.resolve_state_dir(here)

    if state_dir is None:
        raise TaskNotFound(
            f"no registered project found for {here.resolve()}"
        )

    task_id = explicit_id or os.environ.get("FLEET_TASK_ID")
    if task_id is None:
        task_id = _from_cwd(here, state_dir)
    if task_id is None:
        raise TaskNotFound(
            "could not determine task id "
            "(pass --task-id, set FLEET_TASK_ID, or run inside a task dir)"
        )
    return state_dir, task_id


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
