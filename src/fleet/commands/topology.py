"""``fleet topology`` — list and inspect team topology definitions."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .. import state as state_mod
from .. import topology as topology_mod


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "topology",
        help="Inspect available team topologies",
        description=(
            "List preset + custom topologies, or print one definition. "
            "Custom topologies live in <state>/topologies/<name>.yaml."
        ),
    )
    p.add_argument(
        "--project",
        default=".",
        help="Project name used to resolve custom topologies (default: resolved from cwd)",
    )
    sp = p.add_subparsers(dest="topology_cmd", required=True, metavar="<sub>")

    p_list = sp.add_parser("list", help="List preset + custom topologies")
    p_list.set_defaults(func=run_list)

    p_show = sp.add_parser("show", help="Print a topology's YAML")
    p_show.add_argument("name", help="Topology name (preset or custom)")
    p_show.set_defaults(func=run_show)


def _state_dir(project: str) -> Path | None:
    project_name = project if project != "." else None
    return state_mod.resolve_state_dir(Path.cwd(), project_name=project_name)


def run_list(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args.project)
    presets = topology_mod.list_presets()
    print("preset topologies:")
    if not presets:
        print("  (none — bug? src/fleet/presets/ is empty)")
    else:
        for name in presets:
            print(f"  {name}")
    print()
    print("custom topologies:")
    if state_dir is None:
        print("  (no registered project found for cwd — run `fleet init` first)")
    else:
        custom = topology_mod.list_custom(state_dir)
        if not custom:
            print(f"  (none under {state_dir / 'topologies'})")
        else:
            for name in custom:
                print(f"  {name}")
    return 0


def run_show(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args.project)
    try:
        data = topology_mod.load(args.name, state_dir=state_dir)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        topology_mod.validate(data)
    except ValueError as e:
        print(f"warn: topology validation failed: {e}", file=sys.stderr)
    print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), end="")
    return 0
