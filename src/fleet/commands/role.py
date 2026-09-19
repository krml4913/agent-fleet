"""``fleet role`` — seed shipped role prompt fragments into a runtime tier."""
from __future__ import annotations

import argparse

from ._seed import add_seed_parser


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "role",
        help="Seed shipped role prompt fragments into the project or global tier",
        description=(
            "Roles resolve project -> global at runtime; shipped roles under "
            "docs/prompts/roles/ are seed sources only. `seed` copies one into a "
            "runtime tier."
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
    sp = p.add_subparsers(dest="role_cmd", required=True, metavar="<sub>")
    add_seed_parser(sp, "role")
