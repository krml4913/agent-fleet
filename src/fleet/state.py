"""State directory creation and read/write helpers.

File-based state is the source of truth. All writes go through
:func:`save_project` / :func:`save_task`, which use :mod:`fleet.locking` to
guarantee atomic, race-free updates and trigger a dashboard rebuild on
success (design doc §5.3-5.5).

This module is intentionally free of CLI concerns; subcommand modules
adapt it to argparse.
"""
from __future__ import annotations

import sys
from pathlib import Path

# PyYAML (vendored or system) for task.yaml — supports nested stages structure.
_VENDOR = Path(__file__).resolve().parent.parent.parent / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

import yaml as _yaml

from .events import utcnow_iso
from .locking import atomic_write
from . import simple_yaml  # kept for flat project.yaml


STATE_DIR_NAME = ".fleet-state"


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def init_state(state_dir: Path, *, name: str) -> None:
    """Create a fresh ``.fleet-state/`` directory tree.

    Caller is expected to verify ``state_dir`` does not exist; this function
    will let ``FileExistsError`` propagate if it does.
    """
    state_dir.mkdir(parents=True)
    (state_dir / "tasks").mkdir()
    (state_dir / "events.jsonl").touch()

    save_project(
        state_dir,
        {
            "name": name,
            "created_at": utcnow_iso(),
            "version": "0.0.1",
        },
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_state_dir(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a ``.fleet-state/`` directory.

    Returns the located state dir or ``None`` if none exists in any ancestor.
    """
    cur = Path(start).resolve()
    while True:
        candidate = cur / STATE_DIR_NAME
        if candidate.is_dir():
            return candidate
        if cur.parent == cur:
            return None
        cur = cur.parent


# ---------------------------------------------------------------------------
# Project (`.fleet-state/project.yaml`)
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
# Tasks (`.fleet-state/tasks/task-<id>/task.yaml`)
# ---------------------------------------------------------------------------


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
    text = _yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    with atomic_write(task_yaml_path(state_dir, task_id)) as f:
        f.write(text)
    _maybe_rebuild_dashboard(state_dir)


# ---------------------------------------------------------------------------
# Stage helpers (new schema: task has stages list as SOT)
# ---------------------------------------------------------------------------


def derive_task_status(stages: list[dict]) -> str:
    """Compute task-level status from stage list.

    SOT is the stages list; ``task.status`` is a cached projection of it.
    Stages with any progress (running or done) mean the task is "running";
    "spawning" only when nothing has started yet (all pending).
    """
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
    """Rebuild ``dashboard.md`` after a state write.

    Imported lazily to break the dashboard ↔ state import cycle (dashboard
    needs to call back into ``list_tasks`` / ``load_project``).
    """
    from . import dashboard  # local import: cycle break

    dashboard.rebuild(state_dir)
