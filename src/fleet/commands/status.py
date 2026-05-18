"""``fleet status`` — print a quick snapshot of the current project."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

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

    print(f"project: {project.get('name', '?')}")
    print(f"  state dir: {state_dir}")
    print(f"  created:   {project.get('created_at', '?')}")
    print(f"  fleet ver: {project.get('version', '?')}")
    print()
    print(f"tasks ({len(tasks)}):")
    if not tasks:
        print("  (none)")
    else:
        for t in tasks:
            print(
                "  task-{id}  [{status}]  {title}  ({agent})".format(
                    id=t.get("id", "?"),
                    status=t.get("status", "-"),
                    title=t.get("title", "-"),
                    agent=t.get("agent", "-"),
                )
            )

    if args.events > 0:
        events = read_events(state_dir / "events.jsonl")
        print()
        print(f"recent events (last {args.events} of {len(events)}):")
        if not events:
            print("  (none)")
        else:
            for ev in events[-args.events :]:
                print(f"  {ev.get('ts', '?')}  {ev.get('type', '?')}  {ev}")

    return 0
