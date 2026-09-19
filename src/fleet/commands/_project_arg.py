"""Shared ``--project`` option for grouped commands (``formation`` / ``workspace`` / ``role``).

``--project`` is accepted both before and after the subcommand::

    fleet formation --project P show solo
    fleet formation show solo --project P

When both are given, the one after the subcommand wins.
"""
from __future__ import annotations

import argparse

GROUP_PROJECT_HELP = (
    "Project name (registry); required from a project-agnostic leader "
    "session, else resolved from FLEET_STATE_DIR / cwd"
)


def add_group_project_arg(group: argparse.ArgumentParser) -> None:
    """Add ``--project`` to a command group's own parser (owns the ``.`` default)."""
    group.add_argument("--project", default=".", help=GROUP_PROJECT_HELP)


def add_sub_project_arg(sub: argparse.ArgumentParser, help: str = GROUP_PROJECT_HELP) -> None:
    """Add ``--project`` to a subcommand's parser, after the group-level one.

    SUPPRESS: when the flag is absent the subparser leaves the namespace alone, so
    the group-level ``--project`` keeps working; a real default here would overwrite
    it. When present, the subcommand value overwrites the group-level one.
    """
    sub.add_argument("--project", default=argparse.SUPPRESS, help=help)
