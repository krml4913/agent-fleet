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
    orch.advance(state_dir, task_id, task, result=result)

    append_event(state_dir / "events.jsonl", "done", task_id=task_id)

    project = state_mod.load_project(state_dir)
    notify.send(
        state_dir,
        title=f"fleet {project.get('name', '?')}: task-{task_id} done",
        message=f"task-{task_id} marked complete",
    )

    print(f"task-{task_id} marked done")
    return 0
