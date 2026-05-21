"""``fleet rm <name>`` — remove a project from the registry and delete its state."""
from __future__ import annotations

import argparse
import shutil
import sys

from .. import tmux as tmux_mod
from ..state import load_registry, project_state_dir, unregister_project, list_tasks


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "rm",
        help="Remove a project from the registry and delete its state",
        description=(
            "Unregisters <name> from projects.yaml and deletes "
            "fleet-state/projects/<name>/. Active tmux sessions are not killed "
            "automatically; a warning is shown when one is detected."
        ),
    )
    p.add_argument("name", help="Project name to remove")
    p.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt (required in non-TTY environments)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    reg = load_registry()
    if args.name not in reg.get("projects", {}):
        print(
            f"error: project {args.name!r} not found in registry",
            file=sys.stderr,
        )
        return 1

    state_dir = project_state_dir(args.name)
    session = f"fleet-{args.name}"

    # Warn about live tmux session.
    if tmux_mod.available() and tmux_mod.session_exists(session):
        print(
            f"warn: tmux session {session!r} is still running. "
            f"Kill it first:  tmux kill-session -t {session}",
            file=sys.stderr,
        )

    # Warn about non-completed tasks.
    if state_dir.is_dir():
        try:
            tasks = list_tasks(state_dir)
            active = [t for t in tasks if t.get("status") not in ("completed", "done")]
            if active:
                print(
                    f"warn: {len(active)} task(s) still active in {args.name!r}",
                    file=sys.stderr,
                )
        except Exception:
            pass

    # Confirm.
    if not args.yes:
        if not sys.stdin.isatty():
            print(
                "error: stdin is not a TTY; pass --yes to confirm deletion",
                file=sys.stderr,
            )
            return 1
        answer = input(
            f"Remove project {args.name!r} and delete {state_dir}? [y/N] "
        ).strip().lower()
        if answer not in ("y", "yes"):
            print("aborted.")
            return 1

    # Deregister.
    try:
        unregister_project(args.name)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Delete state tree.
    if state_dir.is_dir():
        shutil.rmtree(state_dir)
        print(f"deleted: {state_dir}")
    else:
        print(f"note: state dir not found (already deleted?): {state_dir}")

    print(f"project {args.name!r} removed.")
    return 0
