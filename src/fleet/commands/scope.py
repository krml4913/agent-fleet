"""``fleet scope`` — view and edit a leader session's project scope.

A session's scope is the set of projects it is responsible for. Projects not
in scope are filtered from ``fleet status --all`` and blocked from dispatch via
``fleet-agent start`` (pass ``--allow-out-of-scope`` to override).

Scope is stored as the optional ``scope`` field in
``global/sessions/<label>/session.json``. A missing field / missing record
means unscoped — the session serves all registered projects.
"""
from __future__ import annotations

import argparse
import os
import sys

from .. import state as state_mod


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "scope",
        help="View or edit a leader session's project scope",
        description=(
            "Show or mutate the set of projects a leader session is responsible for. "
            "Projects outside the scope are filtered from `fleet status --all` and "
            "blocked from dispatch. With no mutation flag, prints the current scope. "
            "Label defaults to FLEET_SESSION env, then 'main'."
        ),
    )
    p.add_argument(
        "label",
        nargs="?",
        default=None,
        metavar="LABEL",
        help=(
            "Session label to view/edit. Defaults to $FLEET_SESSION, then 'main'."
        ),
    )
    mut = p.add_mutually_exclusive_group()
    mut.add_argument(
        "--set",
        metavar="PROJECTS",
        dest="set_names",
        default=None,
        help="Replace the scope exactly with this comma-separated project list.",
    )
    mut.add_argument(
        "--add",
        metavar="PROJECTS",
        dest="add_names",
        default=None,
        help="Add comma-separated project names to the scope.",
    )
    mut.add_argument(
        "--rm",
        metavar="PROJECTS",
        dest="rm_names",
        default=None,
        help="Remove comma-separated project names from the scope.",
    )
    mut.add_argument(
        "--clear",
        action="store_true",
        dest="clear",
        help="Remove the scope field entirely (session becomes unscoped).",
    )
    p.set_defaults(func=run)


def _resolve_label(args: argparse.Namespace) -> str:
    if getattr(args, "label", None):
        return args.label
    return os.environ.get("FLEET_SESSION") or "main"


def _parse_names(value: str) -> list[str]:
    return [n.strip() for n in value.split(",") if n.strip()]


def _require_names(names: list[str], flag: str) -> None:
    """Reject an empty / whitespace-only project list for a mutation flag.

    ``--add ""`` (and friends) parse to ``[]``; without this they would be a
    silent no-op. Make the intent explicit — a mutation flag carrying no real
    name is an error, not a quiet skip.
    """
    if not names:
        raise ValueError(f"{flag}: no project name given (got an empty value)")


def run(args: argparse.Namespace) -> int:
    label = _resolve_label(args)

    set_names = getattr(args, "set_names", None)
    add_names = getattr(args, "add_names", None)
    rm_names = getattr(args, "rm_names", None)
    clear = getattr(args, "clear", False)

    # Read-only mode: no mutation flag was given at all. Test flag *presence*
    # (`is not None` / the `clear` bool), not truthiness — `--add ""` is present
    # but falsy and must not silently fall through to the display path.
    if set_names is None and add_names is None and rm_names is None and not clear:
        scope = state_mod.session_scope(label)
        if scope is None:
            print(f"{label}: (unscoped — all projects)")
        else:
            print(f"{label}: {', '.join(scope)}")
        return 0

    # Mutate
    try:
        if clear:
            state_mod.set_session_scope(label, None, mode="clear")
            print(f"{label}: scope cleared (unscoped — all projects)")
        elif set_names is not None:
            names = _parse_names(set_names)
            _require_names(names, "--set")
            new = state_mod.set_session_scope(label, names, mode="set")
            print(f"{label}: scope set to {', '.join(new or [])}")
        elif add_names is not None:
            names = _parse_names(add_names)
            _require_names(names, "--add")
            new = state_mod.set_session_scope(label, names, mode="add")
            print(f"{label}: scope is now {', '.join(new or [])}")
        elif rm_names is not None:
            names = _parse_names(rm_names)
            _require_names(names, "--rm")
            new = state_mod.set_session_scope(label, names, mode="rm")
            if new:
                print(f"{label}: scope is now {', '.join(new)}")
            else:
                print(f"{label}: scope cleared (unscoped — all projects)")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    return 0
