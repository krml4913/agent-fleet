"""Top-level CLI dispatcher.

Each subcommand lives in ``src/fleet/commands/<name>.py`` and exposes an
``add_parser(subparsers)`` function plus a ``run(args) -> int`` entry point.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .commands import ask as ask_cmd
from .commands import done as done_cmd
from .commands import event as event_cmd
from .commands import init as init_cmd
from .commands import spawn as spawn_cmd
from .commands import status as status_cmd
from .commands import topology as topology_cmd
from .commands import workflow as workflow_cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet",
        description="Hierarchical multi-vendor agent orchestration over tmux.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"fleet {__version__}",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")
    init_cmd.add_parser(sub)
    spawn_cmd.add_parser(sub)
    status_cmd.add_parser(sub)
    topology_cmd.add_parser(sub)
    workflow_cmd.add_parser(sub)
    ask_cmd.add_parser(sub)
    event_cmd.add_parser(sub)
    done_cmd.add_parser(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    rc = args.func(args)
    return rc if isinstance(rc, int) else 0
