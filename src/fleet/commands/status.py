"""``fleet status`` — print a quick snapshot of the current project."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import heartbeat
from .. import state as state_mod
from ..events import read_events


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "status",
        help="Print current project status",
        description="Show project info, task list, and recent events.",
    )
    p.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Project root path (default: cwd)",
    )
    p.add_argument(
        "--events",
        type=int,
        default=5,
        metavar="N",
        help="Number of recent events to show (default: 5; 0 to omit)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    state_dir = state_mod.discover_state_dir(Path(args.path))
    if state_dir is None:
        print(
            f"error: no .fleet-state/ found under {Path(args.path).resolve()}",
            file=sys.stderr,
        )
        return 1

    project = state_mod.load_project(state_dir)
    tasks = state_mod.list_tasks(state_dir)
    events = read_events(state_dir / "events.jsonl")
    last_seen = heartbeat.last_per_task(events)
    unread = _unread_tasks(events)

    workflow = project.get("workflow") or "bare"
    print(f"project: {project.get('name', '?')}")
    print(f"  state dir: {state_dir}")
    print(f"  created:   {project.get('created_at', '?')}")
    print(f"  fleet ver: {project.get('version', '?')}")
    print(f"  workflow:  {workflow}")

    needs_input = [t for t in tasks if t.get("status") == "needs_input"]
    if needs_input:
        print()
        print(f"⚠  needs your input ({len(needs_input)}):")
        for t in needs_input:
            print(f"  task-{t.get('id', '?')}  {t.get('title', '-')}")

    print()
    print(f"tasks ({len(tasks)}):")
    if not tasks:
        print("  (none)")
    else:
        for t in tasks:
            tid = t.get("id", "?")
            seen = last_seen.get(tid, "—")
            inbox_flag = " [unread inbox]" if tid in unread else ""
            print(
                "  task-{id}  [{status}]  {title}  ({agent}, {workflow}, seen {seen}){flag}".format(
                    id=tid,
                    status=t.get("status", "-"),
                    title=t.get("title", "-"),
                    agent=t.get("agent", "-"),
                    workflow=t.get("workflow", "-"),
                    seen=seen,
                    flag=inbox_flag,
                )
            )

    if args.events > 0:
        print()
        print(f"recent events (last {args.events} of {len(events)}):")
        if not events:
            print("  (none)")
        else:
            for ev in events[-args.events :]:
                tid = ev.get("task_id", "—")
                print(
                    f"  {ev.get('ts', '?')}  {ev.get('type', '?'):<14}  task-{tid}"
                )

    return 0


def _unread_tasks(events: list[dict]) -> set[str]:
    """Return task ids that have inbox messages newer than the last inbox_seen ack.

    Unread = latest inbox_message.ts > latest inbox_seen.watermark for that task.
    Tasks with no inbox_seen at all but with inbox_message events are unread.
    """
    last_msg: dict[str, str] = {}
    last_ack: dict[str, str] = {}
    for ev in events:
        tid = ev.get("task_id", "")
        if not tid:
            continue
        t = ev.get("type", "")
        ts = ev.get("ts", "")
        if t == "inbox_message" and ts:
            last_msg[tid] = ts
        elif t == "inbox_seen":
            watermark = ev.get("watermark") or ""
            last_ack[tid] = watermark
    unread: set[str] = set()
    for tid, msg_ts in last_msg.items():
        watermark = last_ack.get(tid, "")
        if msg_ts > watermark:
            unread.add(tid)
    return unread
