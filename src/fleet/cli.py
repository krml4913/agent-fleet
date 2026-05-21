"""Top-level CLI dispatcher.

Each subcommand lives in ``src/fleet/commands/<name>.py`` and exposes an
``add_parser(subparsers)`` function plus a ``run(args) -> int`` entry point.

Two entrypoints:
  fleet        — user-facing (init / preflight / leader / attach / status /
                 log / topology / workflow)
  fleet-agent  — agent-facing (start / inbox / inbox-read / send-prompt /
                 cleanup / ask / event / done)
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .commands import ask as ask_cmd
from .commands import attach as attach_cmd
from .commands import cleanup as cleanup_cmd
from .commands import done as done_cmd
from .commands import event as event_cmd
from .commands import inbox as inbox_cmd
from .commands import inbox_read as inbox_read_cmd
from .commands import init as init_cmd
from .commands import leader as leader_cmd
from .commands import log as log_cmd
from .commands import preflight as preflight_cmd
from .commands import rm as rm_cmd
from .commands import send_prompt as send_prompt_cmd
from .commands import start as start_cmd
from .commands import status as status_cmd
from .commands import topology as topology_cmd
from .commands import workflow as workflow_cmd


def build_parser_user() -> argparse.ArgumentParser:
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
    preflight_cmd.add_parser(sub)
    leader_cmd.add_parser(sub)
    attach_cmd.add_parser(sub)
    status_cmd.add_parser(sub)
    log_cmd.add_parser(sub)
    topology_cmd.add_parser(sub)
    workflow_cmd.add_parser(sub)
    rm_cmd.add_parser(sub)
    return parser


def build_parser_agent() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet-agent",
        description=(
            "agent-fleet internal CLI — for leader / driver agents. "
            "Not intended for direct human use."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"fleet-agent {__version__}",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")
    start_cmd.add_parser(sub)
    inbox_cmd.add_parser(sub)
    inbox_read_cmd.add_parser(sub)
    send_prompt_cmd.add_parser(sub)
    cleanup_cmd.add_parser(sub)
    ask_cmd.add_parser(sub)
    event_cmd.add_parser(sub)
    done_cmd.add_parser(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser_user()
    args = parser.parse_args(argv)
    rc = args.func(args)
    return rc if isinstance(rc, int) else 0


def main_agent(argv: Sequence[str] | None = None) -> int:
    parser = build_parser_agent()
    args = parser.parse_args(argv)
    rc = args.func(args)
    return rc if isinstance(rc, int) else 0
