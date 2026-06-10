"""``fleet-agent cleanup [task-id]`` — physical teardown of a finished task.

Conceptually distinct from ``fleet-agent done``:
  * ``done``    flips the status to ``completed`` and emits an event.
  * ``cleanup`` is the destructive step: kill the tmux window, drop the
    driver-prompt buffer, run the workspace ``on_cleanup`` hook
    (workspace=worktree removes the worktree + branch), and optionally
    archive the task dir.

Refuses to run unless the task is in a terminal state (``completed`` /
``failed``); pass ``--force`` to override.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .. import dashboard as dashboard_mod
from .. import workspace as workspace_mod
from .. import state as state_mod
from .. import task_context
from .. import tmux as tmux_mod
from ..events import append_event


TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def teardown(
    state_dir: Path,
    task_id: str,
    task: dict,
    *,
    archive: bool,
    project_root: Path | None = None,
) -> bool:
    """Physical teardown shared by ``cleanup`` and ``merge``.

    Runs the workspace ``on_cleanup`` hook (worktree remove + local
    ``branch -D`` when workspace=worktree), kills the task's tmux window,
    drops its prompt buffer, and—when ``archive`` is set—moves the task dir
    under ``tasks/_archive/``. Returns whether the dir was archived.

    Does NOT emit an event or rebuild the dashboard; each caller does that
    with its own event type.
    """
    project = state_mod.load_project(state_dir)

    # workspace cleanup hook — the worktree lives in the *project repo*
    # (project.yaml `repo`), which is NOT state_dir.parent for projects whose
    # state dir lives outside their repo. An explicit project_root arg wins,
    # then the project repo, then state_dir.parent as a last resort.
    repo = project.get("repo")
    resolved_root = project_root or (Path(repo) if repo else state_dir.parent)
    ctx: dict = {
        "state_dir": state_dir,
        "task_id": task_id,
        "task": task,
        "project_root": resolved_root,
    }
    try:
        workspace_mod.on_cleanup(ctx)
    except Exception as e:  # noqa: BLE001 — workspace errors warn, don't block
        print(f"warn: workspace on_cleanup failed: {e}", file=sys.stderr)

    # Drop tmux artefacts.
    project_name = project.get("name") or "fleet"
    session = f"fleet-{project_name}"
    buffer_name = f"fleet-task-{task_id}"
    if tmux_mod.available():
        if tmux_mod.session_exists(session):
            try:
                tmux_mod.kill_task_windows(session, task_id)
            except tmux_mod.TmuxError as e:
                print(f"warn: kill_window failed: {e}", file=sys.stderr)
        tmux_mod.delete_buffer(buffer_name)

    archived = False
    if archive:
        archive_root = state_dir / "tasks" / "_archive"
        archive_root.mkdir(parents=True, exist_ok=True)
        src = state_mod.task_dir(state_dir, task_id)
        dst = archive_root / src.name
        if dst.exists():
            print(
                f"warn: archive target already exists; leaving task dir in place: {dst}",
                file=sys.stderr,
            )
        else:
            os.rename(src, dst)
            archived = True

    return archived


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "cleanup",
        help="Tear down a finished task (worktree, tmux window, optional archive)",
        description=(
            "Run the workspace on_cleanup hook (removes worktree if workspace=worktree), "
            "kill the task's tmux window if any, drop its prompt buffer. Optionally archive "
            "the task directory. Refuses to run on a non-terminal task "
            "unless --force is passed."
        ),
    )
    p.add_argument(
        "task_id",
        nargs="?",
        default=None,
        help="Task id (default: derived from cwd or FLEET_TASK_ID)",
    )
    p.add_argument("--project", default=".", help="Project path (default: cwd)")
    p.add_argument(
        "--archive",
        action="store_true",
        help="Move tasks/task-<id>/ to tasks/_archive/task-<id>/ on success",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Run even if the task isn't in a terminal status",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        state_dir, task_id = task_context.resolve(
            explicit_id=args.task_id,
            cwd=Path(args.project),
        )
    except task_context.TaskNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        task = state_mod.load_task(state_dir, task_id)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    status = task.get("status", "?")
    if status not in TERMINAL_STATUSES and not args.force:
        print(
            f"error: refusing to clean up task-{task_id} (status={status}); "
            f"pass --force to override.",
            file=sys.stderr,
        )
        return 1

    archived = teardown(state_dir, task_id, task, archive=args.archive)

    append_event(
        state_dir / "events.jsonl",
        "cleanup",
        task_id=task_id,
        archived=archived,
    )
    dashboard_mod.rebuild(state_dir)

    print(f"task-{task_id} cleaned" + (" and archived" if archived else ""))
    return 0
