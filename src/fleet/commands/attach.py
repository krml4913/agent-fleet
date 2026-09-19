"""``fleet attach [<target>]`` — attach to ``fleet-<name>`` on the leader or a task window."""
from __future__ import annotations

import argparse
import sys

from .. import state as state_mod
from .. import task_context
from .. import mux


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "attach",
        help="Attach to the leader pane or a task driver pane",
        description=(
            "Attach to the multiplexer session fleet-<project> on the given window "
            "(tmux window / zellij tab; on tmux this is like "
            "`tmux attach -t fleet-<project>:<window>`). "
            "Target is either 'leader' (default) or a task id."
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
            "Project name (registry); required from a project-agnostic leader "
            "session, else resolved from FLEET_STATE_DIR / cwd"
        ),
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    try:
        state_dir = task_context.resolve_project_state_dir(project_name=project_name)
    except task_context.ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    m = mux.get()
    if not m.available():
        print(f"error: {m.name} not on PATH", file=sys.stderr)
        return 1

    project = state_mod.load_project(state_dir)
    name = project.get("name") or "fleet"
    session = f"fleet-{name}"

    if not m.session_exists(session):
        print(f"error: {m.name} session not running: {session}", file=sys.stderr)
        print(f"  start it: fleet leader --project {args.project or name}", file=sys.stderr)
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
