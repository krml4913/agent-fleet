"""``fleet formation`` — list and inspect formations and template seeds."""
from __future__ import annotations

import argparse
import sys

import yaml

from .. import task_context
from .. import formation as formation_mod
from .. import state as state_mod
from ._project_arg import add_group_project_arg, add_sub_project_arg
from ._seed import add_seed_parser


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "formation",
        help="List formations and template seeds, or print one definition",
        description=(
            "List runtime formations plus shipped template seed sources, or print "
            "one runtime definition. Explicit names resolve project -> global."
        ),
    )
    add_group_project_arg(p)
    sp = p.add_subparsers(dest="formation_cmd", required=True, metavar="<sub>")

    p_list = sp.add_parser("list", help="List runtime formations and template seeds")
    add_sub_project_arg(p_list)
    p_list.set_defaults(func=run_list)

    p_show = sp.add_parser("show", help="Print a formation's YAML")
    p_show.add_argument("name", help="Formation name")
    add_sub_project_arg(p_show)
    p_show.set_defaults(func=run_show)

    add_seed_parser(sp, "formation")


def run_list(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    global_names = set(formation_mod.list_global())
    template_names = set(formation_mod.list_templates())

    print("formations and seed sources:")
    state_dir = None
    try:
        state_dir = task_context.resolve_project_state_dir(project_name=project_name)
        project_names = set(formation_mod.list_custom(state_dir))
        try:
            loaded = state_mod.load_project(state_dir)
            project_label = loaded.get("name") or state_dir.name
        except Exception:
            project_label = state_dir.name
    except task_context.ProjectNotFound as e:
        project_names = set()
        project_label = "unresolved"
        print(f"  (no project resolved — {e})")

    all_names = sorted(project_names | global_names | template_names)
    if not all_names:
        print("  (none)")
        return 0

    for name in all_names:
        tiers: list[tuple[str, bool]] = []
        if name in project_names:
            tiers.append((f"project:{project_label}", True))
        if name in global_names:
            tiers.append(("global", len(tiers) == 0))
        for tier, wins in tiers:
            suffix = " (wins)" if wins else " (shadowed)"
            print(f"  {name} [{tier}]{suffix}")
            if wins:
                problem = _validation_problem(name, tier == "global", state_dir)
                if problem:
                    print(f"    invalid: {problem}")
        if name in template_names:
            print(f"  {name} [seed:template]")
    return 0


def _validation_problem(name: str, is_global: bool, state_dir) -> str | None:
    """The validation error of the winning runtime formation ``name``, if any.

    Flags a bad agent spec or an unknown agent alias (``formation.validate``)
    in the listing instead of only at ``fleet-agent start``.
    """
    try:
        if is_global or state_dir is None:
            data = formation_mod.load_global(name)
        else:
            data = formation_mod.load_custom(state_dir, name)
        formation_mod.validate(data)
    except Exception as e:  # noqa: BLE001 — a listing never crashes
        return str(e)
    return None


def run_show(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    try:
        state_dir = task_context.resolve_project_state_dir(project_name=project_name)
    except task_context.ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        data = formation_mod.load_formation(args.name, state_dir)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        formation_mod.validate(data)
    except ValueError as e:
        print(f"warn: formation validation failed: {e}", file=sys.stderr)
    print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), end="")
    return 0
