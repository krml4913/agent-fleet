"""``fleet formation`` — list and inspect team formation definitions."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .. import state as state_mod
from .. import formation as formation_mod


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "formation",
        help="Inspect available team formations",
        description=(
            "List preset + custom formations, or print one definition. "
            "Custom formations live in <state>/formations/<name>.yaml."
        ),
    )
    p.add_argument(
        "--project",
        default=".",
        help="Project name used to resolve custom formations (default: resolved from cwd)",
    )
    sp = p.add_subparsers(dest="formation_cmd", required=True, metavar="<sub>")

    p_list = sp.add_parser("list", help="List preset + custom formations")
    p_list.set_defaults(func=run_list)

    p_show = sp.add_parser("show", help="Print a formation's YAML")
    p_show.add_argument("name", help="Formation name (preset or custom)")
    p_show.set_defaults(func=run_show)


def _state_dir(project: str) -> Path | None:
    project_name = project if project != "." else None
    return state_mod.resolve_state_dir(Path.cwd(), project_name=project_name)


def run_list(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args.project)
    presets = formation_mod.list_presets()
    print("preset formations:")
    if not presets:
        print("  (none — bug? src/fleet/presets/ is empty)")
    else:
        for name in presets:
            print(f"  {name}")
    print()
    print("custom formations:")
    if state_dir is None:
        print("  (no registered project found for cwd — run `fleet init` first)")
    else:
        custom = formation_mod.list_custom(state_dir)
        if not custom:
            print(f"  (none under {state_dir / 'formations'})")
        else:
            for name in custom:
                print(f"  {name}")
    return 0


def run_show(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args.project)
    try:
        data = formation_mod.load(args.name, state_dir=state_dir)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        formation_mod.validate(data)
    except ValueError as e:
        print(f"warn: formation validation failed: {e}", file=sys.stderr)
    print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), end="")
    return 0
