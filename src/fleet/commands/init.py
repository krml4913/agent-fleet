"""``fleet init`` — initialize a project in the central fleet-state registry."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..state import fleet_home, init_state, project_state_dir, register_project


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "init",
        help="Register a project and create its fleet-state directory",
        description=(
            "Register the project repo in fleet-state/projects.yaml and "
            "create fleet-state/projects/<name>/ with the initial state layout. "
            "If --name is omitted the repo directory basename is used."
        ),
    )
    p.add_argument(
        "--name",
        default=None,
        help="Project name (default: basename of path). Must be unique.",
    )
    p.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Project root path (default: current directory)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    repo = Path(args.path).resolve()
    if not repo.is_dir():
        print(f"error: not a directory: {repo}", file=sys.stderr)
        return 1

    name = args.name if args.name else repo.name
    state_dir = project_state_dir(name)

    if state_dir.exists():
        print(f"error: already registered: {name}", file=sys.stderr)
        return 1

    try:
        register_project(name, repo)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        init_state(state_dir, name=name, repo=repo)
    except Exception as e:
        # Roll back registry entry if state creation fails.
        try:
            from ..state import unregister_project
            unregister_project(name)
        except Exception:
            pass
        print(f"error: failed to create state directory: {e}", file=sys.stderr)
        return 1

    print(f"Initialized fleet state:")
    print(f"  project name: {name}")
    print(f"  repo:         {repo}")
    print(f"  state dir:    {state_dir}")
    print(f"  tmux session: fleet-{name}")
    return 0
