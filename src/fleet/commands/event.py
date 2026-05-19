"""``fleet-agent event emit <type> [--field K=V ...]`` — record an audit event."""
from __future__ import annotations

import argparse
import sys

from .. import task_context
from ..events import append_event


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "event",
        help="Emit an audit event",
        description="Append a record to events.jsonl tagged with the current task id.",
    )
    sp = p.add_subparsers(dest="event_cmd", required=True, metavar="<sub>")

    p_emit = sp.add_parser("emit", help="Append one event record")
    p_emit.add_argument("type", help="Event type (e.g. milestone, progress, note)")
    p_emit.add_argument(
        "--task-id",
        default=None,
        help="Override the auto-detected task id",
    )
    p_emit.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="K=V",
        help="Extra field; repeatable. Use K=V syntax.",
    )
    p_emit.set_defaults(func=run_emit)


def run_emit(args: argparse.Namespace) -> int:
    try:
        state_dir, task_id = task_context.resolve(explicit_id=args.task_id)
    except task_context.TaskNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    extra: dict[str, str] = {}
    for raw in args.field:
        if "=" not in raw:
            print(f"error: --field expects K=V, got {raw!r}", file=sys.stderr)
            return 1
        k, _, v = raw.partition("=")
        k = k.strip()
        if not k:
            print(f"error: empty key in --field {raw!r}", file=sys.stderr)
            return 1
        extra[k] = v
    extra["task_id"] = task_id

    append_event(state_dir / "events.jsonl", args.type, **extra)
    print(f"emitted {args.type} for task-{task_id}")
    return 0
