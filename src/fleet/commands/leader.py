"""``fleet leader`` — launch the leader pane for a project.

Creates a detached tmux session named ``fleet-<project>``, opens a
single ``leader`` window in the project root, and starts the chosen
agent CLI inside. If the session already exists, prints the attach
command and exits (the leader is single-instance per project).

This is the entry point described in design doc §3 — the leader is the
user's conversational counterpart; per §4.1 it only does dialogue and
``fleet spawn``, never state polling.
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

from .. import agents as agents_mod
from .. import state as state_mod
from .. import tmux as tmux_mod
from ..events import append_event


DEFAULT_LEADER_AGENT = "claude:opus"


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "leader",
        help="Launch the leader pane for this project",
        description=(
            "Create a tmux session 'fleet-<project>' with a single leader "
            "window running the chosen agent CLI. Single-instance per "
            "project: if the session already exists, prints the attach "
            "command and exits."
        ),
    )
    p.add_argument(
        "--project",
        default=".",
        help="Project path (default: cwd)",
    )
    p.add_argument(
        "--agent",
        default=DEFAULT_LEADER_AGENT,
        help=f"Agent spec for the leader pane (default: {DEFAULT_LEADER_AGENT})",
    )
    p.add_argument(
        "--attach",
        action="store_true",
        help="After starting, exec `tmux attach -t <session>` (foreground).",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    state_dir = state_mod.discover_state_dir(Path(args.project))
    if state_dir is None:
        print(
            f"error: no .fleet-state/ found under {Path(args.project).resolve()}",
            file=sys.stderr,
        )
        return 1

    try:
        project = state_mod.load_project(state_dir)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    name = project.get("name") or "fleet"
    session = f"fleet-{name}"

    if not tmux_mod.available():
        print("error: tmux not on PATH", file=sys.stderr)
        return 1

    if tmux_mod.session_exists(session):
        print(f"leader session already exists: {session}")
        print(f"  attach: tmux attach -t {session}")
        if args.attach:
            os.execvp("tmux", ["tmux", "attach", "-t", session])
        return 0

    try:
        agents_mod.parse_spec(args.agent)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    cli = agents_mod.cli_command(args.agent)
    cli_quoted = " ".join(shlex.quote(p) for p in cli)

    project_root = state_dir.parent

    try:
        tmux_mod.new_session(
            session,
            window_name="leader",
            cwd=str(project_root),
            env={
                "FLEET_PROJECT": name,
                "FLEET_STATE_DIR": str(state_dir),
            },
        )
        tmux_mod.send_keys(session, "leader", cli_quoted)
    except tmux_mod.TmuxError as e:
        print(f"error: tmux setup failed: {e}", file=sys.stderr)
        return 1

    append_event(
        state_dir / "events.jsonl",
        "leader_start",
        agent=args.agent,
        session=session,
    )

    print(f"leader started: session={session}, agent={args.agent}")
    print(f"  attach: tmux attach -t {session}")

    if args.attach:
        os.execvp("tmux", ["tmux", "attach", "-t", session])
    return 0
