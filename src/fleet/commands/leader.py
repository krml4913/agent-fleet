"""``fleet leader`` — launch the leader pane for a project.

Creates a detached tmux session named ``fleet-<project>``, opens a
single ``leader`` window in the project root, and starts the chosen
agent CLI inside. If the session already exists, prints the attach
command and exits (the leader is single-instance per project).

This is the entry point described in design doc §3 — the leader is the
user's conversational counterpart; per §4.1 it only does dialogue and
``fleet-agent start``, never state polling.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import agents as agents_mod
from .. import leader_prompt as lp
from .. import prompt_pointer
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
        help="Project name (default: resolved from cwd via registry)",
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
    p.add_argument(
        "--no-auto-paste",
        dest="auto_paste",
        action="store_false",
        help="Skip pasting the leader prompt into the pane.",
    )
    p.add_argument(
        "--prompt-delay",
        type=float,
        default=3.0,
        help="Seconds to wait for the agent CLI to start before pasting (default: 3.0).",
    )
    p.set_defaults(func=run, auto_paste=True)


def run(args: argparse.Namespace) -> int:
    state_dir = state_mod.resolve_state_dir(Path.cwd(), project_name=args.project if args.project != "." else None)
    if state_dir is None:
        print(
            f"error: no registered project found for {args.project!r}",
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

    # Session display name so the leader is identifiable in the session picker.
    session_name = f"{name}-leader"

    cli = agents_mod.cli_command(args.agent)
    cli = cli + agents_mod.session_name_launch_args(args.agent, session_name)
    cli_quoted = " ".join(shlex.quote(p) for p in cli)

    project_root = Path(project.get("repo", str(state_dir.parent)))

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

    leader_session_path = state_dir / "leader-session.json"
    leader_session_path.write_text(
        json.dumps({
            "agent": args.agent,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2),
        encoding="utf-8",
    )

    if args.auto_paste:
        prompt_text = lp.render(project_name=name, state_dir=state_dir)
        prompt_path = state_dir / "leader-prompt.md"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        buffer_name = f"fleet-leader-{name}"
        try:
            prompt_pointer.load_pointer_buffer(tmux_mod, buffer_name, prompt_path)
            time.sleep(max(0.0, args.prompt_delay))
            # Name the session BEFORE pasting: vendors with no launch-time flag
            # (codex) rename via post-ready keystrokes; claude is already named
            # at launch → session_rename_keys is [] → no-op.
            for text, enter in agents_mod.session_rename_keys(args.agent, session_name):
                tmux_mod.send_keys(session, "leader", text, enter=enter)
                time.sleep(0.6)
            tmux_mod.paste_buffer(session, "leader", buffer_name)
            time.sleep(0.8)
            tmux_mod.send_keys(session, "leader", "", enter=True)
        except tmux_mod.TmuxError as e:
            print(f"warn: leader prompt paste failed: {e}", file=sys.stderr)

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
