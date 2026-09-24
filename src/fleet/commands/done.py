"""``fleet-agent done [task-id]`` — mark the current stage done.

The stage transition logic lives in :mod:`fleet.orchestrator`; this command
is intentionally thin: resolve context → call orchestrator → emit event.

Real cleanup (worktree removal, branch deletion, multiplexer window kill) belongs
in ``fleet-agent cleanup``.
"""
from __future__ import annotations

import argparse
import sys

from .. import leader_notifier
from .. import notify
from .. import orchestrator as orch
from .. import state as state_mod
from .. import task_context
from ..events import append_event


_truthy = leader_notifier.truthy


def _next_handoff_role(stages: list, idx: int) -> str:
    """Role of the driver that actually runs next after an intermediate handoff.

    A peer_review handoff keeps the **same** stage and only flips the
    implementer/reviewer phase, so ``stages[idx]["role"]`` is the stage's
    primary (implementer) role — not necessarily who runs next. When the stage
    is in the ``reviewing`` phase the next driver is the reviewer
    (``peer_review.role``); otherwise it is the stage's own role (a multi-stage
    advance, or a rework iteration back to the implementer).
    """
    if not (0 <= idx < len(stages)):
        return "?"
    stage = stages[idx]
    pr = stage.get("peer_review")
    if isinstance(pr, dict) and pr.get("role") and pr.get("phase") == "reviewing":
        return str(pr.get("role") or "?")
    return str(stage.get("role", "?") or "?")


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "done",
        help="Mark the current stage done",
        description=(
            "Mark the current stage done and advance the task state machine. "
            "With --result=approved (default) the next stage is launched or "
            "the task is completed. With --result=changes-requested the stage "
            "result is recorded for the stage-5 peer_review loop. "
            "Cleanup (worktree / branch / multiplexer window) is done via fleet-agent cleanup."
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
        next_role = _next_handoff_role(stages, current_idx)
        level = "progress"
        title = f"fleet {project_name}: task-{task_id} stage {N} handed off"
        message = f"task-{task_id} stage {N} handed off → {next_role}"

    notify.send(
        state_dir, title=title, message=message, level=level,
        project=project_name, task_id=task_id,
        # Approval gate only: the Windows toast gets Approve/Reject/Open buttons (#318).
        approval_stage=current_idx if status == "awaiting_orders" else None,
    )

    _maybe_notify_leader(
        state_dir,
        task_id,
        task,
        project,
        project_name,
        status=status,
        result=result,
        summary=message,
    )

    print(f"task-{task_id} marked done")
    return 0


def _maybe_notify_leader(
    state_dir,
    task_id: str,
    task: dict,
    project: dict,
    project_name: str,
    *,
    status: str,
    result: str,
    summary: str,
) -> None:
    """Opt-in leader-pane push of a ``done`` (:func:`leader_notifier.push_to_leader`)."""
    # Only notify the leader for events that require action: task completed or
    # awaiting_orders (user_approval gate). Intermediate peer_review / multi_stage
    # handoffs are internal driver-to-driver transfers — the leader has nothing to
    # do until the whole stage chain reaches a terminal state.
    if status not in ("completed", "awaiting_orders"):
        return

    leader_notifier.push_to_leader(
        state_dir,
        task_id,
        task,
        project,
        project_name,
        status=status,
        summary=summary,
        result=result,
    )
