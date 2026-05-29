"""``fleet workspace`` — list / set the active workspace mode."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import workspace as workspace_mod
from .. import state as state_mod


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
    p.add_argument("--project", default=".", help="Project name (default: cwd)")
    sp = p.add_subparsers(dest="workspace_cmd", required=True, metavar="<sub>")

    sp_list = sp.add_parser("list", help="Show available workspace modes and the active one")
    sp_list.set_defaults(func=run_list)

    sp_set = sp.add_parser("set", help="Set the active workspace mode")
    sp_set.add_argument("name", choices=workspace_mod.VALUES, help="worktree | none")
    sp_set.set_defaults(func=run_set)


def _state_dir(project: str) -> "Path | None":
    project_name = project if project != "." else None
    return state_mod.resolve_state_dir(Path.cwd(), project_name=project_name)


def run_list(args: argparse.Namespace) -> int:
    sd = _state_dir(args.project)
    print("available workspace modes:")
    for v in workspace_mod.VALUES:
        print(f"  {v}")
    if sd is not None:
        try:
            active = workspace_mod.load(sd)
        except FileNotFoundError:
            active = workspace_mod.DEFAULT
        print()
        print(f"active workspace: {active}")
    return 0


def run_set(args: argparse.Namespace) -> int:
    sd = _state_dir(args.project)
    if sd is None:
        print(f"error: no registered project for {args.project!r}", file=sys.stderr)
        return 1
    project = state_mod.load_project(sd)
    project["workspace"] = args.name
    state_mod.save_project(sd, project)
    print(f"workspace set to: {args.name}")
    return 0
