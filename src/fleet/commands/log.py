"""``fleet log`` — tail events.jsonl with optional filters."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import state as state_mod
from ..events import read_events


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "log",
        help="Tail events.jsonl",
        description=(
            "Print recent events. Optional --task / --type filters; -n "
            "limits how many to show."
        ),
    )
    p.add_argument(
        "task_id",
        nargs="?",
        default=None,
        help="Filter by task id (default: all tasks)",
    )
    p.add_argument("--project", default=".", help="Project path (default: cwd)")
    p.add_argument(
        "-n",
        "--lines",
        type=int,
        default=20,
        metavar="N",
        help="Show the last N events (default: 20, 0 = all)",
    )
    p.add_argument(
        "--type",
        action="append",
        default=[],
        metavar="T",
        help="Filter by event type; repeatable",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    state_dir = state_mod.discover_state_dir(Path(args.project))
    if state_dir is None:
        print(
            f"error: no .fleet-state/ found under {Path(args.project).resolve()}",
            file=sys.stderr,
        )
        return 1

    events = read_events(state_dir / "events.jsonl")
    if args.task_id is not None:
        events = [e for e in events if str(e.get("task_id", "")) == args.task_id]
    if args.type:
        types = set(args.type)
        events = [e for e in events if e.get("type") in types]
    if args.lines and args.lines > 0:
        events = events[-args.lines :]

    if not events:
        print("(no matching events)")
        return 0

    for ev in events:
        ts = ev.get("ts", "?")
        typ = ev.get("type", "?")
        tid = ev.get("task_id", "—")
        extras = {
            k: v for k, v in ev.items() if k not in ("ts", "type", "task_id")
        }
        extra_str = " ".join(f"{k}={v}" for k, v in extras.items())
        print(f"{ts}  {typ:<16}  task-{tid:<5}  {extra_str}")
    return 0
