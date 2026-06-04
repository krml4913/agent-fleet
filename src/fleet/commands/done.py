"""``fleet-agent done [task-id]`` — mark the current stage done.

The stage transition logic lives in :mod:`fleet.orchestrator`; this command
is intentionally thin: resolve context → call orchestrator → emit event.

Real cleanup (worktree removal, branch deletion, tmux window kill) belongs
in ``fleet-agent cleanup``.
"""
from __future__ import annotations

import argparse
import sys

from .. import notify
from .. import orchestrator as orch
from .. import state as state_mod
from .. import task_context
from ..events import append_event


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "done",
        help="Mark the current stage done",
        description=(
            "Mark the current stage done and advance the task state machine. "
            "With --result=approved (default) the next stage is launched or "
            "the task is completed. With --result=changes-requested the stage "
            "result is recorded for the stage-5 peer_review loop. "
            "Cleanup (worktree / branch / tmux window) is done via fleet-agent cleanup."
        ),
    )
    p.add_argument(
        "task_id",
        nargs="?",
        default=None,
        help="Task id (default: derived from cwd or FLEET_TASK_ID)",
    )
    p.add_argument(
        "--result",
        default="approved",
        choices=["approved", "changes-requested"],
        help="Stage result (default: approved)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        state_dir, task_id = task_context.resolve(explicit_id=args.task_id)
    except task_context.TaskNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        task = state_mod.load_task(state_dir, task_id)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    result = getattr(args, "result", "approved") or "approved"
    prev_stage_idx = task.get("current_stage", 0)
    if not isinstance(prev_stage_idx, int):
        prev_stage_idx = 0
    orch.advance(state_dir, task_id, task, result=result)
    task = state_mod.load_task(state_dir, task_id)

    append_event(state_dir / "events.jsonl", "done", task_id=task_id)

    project = state_mod.load_project(state_dir)
    project_name = project.get("name", "?")
    status = task.get("status", "")
    stages = task.get("stages") or []
    M = len(stages)

    if status == "completed":
        level = "success"
        title = f"fleet {project_name}: task-{task_id} completed"
        message = f"task-{task_id} completed (all stages finished)"
    elif status == "awaiting_orders":
        current_idx = task.get("current_stage", 0)
        if not isinstance(current_idx, int):
            current_idx = 0
        N = current_idx + 1
        role = stages[current_idx].get("role", "?") if 0 <= current_idx < M else "?"
        level = "waiting"
        title = f"fleet {project_name}: task-{task_id} stage {N}/{M} awaiting approval"
        message = f"task-{task_id} stage {N}/{M} ({role}) done — awaiting approval"
    else:
        N = prev_stage_idx + 1
        current_idx = task.get("current_stage", 0)
        if not isinstance(current_idx, int):
            current_idx = 0
        next_role = stages[current_idx].get("role", "?") if 0 <= current_idx < M else "?"
        level = "progress"
        title = f"fleet {project_name}: task-{task_id} stage {N} done"
        message = f"task-{task_id} stage {N} done → next stage ({next_role}) starting"

    notify.send(state_dir, title=title, message=message, level=level)

    print(f"task-{task_id} marked done")
    return 0
