"""``fleet init`` — initialize a project in the central fleet-state registry."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from ..state import (
    ensure_global_leader_memory,
    init_state,
    project_state_dir,
    register_project,
)
from .. import formation as formation_mod


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "init",
        help="Register a project and create its fleet-state directory",
        description=(
            "Register the project repo in fleet-state/projects.yaml and "
            "create fleet-state/projects/<name>/ with the initial state layout. "
            "If --name is omitted the repo directory basename is used."
        ),
    )
    p.add_argument(
        "--name",
        default=None,
        help="Project name (default: basename of path). Must be unique.",
    )
    p.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Project root path (default: current directory)",
    )
    p.add_argument(
        "--formation",
        action="append",
        dest="formations",
        metavar="NAME",
        help=(
            "Formation template(s) to copy into <state>/formations/ as project overrides. "
            "Repeat or use comma-separated names. "
            "Available: solo, pair_review, multi_stage."
        ),
    )
    p.add_argument(
        "--no-formation",
        action="store_true",
        help="Create an empty formations/ directory (the default).",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Accepted for compatibility; init no longer prompts by default.",
    )
    p.set_defaults(func=run)


def _flatten_formations(raw: list[str]) -> list[str]:
    """Flatten comma-separated formation names and deduplicate."""
    seen: list[str] = []
    for item in raw:
        for name in item.split(","):
            name = name.strip()
            if name and name not in seen:
                seen.append(name)
    return seen


def _interactive_pick() -> list[str] | None:
    """Run the interactive formation template picker.

    Returns a list of selected template names, or None if aborted.
    """
    templates = formation_mod.list_templates()
    descriptions: dict[str, str] = {}
    for name in templates:
        try:
            data = formation_mod.load_template(name)
            descriptions[name] = data.get("description", "")
        except Exception:
            descriptions[name] = ""

    print()
    print("Available formation templates (fleet ships these as starting points):")
    for i, name in enumerate(templates, 1):
        desc = descriptions.get(name, "")
        print(f"  {i}) {name:<14} - {desc}")
    print()
    print("Which to copy into <state>/formations/?")
    print("  Enter numbers (1,2,3), names (solo,pair_review), 'all', or 'none'.")
    print("  Default: all")
    print()

    for attempt in range(3):
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nerror: aborted by user", file=sys.stderr)
            return None

        if not raw:
            raw = "all"

        if raw.lower() in ("none", "no", "0"):
            return []

        if raw.lower() == "all":
            return list(templates)

        # Parse numbers / names
        parts = [p.strip() for p in raw.replace(",", " ").split()]
        selected: list[str] = []
        unknown: list[str] = []
        for part in parts:
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(templates):
                    name = templates[idx]
                    if name not in selected:
                        selected.append(name)
                else:
                    unknown.append(part)
            elif part in templates:
                if part not in selected:
                    selected.append(part)
            else:
                unknown.append(part)

        if unknown:
            remaining = 2 - attempt
            if remaining > 0:
                print(f"  unknown: {', '.join(unknown)}. Try again ({remaining} left).")
            else:
                print(f"  unknown: {', '.join(unknown)}. No retries left.")
            continue

        break
    else:
        print("error: too many invalid attempts, aborting", file=sys.stderr)
        return None

    if not selected:
        return []

    names_str = ", ".join(selected)
    print()
    print(f"Selected: {names_str}")
    try:
        ans = input("Copy these to <state>/formations/? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nerror: aborted by user", file=sys.stderr)
        return None

    if ans in ("n", "no"):
        print("error: aborted by user", file=sys.stderr)
        return None

    return selected


def run(args: argparse.Namespace) -> int:
    repo = Path(args.path).resolve()
    if not repo.is_dir():
        print(f"error: not a directory: {repo}", file=sys.stderr)
        return 1

    # Validate --formation / --no-formation
    if args.no_formation and args.formations:
        print("error: --formation and --no-formation are mutually exclusive", file=sys.stderr)
        return 1

    name = args.name if args.name else repo.name
    state_dir = project_state_dir(name)

    if state_dir.exists():
        print(f"error: already registered: {name}", file=sys.stderr)
        return 1

    # Resolve formation selection before doing any state changes
    if args.no_formation:
        selected_formations: list[str] = []
        interactive = False
    elif args.formations:
        # Validate template names upfront
        flat = _flatten_formations(args.formations)
        available = formation_mod.list_templates()
        unknown = [f for f in flat if f not in available]
        if unknown:
            print(
                f"error: unknown formation template(s): {', '.join(unknown)}. "
                f"Available: {', '.join(available)}",
                file=sys.stderr,
            )
            return 1
        selected_formations = flat
        interactive = False
    else:
        selected_formations = []
        interactive = False

    try:
        register_project(name, repo)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        init_state(state_dir, name=name, repo=repo)
    except Exception as e:
        try:
            from ..state import unregister_project
            unregister_project(name)
        except Exception:
            pass
        print(f"error: failed to create state directory: {e}", file=sys.stderr)
        return 1

    # Scaffold the GLOBAL tier of two-tier leader memory (design §6). It is
    # project-independent (lives under fleet-state/global/, the reserved
    # cross-project namespace, §5.3), so it is wired here at the project-agnostic
    # `fleet init` entrypoint rather than off any one project's state — `fleet
    # leader` does not exist until Issue #166 Phase 2. Idempotent: never clobbers
    # existing content, so initializing further projects re-affirms it harmlessly.
    ensure_global_leader_memory()

    # Ensure formations/ exists
    formations_dir = state_dir / "formations"
    formations_dir.mkdir(parents=True, exist_ok=True)

    # Interactive mode: run after state is created
    if interactive:
        print(f"Initializing project '{name}' at {repo} ...")
        print(f"Registered. State dir: {state_dir}")
        picked = _interactive_pick()
        if picked is None:
            # Aborted — project is already registered; keep it but warn
            return 1
        selected_formations = picked
        # Copy selected formations
        copied: list[str] = []
        for tname in selected_formations:
            src = formation_mod.TEMPLATES_DIR / f"{tname}.yaml"
            dst = formations_dir / f"{tname}.yaml"
            shutil.copyfile(src, dst)
            copied.append(f"  formations/{tname}.yaml")
        if copied:
            print()
            print("Copied:")
            for line in copied:
                print(line)
            print()
            print("Done. Edit them to customize agents/stages.")
        else:
            print()
            print("Done. formations/ is empty.")
        return 0

    # Non-interactive path: copy formations, print summary
    copied_names: list[str] = []
    for tname in selected_formations:
        src = formation_mod.TEMPLATES_DIR / f"{tname}.yaml"
        dst = formations_dir / f"{tname}.yaml"
        shutil.copyfile(src, dst)
        copied_names.append(tname)

    formations_summary = ", ".join(copied_names) if copied_names else "(none)"

    print("Initialized fleet state:")
    print(f"  project name: {name}")
    print(f"  repo:         {repo}")
    print(f"  state dir:    {state_dir}")
    print(f"  tmux session: fleet-{name}")
    print(f"  formations copied: {formations_summary}")
    return 0
