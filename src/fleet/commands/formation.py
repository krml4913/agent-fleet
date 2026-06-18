"""``fleet formation`` — list and inspect team formations and templates."""
from __future__ import annotations

import argparse
import shutil
import sys

import yaml

from .. import task_context
from .. import formation as formation_mod


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "formation",
        help="List formations and templates, or print one definition",
        description=(
            "List custom formations and templates, or print one definition. "
            "Custom formations live in <state>/formations/<name>.yaml. "
            "Templates ship with fleet (src/fleet/templates/) and are only used "
            "as the source for `fleet formation init --from`."
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
    sp = p.add_subparsers(dest="formation_cmd", required=True, metavar="<sub>")

    p_list = sp.add_parser("list", help="List template + custom formations")
    p_list.set_defaults(func=run_list)

    p_show = sp.add_parser("show", help="Print a formation's YAML")
    p_show.add_argument("name", help="Formation name (custom only)")
    p_show.set_defaults(func=run_show)

    p_init = sp.add_parser(
        "init",
        help="Copy a formation template into <state>/formations/",
    )
    p_init.add_argument(
        "--from",
        dest="template",
        required=True,
        metavar="TEMPLATE",
        help="Template name to copy from (solo, pair_review, multi_stage)",
    )
    p_init.add_argument(
        "--name",
        default=None,
        metavar="NAME",
        help="Name for the new formation (default: same as template)",
    )
    p_init.set_defaults(func=run_init)


def run_list(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    templates = formation_mod.list_templates()
    print("template formations:")
    if not templates:
        print("  (none — bug? src/fleet/templates/ is empty)")
    else:
        for name in templates:
            print(f"  {name}")
    print()
    print("custom formations:")
    try:
        state_dir = task_context.resolve_project_state_dir(project_name=project_name)
        custom = formation_mod.list_custom(state_dir)
        if not custom:
            print(f"  (none under {state_dir / 'formations'})")
        else:
            for name in custom:
                print(f"  {name}")
    except task_context.ProjectNotFound as e:
        print(f"  (no project resolved — {e})")
    return 0


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


def run_init(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    try:
        state_dir = task_context.resolve_project_state_dir(project_name=project_name)
    except task_context.ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    template_name = args.template
    formation_name = args.name or template_name

    src = formation_mod.TEMPLATES_DIR / f"{template_name}.yaml"
    if not src.is_file():
        available = ", ".join(formation_mod.list_templates())
        print(
            f"error: unknown template: {template_name}. Available: {available}",
            file=sys.stderr,
        )
        return 1

    formations_dir = state_dir / "formations"
    formations_dir.mkdir(parents=True, exist_ok=True)
    dst = formations_dir / f"{formation_name}.yaml"
    if dst.exists():
        print(
            f"error: {dst} already exists. Delete it first if you want to recreate.",
            file=sys.stderr,
        )
        return 1

    shutil.copyfile(src, dst)
    print(f"Created formation: {dst}")
    print(f"  source template: {template_name}")
    print("Edit it to customize agents / stages.")
    return 0
