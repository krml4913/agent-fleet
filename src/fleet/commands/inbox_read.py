"""``fleet-agent inbox-read`` — driver-side: read inbox.md and emit an ack.

Prints the current inbox.md to stdout, then appends an ``inbox_seen`` event
to events.jsonl with the watermark (timestamp of the last message block).
Drivers must use this instead of reading inbox.md directly so the ack fires.
"""
from __future__ import annotations

import argparse
import re
import sys

from .. import state as state_mod
from .. import task_context
from ..events import append_event


_TS_HEADER_RE = re.compile(r"^### (\S+)$", re.MULTILINE)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "inbox-read",
        help="Driver-side: read inbox.md and emit inbox_seen ack",
        description=(
            "Print the current task's inbox.md to stdout and record an "
            "`inbox_seen` event with a watermark so the leader knows how "
            "far the driver has read."
        ),
    )
    p.add_argument(
        "--task-id",
        default=None,
        help="Override the auto-detected task id",
    )
    p.set_defaults(func=run)


def extract_watermark(text: str) -> str | None:
    """Return the last ``### <timestamp>`` header value, or None if absent."""
    matches = _TS_HEADER_RE.findall(text)
    return matches[-1] if matches else None


def run(args: argparse.Namespace) -> int:
    try:
        state_dir, task_id = task_context.resolve(explicit_id=args.task_id)
    except task_context.TaskNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    inbox_path = state_mod.task_dir(state_dir, task_id) / "inbox.md"
    if not inbox_path.exists():
        print(f"error: inbox.md not found for task-{task_id}", file=sys.stderr)
        return 1

    text = inbox_path.read_text(encoding="utf-8")
    print(text, end="")

    watermark = extract_watermark(text)
    append_event(
        state_dir / "events.jsonl",
        "inbox_seen",
        task_id=task_id,
        watermark=watermark,
    )

    return 0
