"""``fleet status`` — print a quick snapshot of the current project."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .. import heartbeat
from .. import state as state_mod
from ..events import read_events

_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"


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
    use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ

    workflow = project.get("workflow") or "bare"
    print(
        "  ·  ".join(
            [
                project.get("name", "?"),
                workflow,
                f"v{project.get('version', '?')}",
                f"since {_short_date(project.get('created_at', '?'))}",
            ]
        )
    )

    needs_input = [t for t in tasks if t.get("status") == "needs_input"]
    if needs_input:
        print()
        print(
            _style(
                f"⚠ needs your input  {len(needs_input)}",
                _RED + _BOLD,
                use_color,
            )
        )
        for t in needs_input:
            print(f"  task-{t.get('id', '?')}  {t.get('title', '-')}")

    print()
    print(_style(f"TASKS  {len(tasks)}", _BOLD, use_color))
    if not tasks:
        print("  (none)")
    else:
        task_rows = []
        for t in tasks:
            tid = t.get("id", "?")
            task_rows.append(
                {
                    "id": f"task-{tid}",
                    "status": str(t.get("status", "-")),
                    "seen": last_seen.get(tid, "—"),
                    "title": t.get("title", "-"),
                    "agent": _task_agent(t),
                    "workflow": t.get("workflow", "-"),
                    "unread": tid in unread,
                }
            )
        id_width = max(len(r["id"]) for r in task_rows)
        status_width = max(len(r["status"]) for r in task_rows)
        for row in task_rows:
            bullet = _style("●", _status_color(row["status"]), use_color)
            print(
                "  {bullet} {id:<{id_width}}  {status:<{status_width}}  seen {seen}".format(
                    bullet=bullet,
                    id=row["id"],
                    id_width=id_width,
                    status=row["status"],
                    status_width=status_width,
                    seen=row["seen"],
                )
            )
            meta = f"agent {row['agent']}  workflow {row['workflow']}"
            if row["unread"]:
                meta += "  [unread inbox]"
            print(f"      {row['title']}  ({meta})")

    if args.events > 0:
        print()
        print(_style(f"EVENTS  last {args.events} / {len(events)}", _BOLD, use_color))
        if not events:
            print("  (none)")
        else:
            shown_events = events[-args.events :]
            type_width = max(len(str(ev.get("type", "?"))) for ev in shown_events)
            for ev in shown_events:
                tid = ev.get("task_id", "—")
                print(
                    "  {time}  {type:<{type_width}}  task-{tid}".format(
                        time=_short_time(ev.get("ts", "?")),
                        type=str(ev.get("type", "?")),
                        type_width=type_width,
                        tid=tid,
                    )
                )

    print()
    legend = []
    for status in ("done", "running", "needs_input"):
        legend.append(f"{_style('●', _status_color(status), use_color)} {status}")
    print("  ".join(legend))

    return 0


def _style(text: str, code: str, enabled: bool) -> str:
    if not enabled or not code:
        return text
    return f"{code}{text}{_RESET}"


def _status_color(status: str) -> str:
    if status in {"done", "approved"}:
        return _GREEN
    if status in {"running", "spawning"}:
        return _YELLOW
    if status in {"needs_input", "failed", "changes-requested"}:
        return _RED
    return ""


def _task_agent(task: dict) -> str:
    current_stage = task.get("current_stage", 0)
    stages = task.get("stages") or []
    if not isinstance(current_stage, int):
        return "-"
    if current_stage < 0 or current_stage >= len(stages):
        return "-"
    stage = stages[current_stage]
    if not isinstance(stage, dict):
        return "-"
    return str(stage.get("agent") or "-")


def _short_date(value: str) -> str:
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return "?"


def _short_time(value: str) -> str:
    if isinstance(value, str) and len(value) >= 16 and "T" in value:
        return value[11:16]
    return "??:??"


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
