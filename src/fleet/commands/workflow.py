"""``fleet workflow`` — list / show / set the active workflow plugin."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import plugins as plugins_mod
from .. import state as state_mod


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "workflow",
        help="Inspect or set the project's workflow plugin",
        description=(
            "List built-in / custom workflow plugins, show one's docstring, "
            "or set the active workflow in project.yaml."
        ),
    )
    p.add_argument(
        "--project",
        default=".",
        help="Project name (default: resolved from cwd)",
    )
    sp = p.add_subparsers(dest="workflow_cmd", required=True, metavar="<sub>")

    sp_list = sp.add_parser("list", help="List built-in and custom workflows")
    sp_list.set_defaults(func=run_list)

    sp_show = sp.add_parser("show", help="Show a workflow plugin's metadata")
    sp_show.add_argument("name", help="Workflow plugin name")
    sp_show.set_defaults(func=run_show)

    sp_set = sp.add_parser("set", help="Set the active workflow")
    sp_set.add_argument("name", help="Workflow plugin name")
    sp_set.set_defaults(func=run_set)


def _state_dir(project: str) -> Path | None:
    project_name = project if project != "." else None
    return state_mod.resolve_state_dir(Path.cwd(), project_name=project_name)


def run_list(args: argparse.Namespace) -> int:
    sd = _state_dir(args.project)
    print("built-in workflows:")
    for name in plugins_mod.list_builtin():
        print(f"  {name}")
    print()
    print("custom workflows:")
    if sd is None:
        print("  (no registered project found for cwd — run `fleet init` first)")
    else:
        custom = plugins_mod.list_custom(sd)
        if not custom:
            print(f"  (none under {sd / plugins_mod.PROJECT_PLUGIN_SUBDIR})")
        else:
            for name in custom:
                print(f"  {name}")
    print()
    if sd is not None:
        try:
            project = state_mod.load_project(sd)
        except FileNotFoundError:
            project = {}
        active = project.get("workflow") or plugins_mod.DEFAULT_WORKFLOW
        print(f"active workflow: {active}")
    return 0


def run_show(args: argparse.Namespace) -> int:
    sd = _state_dir(args.project)
    try:
        module = plugins_mod._resolve(args.name, sd or Path("/dev/null"))
    except (ImportError, ModuleNotFoundError) as e:
        print(f"error: workflow not found: {args.name} ({e})", file=sys.stderr)
        return 1
    name = getattr(module, "WORKFLOW_NAME", args.name)
    desc = getattr(module, "DESCRIPTION", "") or (module.__doc__ or "").strip()
    hooks = sorted(
        attr for attr in dir(module) if attr.startswith("on_")
    )
    print(f"workflow: {name}")
    print(f"  path:   {module.__file__}")
    print(f"  hooks:  {', '.join(hooks) if hooks else '(none)'}")
    print()
    print(desc)
    return 0


def run_set(args: argparse.Namespace) -> int:
    sd = _state_dir(args.project)
    if sd is None:
        print(
            f"error: no registered project found for {args.project!r}",
            file=sys.stderr,
        )
        return 1

    # Verify the plugin resolves before persisting the choice.
    try:
        plugins_mod._resolve(args.name, sd)
    except (ImportError, ModuleNotFoundError) as e:
        print(f"error: workflow not found: {args.name} ({e})", file=sys.stderr)
        return 1

    project = state_mod.load_project(sd)
    project["workflow"] = args.name
    state_mod.save_project(sd, project)
    print(f"workflow set to: {args.name}")
    return 0
