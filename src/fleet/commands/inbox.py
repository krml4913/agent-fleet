"""``fleet-agent inbox <task-id> "<message>"`` — send a message to a driver's inbox.md.

A timestamped block is appended; the driver reads `inbox.md` on its
own cadence (per the rules in `driver-prompt.md`).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import state as state_mod
from ..events import append_event, utcnow_iso


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "inbox",
        help="Append a message to a driver's inbox.md",
        description=(
            "Adds a timestamped block to <state>/tasks/task-<id>/inbox.md "
            "and emits an `inbox_message` event."
        ),
    )
    p.add_argument("task_id", help="Task id")
    p.add_argument(
        "message",
        nargs="+",
        help="Message body (joined with spaces if multiple words)",
    )
    p.add_argument("--project", default=".", help="Project path (default: cwd)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    state_dir = state_mod.discover_state_dir(Path(args.project))
    if state_dir is None:
        print(
            f"error: no .fleet-state/ found under {Path(args.project).resolve()}",
            file=sys.stderr,
        )
        return 1

    task_dir = state_mod.task_dir(state_dir, args.task_id)
    if not task_dir.is_dir():
        print(f"error: no task dir: {task_dir}", file=sys.stderr)
        return 1

    inbox_path = task_dir / "inbox.md"
    body = " ".join(args.message)
    block = f"### {utcnow_iso()}\n\n{body}\n\n"
    existing = inbox_path.read_text(encoding="utf-8") if inbox_path.exists() else ""
    inbox_path.write_text(existing + block, encoding="utf-8")

    append_event(
        state_dir / "events.jsonl",
        "inbox_message",
        task_id=args.task_id,
        message=body,
    )

    print(f"sent to task-{args.task_id} inbox ({inbox_path})")
    return 0
