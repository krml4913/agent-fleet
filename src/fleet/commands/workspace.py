"""``fleet workspace`` — list / set the active workspace mode."""
from __future__ import annotations

import argparse
import sys

from .. import workspace as workspace_mod
from .. import state as state_mod
from .. import task_context


def add_parser(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser(
        "workspace",
        help="Inspect or set the project's workspace mode",
        description=(
            "Show available workspace modes and the active one, "
            "or set the active mode in project.yaml. "
            "Available: worktree, none."
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
    sp = p.add_subparsers(dest="workspace_cmd", required=True, metavar="<sub>")

    sp_list = sp.add_parser("list", help="Show available workspace modes and the active one")
    sp_list.set_defaults(func=run_list)

    sp_set = sp.add_parser("set", help="Set the active workspace mode")
    sp_set.add_argument("name", choices=workspace_mod.VALUES, help="worktree | none")
    sp_set.set_defaults(func=run_set)


def run_list(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    print("available workspace modes:")
    for v in workspace_mod.VALUES:
        print(f"  {v}")
    try:
        sd = task_context.resolve_project_state_dir(project_name=project_name)
        try:
            active = workspace_mod.load(sd)
        except FileNotFoundError:
            active = workspace_mod.DEFAULT
        print()
        print(f"active workspace: {active}")
    except task_context.ProjectNotFound:
        pass
    return 0


def run_set(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    try:
        sd = task_context.resolve_project_state_dir(project_name=project_name)
    except task_context.ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    project = state_mod.load_project(sd)
    project["workspace"] = args.name
    state_mod.save_project(sd, project)
    print(f"workspace set to: {args.name}")
    return 0
