"""Top-level CLI dispatcher.

Each subcommand lives in ``src/fleet/commands/<name>.py`` and exposes an
``add_parser(subparsers)`` function plus a ``run(args) -> int`` entry point.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .commands import init as init_cmd


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    rc = args.func(args)
    return rc if isinstance(rc, int) else 0
