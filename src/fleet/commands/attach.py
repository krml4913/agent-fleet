"""``fleet attach [<target>]`` — attach to a leader session or a task's driver window.

Sessions are ``fleet-<label>``, keyed by the leader session label (Issue #166
§5.2), not by project: a task's driver window lives in ``fleet-<owner_session>``
(:func:`fleet.state.task_owner_session`) and the ``leader`` target is a session
label chosen with ``--session``.
"""
from __future__ import annotations

import argparse
import os
import sys

from .. import state as state_mod
from .. import status_data as status_data_mod
from .. import task_context
from .. import mux
from .leader import DEFAULT_SESSION_LABEL


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "attach",
        help="Attach to a leader session's leader pane or a task driver pane",
        description=(
            "Attach to a multiplexer session on the given window (tmux window / "
            "zellij tab; on tmux this is like `tmux attach -t fleet-<label>:<window>`). "
            "Target is either 'leader' (default) or a task id. A task id attaches "
            "to the driver window in the session that owns the task "
            "(fleet-<owner_session>); 'leader' attaches to the leader window of the "
            "session chosen with --session."
        ),
    )
    p.add_argument(
        "target",
        nargs="?",
        default="leader",
        help="'leader' (default) or a task id (e.g. 1, 42)",
    )
    p.add_argument(
        "--project",
        default=".",
        help=(
            "Project name (registry) a task id belongs to; required from a "
            "project-agnostic leader session, else resolved from FLEET_STATE_DIR / "
            "cwd. Not used for the 'leader' target."
        ),
    )
    p.add_argument(
        "--session",
        default=None,
        metavar="LABEL",
        help=(
            "Leader session label for the 'leader' target → session fleet-<label> "
            f"(default: $FLEET_SESSION, else {DEFAULT_SESSION_LABEL}). "
            "A task id ignores it: the task's own owner session is used."
        ),
    )
    p.set_defaults(func=run)


def _live_sessions() -> list[str]:
    """Labels of the leader sessions currently running (same data as ``fleet sessions``)."""
    labels = set(status_data_mod.load_session_records())
    labels |= set(status_data_mod.scan_inflight_tasks())
    return sorted(label for label in labels if status_data_mod.session_liveness(label, True))


def _report_session_not_running(m: mux.Mux, session: str) -> None:
    label = session[len("fleet-"):]
    print(f"error: {m.name} session not running: {session}", file=sys.stderr)
    live = _live_sessions()
    if live:
        print(f"  live sessions: {', '.join(live)}", file=sys.stderr)
    else:
        print("  live sessions: (none)", file=sys.stderr)
    print(f"  start it: fleet leader --name {label}", file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    if args.target == "leader":
        label = (
            getattr(args, "session", None)
            or os.environ.get("FLEET_SESSION")
            or DEFAULT_SESSION_LABEL
        )
        session = f"fleet-{label}"
    else:
        project_name = args.project if args.project != "." else None
        try:
            state_dir = task_context.resolve_project_state_dir(project_name=project_name)
        except task_context.ProjectNotFound as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        try:
            task = state_mod.load_task(state_dir, args.target)
        except FileNotFoundError:
            print(
                f"error: task not found: task-{args.target} (state: {state_dir})",
                file=sys.stderr,
            )
            return 1
        session = f"fleet-{state_mod.task_owner_session(task)}"

    m = mux.get()
    if not m.available():
        print(f"error: {m.name} not on PATH", file=sys.stderr)
        return 1

    if not m.session_exists(session):
        _report_session_not_running(m, session)
        return 1

    try:
        windows = m.list_windows(session)
    except mux.MuxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.target == "leader":
        window = "leader"
    else:
        matches = mux.matching_task_window_names(windows, args.target)
        if len(matches) == 1:
            window = matches[0]
        elif len(matches) > 1:
            print(
                f"error: multiple windows found for task: {args.target}\n"
                f"  matching: {', '.join(matches)}",
                file=sys.stderr,
            )
            return 1
        else:
            window = args.target
    if window not in windows:
        print(
            f"error: window not found: {session}:{window}\n"
            f"  existing: {', '.join(windows)}",
            file=sys.stderr,
        )
        return 1

    # The backend lands this client on ``window`` without moving other clients
    # attached to the same session (tmux: a grouped view session, Issue #76).
    try:
        return m.attach(session, window)
    except mux.MuxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
