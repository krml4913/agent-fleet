"""``fleet-agent approve/reject <task-id>`` — relay user approval gates."""
from __future__ import annotations

import argparse
import sys

from .. import orchestrator as orch
from .. import state as state_mod
from .. import task_context
from ..events import append_event


def add_approve_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "approve",
        help="Relay user approval for a user_approval gate",
        description="Approve the current stage's pending user_approval gate.",
    )
    _add_task_id(p)
    p.set_defaults(func=run_approve)


def add_reject_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "reject",
        help="Relay user rejection for a user_approval gate",
        description=(
            "Reject the current stage's pending user_approval gate and return "
            "the stage to implementation."
        ),
    )
    _add_task_id(p)
    p.set_defaults(func=run_reject)


def _add_task_id(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "task_id",
        nargs="?",
        default=None,
        help="Task id (default: derived from cwd or FLEET_TASK_ID)",
    )


def run_approve(args: argparse.Namespace) -> int:
    return _run(args, approved=True)


def run_reject(args: argparse.Namespace) -> int:
    return _run(args, approved=False)


def _run(args: argparse.Namespace, *, approved: bool) -> int:
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

    try:
        if approved:
            orch.approve_user_approval(state_dir, task_id, task)
            event_type = "approve"
            verb = "approved"
        else:
            orch.reject_user_approval(state_dir, task_id, task)
            event_type = "reject"
            verb = "rejected"
    except orch.UserApprovalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    append_event(state_dir / "events.jsonl", event_type, task_id=task_id)
    print(f"task-{task_id} user approval {verb}")
    return 0
