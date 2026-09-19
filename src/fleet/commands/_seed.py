"""Shared ``seed`` subcommand behind ``fleet formation seed`` / ``fleet role seed``."""
from __future__ import annotations

import argparse
import sys

from .. import seeds
from .. import task_context
from ._project_arg import add_sub_project_arg


def add_seed_parser(sub: argparse._SubParsersAction, kind: str) -> None:
    p = sub.add_parser(
        "seed",
        help=f"Copy a shipped {kind} seed into the project (or global) tier",
        description=(
            f"Copy a shipped {kind} seed into the project tier (default) or, with "
            f"--global, the global tier. Refuses to overwrite an existing file "
            f"unless --force. Shipped seeds are never read at runtime; a missing "
            f"{kind} is a hard error until seeded."
        ),
    )
    p.add_argument("name", help=f"Shipped {kind} seed name")
    p.add_argument(
        "--global",
        dest="use_global",
        action="store_true",
        help="Seed into the global tier instead of the project tier",
    )
    add_sub_project_arg(p, help="Project name (registry); ignored with --global")
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the target file if it already exists",
    )
    p.set_defaults(func=lambda args: run_seed(kind, args))


def run_seed(kind: str, args: argparse.Namespace) -> int:
    state_dir = None
    if not args.use_global:
        project_name = args.project if args.project != "." else None
        try:
            state_dir = task_context.resolve_project_state_dir(project_name=project_name)
        except task_context.ProjectNotFound as e:
            print(
                f"error: {e}\n"
                f"hint: pass --project <name> for the project tier, or --global "
                f"for the global tier.",
                file=sys.stderr,
            )
            return 1
    try:
        target = seeds.seed(kind, args.name, state_dir=state_dir, force=args.force)
    except seeds.SeedError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    tier = "global" if state_dir is None else f"project:{seeds.project_label(state_dir)}"
    print(f"seeded {kind} {args.name!r} [{tier}]: {target}")
    return 0
