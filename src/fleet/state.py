"""State directory creation and read/write helpers.

File-based state is the source of truth. All writes go through
:func:`save_project` / :func:`save_task`, which use :mod:`fleet.locking` to
guarantee atomic, race-free updates and trigger a dashboard rebuild on
success (design doc §5.3-5.5).

This module is intentionally free of CLI concerns; subcommand modules
adapt it to argparse.
"""
from __future__ import annotations

from pathlib import Path

from .events import utcnow_iso
from .locking import atomic_write
from . import simple_yaml


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


def list_tasks(state_dir: Path) -> list[dict[str, str]]:
    tasks_dir = state_dir / "tasks"
    if not tasks_dir.is_dir():
        return []
    out: list[dict[str, str]] = []
    for child in sorted(tasks_dir.iterdir()):
        if not child.is_dir() or not child.name.startswith("task-"):
            continue
        yaml = child / "task.yaml"
        if not yaml.is_file():
            continue
        data = simple_yaml.load(yaml.read_text(encoding="utf-8"))
        data.setdefault("id", child.name[len("task-") :])
        out.append(data)
    return out


def load_task(state_dir: Path, task_id: str) -> dict[str, str]:
    path = task_yaml_path(state_dir, task_id)
    if not path.exists():
        raise FileNotFoundError(f"task.yaml missing for task-{task_id}")
    data = simple_yaml.load(path.read_text(encoding="utf-8"))
    data.setdefault("id", task_id)
    return data


def save_task(state_dir: Path, task_id: str, data: dict[str, str]) -> None:
    """Persist a task; ensures the task directory exists and rebuilds dashboard."""
    data = dict(data)
    data.setdefault("id", task_id)
    tdir = task_dir(state_dir, task_id)
    tdir.mkdir(parents=True, exist_ok=True)
    text = simple_yaml.dump(data)
    with atomic_write(task_yaml_path(state_dir, task_id)) as f:
        f.write(text)
    _maybe_rebuild_dashboard(state_dir)


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
