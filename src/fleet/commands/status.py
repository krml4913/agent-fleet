"""``fleet status`` — print a quick snapshot of the current project."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .. import heartbeat
from .. import state as state_mod
from ..events import read_events
from ..state import load_registry, project_state_dir

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
        "name",
        nargs="?",
        default=None,
        help="Project name (default: resolved from cwd via registry)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        dest="all_projects",
        help="Show summary for all registered projects",
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
    if getattr(args, "all_projects", False):
        return _run_all(args)

    project_name = getattr(args, "name", None)
    state_dir = state_mod.resolve_state_dir(Path.cwd(), project_name=project_name)
    if state_dir is None:
        print(
            f"error: no registered project found for {project_name!r}" if project_name
            else "error: no registered project found for cwd",
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
                    "progress": _task_progress(t),
                    "seen": last_seen.get(tid, "—"),
                    "title": t.get("title", "-"),
                    "topology": str(t.get("topology") or "-"),
                    "agent": _task_agent(t),
                    "workflow": t.get("workflow", "-"),
                    "unread": tid in unread,
                }
            )
        id_width = max(len(r["id"]) for r in task_rows)
        status_width = max(len(_status_text(r)) for r in task_rows)
        for row in task_rows:
            bullet = _style("●", _status_color(row["status"]), use_color)
            status_text = _status_text(row)
            print(
                "  {bullet} {id:<{id_width}}  {status:<{status_width}}  seen {seen}".format(
                    bullet=bullet,
                    id=row["id"],
                    id_width=id_width,
                    status=status_text,
                    status_width=status_width,
                    seen=row["seen"],
                )
            )
            meta = (
                f"topology {row['topology']}  "
                f"agent {row['agent']}  workflow {row['workflow']}"
            )
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


def _run_all(args: argparse.Namespace) -> int:
    """Print a summary for every registered project."""
    reg = state_mod.load_registry()
    projects = reg.get("projects", {})
    use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ

    if not projects:
        print("(no registered projects — run `fleet init` first)")
        return 0

    for name, entry in projects.items():
        repo = entry.get("repo", "?")
        state_dir = state_mod.project_state_dir(name)
        repo_exists = Path(repo).is_dir()

        header = _style(f"▶ {name}", _BOLD, use_color)
        print(header + f"  ({repo})")

        if not repo_exists:
            print(
                f"  {_style('⚠ repo missing', _RED + _BOLD, use_color)}"
                f"  — fleet rm {name}"
            )

        if not state_dir.is_dir():
            print("  (state dir not found)")
            print()
            continue

        tasks = state_mod.list_tasks(state_dir)
        by_status: dict[str, int] = {}
        needs_input_tasks = []
        for t in tasks:
            s = t.get("status", "?")
            by_status[s] = by_status.get(s, 0) + 1
            if s == "needs_input":
                needs_input_tasks.append(t)

        task_summary = "  ".join(
            f"{count} {_style('●', _status_color(st), use_color)} {st}"
            for st, count in sorted(by_status.items())
        ) if by_status else "(no tasks)"
        print(f"  tasks: {task_summary}")

        if needs_input_tasks:
            print(
                f"  {_style('⚠ needs input', _RED + _BOLD, use_color)}: "
                + ", ".join(f"task-{t.get('id','?')}" for t in needs_input_tasks)
            )

        print()

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


def _status_text(row: dict) -> str:
    progress = row.get("progress")
    if progress:
        return f"{row['status']}  {progress}"
    return str(row["status"])


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


def _task_progress(task: dict) -> str:
    current_stage = task.get("current_stage", 0)
    stages = task.get("stages") or []
    if not isinstance(stages, list):
        return ""
    if not isinstance(current_stage, int):
        return ""
    if current_stage < 0 or current_stage >= len(stages):
        return ""

    parts = []
    if len(stages) > 1:
        parts.append(f"stage {current_stage + 1}/{len(stages)}")

    stage = stages[current_stage]
    if isinstance(stage, dict):
        review = _peer_review_progress(stage.get("peer_review"))
        if review:
            parts.append(review)

    return "  ".join(parts)


def _peer_review_progress(peer_review: object) -> str:
    if not isinstance(peer_review, dict):
        return ""
    if peer_review.get("phase") not in {"implementing", "reviewing"}:
        return ""

    iteration = peer_review.get("iteration")
    if iteration is None or iteration == "":
        return "review"
    return f"review ×{iteration}"


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
