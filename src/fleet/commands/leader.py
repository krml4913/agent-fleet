"""``fleet leader`` — launch a project-agnostic leader session.

Creates a detached tmux session named ``fleet-<label>`` (default label
``main``), opens a single ``leader`` window in the **agent-fleet clone root**,
and starts the chosen agent CLI inside. If the session already exists, prints
the attach command and exits (one session per label).

Since Issue #166 a leader session is **not bound to a project** (design §4.1,
§5.6): it drops project resolution, pins cwd to the clone root, and carries its
label in ``FLEET_SESSION`` so ``fleet-agent start`` can stamp ``owner_session``
onto each task. Per-session state lives at ``global/sessions/<label>/``. Per §4.1
the leader only does dialogue and ``fleet-agent start``, never state polling.
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
DEFAULT_SESSION_LABEL = "main"

# commands/leader.py → parents[0]=commands, [1]=fleet, [2]=src, [3]=clone root.
_CLONE_ROOT = Path(__file__).resolve().parents[3]


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "leader",
        help="Launch a project-agnostic leader session",
        description=(
            "Create a tmux session 'fleet-<label>' (default label 'main') with a "
            "single leader window running the chosen agent CLI in the agent-fleet "
            "clone root. One session per label: if it already exists, prints the "
            "attach command and exits. The session is project-agnostic — it serves "
            "any project, and every dispatch passes --project explicitly."
        ),
    )
    p.add_argument(
        "--name",
        default=DEFAULT_SESSION_LABEL,
        metavar="LABEL",
        help=f"Session label → tmux fleet-<label> (default: {DEFAULT_SESSION_LABEL})",
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
    p.add_argument(
        "--scope",
        default=None,
        metavar="PROJECTS",
        help=(
            "Comma-separated project names to set as this session's scope at launch. "
            "Sets scope in session.json before the prompt is pasted. Names are "
            "validated against the registry. Example: --scope image-gallery,bmweb,fleet"
        ),
    )
    p.set_defaults(func=run, auto_paste=True)


def run(args: argparse.Namespace) -> int:
    label = getattr(args, "name", None) or DEFAULT_SESSION_LABEL
    session = f"fleet-{label}"

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

    session_dir = state_mod.session_dir(label)
    session_dir.mkdir(parents=True, exist_ok=True)

    # Session display name so the leader is identifiable in the session picker.
    session_name = f"{label}-leader"

    cli = agents_mod.cli_command(args.agent)
    cli = cli + agents_mod.session_name_launch_args(args.agent, session_name)
    cli_quoted = " ".join(shlex.quote(p) for p in cli)

    try:
        tmux_mod.new_session(
            session,
            window_name="leader",
            cwd=str(_CLONE_ROOT),
            env={
                "FLEET_SESSION": label,
                "FLEET_STATE_DIR": str(session_dir),
            },
        )
        tmux_mod.send_keys(session, "leader", cli_quoted)
    except tmux_mod.TmuxError as e:
        print(f"error: tmux setup failed: {e}", file=sys.stderr)
        return 1

    record: dict = {
        "label": label,
        "agent": args.agent,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pane": f"{session}:leader",
    }
    state_mod.session_record_path(label).write_text(
        json.dumps(record, indent=2),
        encoding="utf-8",
    )

    # Set scope from --scope flag before prompt is pasted
    scope_arg = getattr(args, "scope", None)
    if scope_arg:
        scope_names = [n.strip() for n in scope_arg.split(",") if n.strip()]
        try:
            state_mod.set_session_scope(label, scope_names, mode="set")
        except ValueError as e:
            print(f"error: --scope: {e}", file=sys.stderr)
            return 1

    if args.auto_paste:
        prompt_text = lp.render(session_label=label)
        prompt_path = session_dir / "leader-prompt.md"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        buffer_name = f"fleet-leader-{label}"
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
        session_dir / "events.jsonl",
        "leader_start",
        agent=args.agent,
        session=session,
        label=label,
    )

    print(f"leader started: session={session}, agent={args.agent}")
    print(f"  attach: tmux attach -t {session}")

    if args.attach:
        os.execvp("tmux", ["tmux", "attach", "-t", session])
    return 0
