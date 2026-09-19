"""``fleet notify`` — show / enable / disable the opt-in leader-pane push.

The push (design §10.3) is gated on ``notify_leader_on_driver_done`` in
``project.yaml``. This is the only supported way to flip it: the leader
protocol forbids hand-editing state files.
"""
from __future__ import annotations

import argparse
import sys

from .. import state as state_mod
from .. import task_context
from .done import _truthy

KEY = "notify_leader_on_driver_done"
STATES = ("on", "off", "status")


def add_parser(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser(
        "notify",
        help="Show or set the project's leader-pane push (notify_leader_on_driver_done)",
        description=(
            "Show or set the opt-in leader-pane push. When on, a driver's "
            "done / approval gate is injected into the owning leader's pane "
            f"({KEY} in project.yaml). Default: off. "
            "With no argument (or 'status'), print the current state."
        ),
    )
    p.add_argument(
        "--project",
        default=".",
        help=(
            "Project name (registry); required from a project-agnostic leader "
            "session, else resolved from FLEET_STATE_DIR / cwd"
        ),
    )
    p.add_argument(
        "state",
        nargs="?",
        choices=STATES,
        default="status",
        help="on | off | status (default: status)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    try:
        sd = task_context.resolve_project_state_dir(project_name=project_name)
    except task_context.ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    project = state_mod.load_project(sd)

    if args.state == "status":
        print(f"notify_leader_on_driver_done: {_label(project)}")
        return 0

    project[KEY] = "true" if args.state == "on" else "false"
    state_mod.save_project(sd, project)
    print(f"notify_leader_on_driver_done: {args.state}")
    return 0


def _label(project: dict) -> str:
    return "on" if _truthy(project.get(KEY)) else "off"
