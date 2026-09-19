"""``fleet-agent approve/reject <task-id>`` — relay user approval gates."""
from __future__ import annotations

import argparse
import sys

from .. import orchestrator as orch
from .. import state as state_mod
from .. import task_context
from ..events import append_event, truncate_text


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
            "the stage to implementation. The driver's inbox always gets a "
            "[fleet reject] note (with the reason when given) so it does not "
            "re-submit unchanged work."
        ),
    )
    _add_task_id(p)
    reason = p.add_mutually_exclusive_group()
    reason.add_argument(
        "--reason",
        default=None,
        metavar="TEXT",
        help="Why the work was rejected; relayed to the driver's inbox",
    )
    reason.add_argument(
        "--reason-file",
        default=None,
        metavar="PATH",
        help="Read the rejection reason from a UTF-8 file (for long feedback)",
    )
    p.set_defaults(func=run_reject)


def _add_task_id(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "task_id",
        nargs="?",
        default=None,
        help="Task id (default: derived from cwd or FLEET_TASK_ID)",
    )
    p.add_argument(
        "--project",
        default=".",
        metavar="NAME",
        help=(
            "Project name (registry). Required from a project-agnostic leader "
            "session; defaults to cwd / FLEET_STATE_DIR resolution otherwise."
        ),
    )


def run_approve(args: argparse.Namespace) -> int:
    return _run(args, approved=True)


def run_reject(args: argparse.Namespace) -> int:
    return _run(args, approved=False)


def _run(args: argparse.Namespace, *, approved: bool) -> int:
    project_arg = getattr(args, "project", ".")
    project_name = project_arg if project_arg != "." else None
    try:
        state_dir, task_id = task_context.resolve(
            explicit_id=args.task_id,
            project_name=project_name,
        )
    except task_context.TaskNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        task = state_mod.load_task(state_dir, task_id)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    reason: str | None = None
    if not approved:
        try:
            reason = _read_reason(args)
        except (OSError, UnicodeDecodeError) as e:
            print(f"error: cannot read --reason-file: {e}", file=sys.stderr)
            return 1

    try:
        if approved:
            orch.approve_user_approval(state_dir, task_id, task)
            event_type = "approve"
            verb = "approved"
        else:
            orch.reject_user_approval(state_dir, task_id, task, reason=reason)
            event_type = "reject"
            verb = "rejected"
    except orch.UserApprovalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    event_fields: dict[str, str] = {}
    if reason:
        # Audit snippet only — the full reason lives in the driver's inbox.md.
        event_fields["reason"] = truncate_text(reason)
    append_event(state_dir / "events.jsonl", event_type, task_id=task_id, **event_fields)
    print(f"task-{task_id} user approval {verb}")
    return 0


def _read_reason(args: argparse.Namespace) -> str | None:
    """The rejection reason from ``--reason`` / ``--reason-file``; None when blank."""
    reason = getattr(args, "reason", None)
    reason_file = getattr(args, "reason_file", None)
    if reason_file is not None:
        # utf-8-sig: also accepts the BOM Windows PowerShell 5.1 puts on ``>`` output.
        with open(reason_file, encoding="utf-8-sig") as f:
            reason = f.read()
    reason = (reason or "").strip()
    return reason or None
