"""``fleet-agent send-prompt <task-id>`` — paste a driver-prompt pointer.

Useful when `fleet-agent start` was run without `--auto-paste` (the safer
default) and the user wants to inject the prompt pointer after attaching
to the window and confirming the agent CLI is ready.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import prompt_pointer
from .. import state as state_mod
from .. import tmux as tmux_mod


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "send-prompt",
        help="Paste a driver-prompt.md pointer into the task pane",
        description=(
            "Loads a small pointer to <state>/tasks/task-<id>/driver-prompt.md "
            "into the named tmux buffer and pastes it into the task's tmux window."
        ),
    )
    p.add_argument("task_id", help="Task id")
    p.add_argument("--project", default=".", help="Project name (default: resolved from cwd)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    state_dir = state_mod.resolve_state_dir(Path.cwd(), project_name=project_name)
    if state_dir is None:
        print(
            f"error: no registered project found for {args.project!r}",
            file=sys.stderr,
        )
        return 1

    if not tmux_mod.available():
        print("error: tmux not on PATH", file=sys.stderr)
        return 1

    prompt_path = state_mod.task_dir(state_dir, args.task_id) / "driver-prompt.md"
    if not prompt_path.is_file():
        print(f"error: no driver-prompt.md at {prompt_path}", file=sys.stderr)
        return 1

    project = state_mod.load_project(state_dir)
    name = project.get("name") or "fleet"
    session = f"fleet-{name}"
    buffer_name = f"fleet-task-{args.task_id}"

    if not tmux_mod.session_exists(session):
        print(
            f"error: session not running: {session}\n"
            f"  start it:  fleet leader --project {project_name or args.project}",
            file=sys.stderr,
        )
        return 1
    try:
        matches = tmux_mod.task_window_names(session, args.task_id)
    except tmux_mod.TmuxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if len(matches) != 1:
        detail = (
            f"multiple windows found: {', '.join(matches)}"
            if matches
            else "window not found"
        )
        print(
            f"error: {detail}: {session}:{args.task_id}",
            file=sys.stderr,
        )
        return 1
    window = matches[0]

    try:
        prompt_pointer.paste_pointer_buffer(
            tmux_mod,
            session=session,
            window=window,
            buffer_name=buffer_name,
            prompt_path=prompt_path,
        )
        # Some CLIs accept Enter to submit, others need a second one; we
        # send one explicitly so the paste is committed.
        tmux_mod.send_keys(session, window, "", enter=True)
    except tmux_mod.TmuxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"pasted prompt pointer → {session}:{window}")
    return 0
