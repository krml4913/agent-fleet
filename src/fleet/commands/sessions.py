"""``fleet sessions`` — read-only view of leader sessions and their work.

Lists every known leader session (``global/sessions/<label>/session.json``,
Issue #166 §5.6) cross-referenced with tmux for liveness, and — per session —
its **in-flight tasks**: a scan of ``task.yaml`` across all registered projects
for ``owner_session == label`` with a non-terminal status.

On-demand read only (design principle 7 — no polling, no state writes). A task
with no ``owner_session`` is attributed to ``main`` (the default label), matching
:func:`fleet.state.task_owner_session`, so cutover-era tasks never disappear.
"""
from __future__ import annotations

import argparse
import os
import sys

from .. import state as state_mod
from .. import status_data as status_data_mod
from .. import tmux as tmux_mod

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "sessions",
        help="List leader sessions and their in-flight tasks",
        description=(
            "Show every known leader session (label → tmux pane + agent) with a "
            "live/stale marker, and each session's in-flight tasks scanned across "
            "all projects by owner_session. Read-only; never writes state."
        ),
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ

    records = _load_session_records()
    tasks_by_label = _scan_inflight_tasks()

    # Union: every session with a record, plus every label some in-flight task
    # claims (a session may own work without a record — or vice versa).
    labels = sorted(set(records) | set(tasks_by_label))

    print(_style(f"SESSIONS  {len(labels)}", _BOLD, use_color))
    if not labels:
        print("  (no leader sessions)")
        return 0

    tmux_ok = tmux_mod.available()
    for label in labels:
        record = records.get(label)
        tasks = tasks_by_label.get(label, [])
        live = _liveness(label, tmux_ok)

        print()
        bullet = _style("●", _GREEN if live is True else _DIM, use_color)
        live_cell = {True: "live", False: "stale", None: "?"}[live]
        agent = (record or {}).get("agent", "—")
        pane = (record or {}).get("pane", f"fleet-{label}:leader")
        since = _short_date((record or {}).get("started_at", ""))
        header = f"{bullet} {_style(label, _BOLD, use_color)}  ({live_cell})  {agent}  {pane}"
        if since:
            header += f"  since {since}"
        if record is None:
            header += _style("  [no session record]", _YELLOW, use_color)
        print(header)

        # Show scope line
        scope = state_mod.session_scope(label)
        if scope is not None:
            print(f"    scope: {', '.join(scope)}")
        else:
            print("    scope: (all projects)")

        if not tasks:
            print(_style("    (no in-flight tasks)", _DIM, use_color))
            continue
        id_width = max(len(f"task-{t['id']}") for t in tasks)
        proj_width = max(len(t["project"]) for t in tasks)
        status_width = max(len(t["status"]) for t in tasks)
        for t in tasks:
            out_of_scope = scope is not None and t["project"] not in scope
            suffix = _style("  (out of scope)", _YELLOW, use_color) if out_of_scope else ""
            print(
                (
                    "    {proj:<{pw}}  {tid:<{iw}}  {status:<{sw}}  {title}".format(
                        proj=t["project"],
                        pw=proj_width,
                        tid=f"task-{t['id']}",
                        iw=id_width,
                        status=t["status"],
                        sw=status_width,
                        title=t["title"],
                    ).rstrip()
                )
                + suffix
            )

    return 0


_liveness = status_data_mod.session_liveness
_load_session_records = status_data_mod.load_session_records
_scan_inflight_tasks = status_data_mod.scan_inflight_tasks


def _style(text: str, code: str, enabled: bool) -> str:
    if not enabled or not code:
        return text
    return f"{code}{text}{_RESET}"


def _short_date(value: str) -> str:
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return ""
