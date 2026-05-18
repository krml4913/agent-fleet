"""``fleet init`` — initialize a project's ``.fleet-state/`` directory."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..state import init_state


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "init",
        help="Initialize .fleet-state/ for a project",
        description=(
            "Create .fleet-state/ inside the given project directory and "
            "record the project name."
        ),
    )
    p.add_argument(
        "--name",
        required=True,
        help="Project name (used for the tmux session: fleet-<name>)",
    )
    p.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Project root path (default: current directory)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    project_path = Path(args.path).resolve()
    if not project_path.is_dir():
        print(f"error: not a directory: {project_path}", file=sys.stderr)
        return 1

    state_dir = project_path / ".fleet-state"
    if state_dir.exists():
        print(f"error: already initialized: {state_dir}", file=sys.stderr)
        return 1

    init_state(state_dir, name=args.name)
    print(f"Initialized fleet state at: {state_dir}")
    print(f"  project name: {args.name}")
    print(f"  tmux session: fleet-{args.name}")
    return 0
