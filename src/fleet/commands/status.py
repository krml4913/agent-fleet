"""``fleet status`` — print a quick snapshot of the current project."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import heartbeat
from .. import leader_notifier
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
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Expand each task: list every stage and the last inbox_seen/heartbeat acks",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help=(
            "Emit a single task's state as a JSON object instead of the table. "
            "In this mode the positional argument is the TASK ID; the project is "
            "resolved from --project or cwd."
        ),
    )
    p.add_argument(
        "--project",
        default=None,
        metavar="NAME",
        help=(
            "Project name for --json mode (default: resolved from cwd via "
            "registry). Lets a leader invoke status by absolute path from "
            "another repo."
        ),
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    if getattr(args, "as_json", False):
        return _run_json(args)

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
    last_acks = _last_acks(events)
    unread = _unread_tasks(events)
    verbose = bool(getattr(args, "verbose", False))
    use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ

    workspace = project.get("workspace") or "worktree"
    print(
        "  ·  ".join(
            [
                project.get("name", "?"),
                workspace,
                f"v{project.get('version', '?')}",
                f"since {_short_date(project.get('created_at', '?'))}",
            ]
        )
    )

    awaiting_orders = [t for t in tasks if t.get("status") == "awaiting_orders"]
    if awaiting_orders:
        print()
        print(
            _style(
                f"⚠ awaiting orders  {len(awaiting_orders)}",
                _RED + _BOLD,
                use_color,
            )
        )
        for t in awaiting_orders:
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
                    "raw_id": tid,
                    "task": t,
                    "status": str(t.get("status", "-")),
                    "stage": _stage_cell(t),
                    "seen": last_seen.get(tid, "—"),
                    "title": str(t.get("title") or "-"),
                    "formation": str(t.get("formation") or "-"),
                    "awaiting": t.get("status") == "awaiting_orders",
                    "unread": tid in unread,
                }
            )
        id_width = max(len(r["id"]) for r in task_rows)
        status_width = max(len(r["status"]) for r in task_rows)
        formation_width = max(len(r["formation"]) for r in task_rows)
        stage_width = max(len(r["stage"]) for r in task_rows)
        seen_width = max(len(r["seen"]) for r in task_rows)
        for row in task_rows:
            bullet = _style("●", _status_color(row["status"]), use_color)
            awaiting = row["awaiting"]
            marker = _style("▸", _RED + _BOLD, use_color) if awaiting else " "
            status_cell = "{:<{w}}".format(row["status"], w=status_width)
            if awaiting:
                status_cell = _style(status_cell, _RED + _BOLD, use_color)
            title = row["title"]
            if row["unread"]:
                title += "  " + _style("[unread inbox]", _YELLOW, use_color)
            print(
                "{marker} {bullet} {id:<{id_width}}  {status}  "
                "{formation:<{formation_width}}  {stage:<{stage_width}}  "
                "{seen:<{seen_width}}  {title}".format(
                    marker=marker,
                    bullet=bullet,
                    id=row["id"],
                    id_width=id_width,
                    status=status_cell,
                    formation=row["formation"],
                    formation_width=formation_width,
                    stage=row["stage"],
                    stage_width=stage_width,
                    seen=row["seen"],
                    seen_width=seen_width,
                    title=title,
                ).rstrip()
            )
            if verbose:
                _print_verbose(row["task"], last_acks.get(row["raw_id"], {}), use_color)

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
    for status in ("done", "running", "awaiting_orders"):
        legend.append(f"{_style('●', _status_color(status), use_color)} {status}")
    print("  ".join(legend))

    return 0


def _run_json(args: argparse.Namespace) -> int:
    """Emit one task's state as a JSON object on stdout (``--json`` mode).

    In this mode the positional ``name`` is the **task id**, not the project.
    The project is resolved from ``--project`` or cwd (mirrors ``start``).
    """
    task_id = getattr(args, "name", None)
    if not task_id:
        print("error: --json requires a task id (positional argument)", file=sys.stderr)
        return 1

    project_name = getattr(args, "project", None)
    state_dir = state_mod.resolve_state_dir(Path.cwd(), project_name=project_name)
    if state_dir is None:
        print(
            f"error: no registered project found for {project_name!r}" if project_name
            else "error: no registered project found for cwd",
            file=sys.stderr,
        )
        return 1

    try:
        task = state_mod.load_task(state_dir, task_id)
    except FileNotFoundError:
        print(
            f"error: no task-{task_id} in project at {state_dir}",
            file=sys.stderr,
        )
        return 1

    obj = _task_json(state_dir, task_id, task)
    print(json.dumps(obj, ensure_ascii=False))
    return 0


def _task_json(state_dir: Path, task_id: str, task: dict) -> dict:
    """Build the machine-readable single-task object for ``--json`` mode."""
    raw_stages = task.get("stages")
    stages: list[dict] = []
    if isinstance(raw_stages, list):
        for stage in raw_stages:
            if not isinstance(stage, dict):
                stages.append({"role": None, "agent": None, "status": None})
                continue
            entry = {
                "role": stage.get("role"),
                "agent": stage.get("agent"),
                "status": stage.get("status"),
            }
            if stage.get("name") is not None:
                entry["name"] = stage.get("name")
            stages.append(entry)

    current_idx = _current_stage_index(task)

    events = read_events(state_dir / "events.jsonl")
    return {
        "task_id": task_id,
        "title": task.get("title"),
        "status": task.get("status"),
        "formation": task.get("formation"),
        "stages": stages,
        "current_stage": current_idx if current_idx >= 0 else None,
        "result": _gate_result(task),
        "pr_url": leader_notifier.scan_pr_url(state_dir, task_id),
        "branch": task.get("branch"),
        "worktree": task.get("worktree"),
        "workspace": task.get("workspace"),
        "last_event": _last_event(events, task_id),
    }


def _gate_result(task: dict) -> str | None:
    """Return the current stage's gate/approval result, or ``None``.

    Mirrors the orchestrator's per-stage bookkeeping: a stage carries an
    explicit ``result`` (e.g. ``changes-requested``) and/or a ``user_approval``
    gate whose ``status`` settles to ``approved``. Prefer the explicit result,
    fall back to a settled approval, else ``None``.
    """
    idx = _current_stage_index(task)
    stages = task.get("stages")
    if idx < 0 or not isinstance(stages, list):
        return None
    stage = stages[idx]
    if not isinstance(stage, dict):
        return None
    result = stage.get("result")
    if result:
        return str(result)
    ua = stage.get("user_approval")
    if isinstance(ua, dict):
        ua_status = ua.get("status")
        if ua_status in {"approved", "rejected"}:
            return str(ua_status)
    return None


def _last_event(events: list[dict], task_id: str) -> dict | None:
    """Return ``{type, ts}`` of the most recent event for ``task_id``, else None."""
    for ev in reversed(events):
        if str(ev.get("task_id", "")) == task_id:
            return {"type": ev.get("type"), "ts": ev.get("ts")}
    return None


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
        awaiting_orders_tasks = []
        for t in tasks:
            s = t.get("status", "?")
            by_status[s] = by_status.get(s, 0) + 1
            if s == "awaiting_orders":
                awaiting_orders_tasks.append(t)

        task_summary = "  ".join(
            f"{count} {_style('●', _status_color(st), use_color)} {st}"
            for st, count in sorted(by_status.items())
        ) if by_status else "(no tasks)"
        print(f"  tasks: {task_summary}")

        if awaiting_orders_tasks:
            print(
                f"  {_style('⚠ awaiting orders', _RED + _BOLD, use_color)}: "
                + ", ".join(f"task-{t.get('id','?')}" for t in awaiting_orders_tasks)
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
    if status in {"awaiting_orders", "failed", "changes-requested"}:
        return _RED
    return ""


def _current_stage_index(task: dict) -> int:
    """Return the valid current-stage index, or -1 if the task has no stages."""
    stages = task.get("stages")
    if not isinstance(stages, list) or not stages:
        return -1
    idx = task.get("current_stage", 0)
    if not isinstance(idx, int) or idx < 0 or idx >= len(stages):
        return -1
    return idx


def _stage_cell(task: dict) -> str:
    """Compact current-stage descriptor: ``stage N/M (role, agent)``.

    Shown for every formation, solo included (``stage 1/1 (driver, claude:opus)``).
    A peer-review phase in progress is appended (``… review ×1``).
    Returns ``-`` for tasks without a usable stage list.
    """
    idx = _current_stage_index(task)
    stages = task.get("stages") or []
    if idx < 0:
        return "-"
    stage = stages[idx]
    if not isinstance(stage, dict):
        return "-"
    role = str(stage.get("role") or "-")
    agent = str(stage.get("agent") or "-")
    cell = f"stage {idx + 1}/{len(stages)} ({role}, {agent})"
    review = _peer_review_progress(stage.get("peer_review"))
    if review:
        cell += f"  {review}"
    return cell


def _stage_status_label(stage: dict, index: int, current: int) -> str:
    """Per-stage label for verbose mode: done / current / pending."""
    if index == current:
        return "current"
    if isinstance(stage, dict) and stage.get("status") == "done":
        return "done"
    if index < current:
        return "done"
    return "pending"


def _print_verbose(task: dict, acks: dict, use_color: bool) -> None:
    """Print the per-stage breakdown and last acks for one task."""
    stages = task.get("stages")
    current = _current_stage_index(task)
    if isinstance(stages, list) and stages:
        count = len(stages)
        rows = []
        for i, stage in enumerate(stages):
            role = str(stage.get("role") or "-") if isinstance(stage, dict) else "-"
            agent = str(stage.get("agent") or "-") if isinstance(stage, dict) else "-"
            rows.append(
                {
                    "label": f"stage {i + 1}/{count}",
                    "state": _stage_status_label(stage, i, current),
                    "role": role,
                    "agent": agent,
                    "current": i == current,
                }
            )
        state_width = max(len(r["state"]) for r in rows)
        role_width = max(len(r["role"]) for r in rows)
        for r in rows:
            line = "      {label}  {state:<{sw}}  {role:<{rw}}  {agent}".format(
                label=r["label"],
                state=r["state"],
                sw=state_width,
                role=r["role"],
                rw=role_width,
                agent=r["agent"],
            )
            if r["current"]:
                line = _style(line, _BOLD, use_color)
            print(line)

    seen = _short_time(acks.get("inbox_seen")) if acks.get("inbox_seen") else "—"
    beat = _short_time(acks.get("heartbeat")) if acks.get("heartbeat") else "—"
    print(f"      acks: inbox_seen {seen}  ·  heartbeat {beat}")


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


def _last_acks(events: list[dict]) -> dict[str, dict[str, str]]:
    """Map ``task_id`` → latest ``inbox_seen`` / ``heartbeat`` event timestamps.

    events.jsonl is append-only, so later occurrences overwrite earlier ones.
    """
    out: dict[str, dict[str, str]] = {}
    for ev in events:
        tid = ev.get("task_id")
        ts = ev.get("ts")
        if not tid or not ts:
            continue
        t = ev.get("type", "")
        if t in {"inbox_seen", "heartbeat"}:
            out.setdefault(str(tid), {})[t] = ts
    return out


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
