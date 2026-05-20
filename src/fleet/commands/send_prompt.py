"""``fleet-agent send-prompt <task-id>`` — paste the driver-prompt into a task pane.

Useful when `fleet-agent start` was run without `--auto-paste` (the safer
default) and the user wants to inject the prompt after attaching to the
window and confirming the agent CLI is ready.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import state as state_mod
from .. import tmux as tmux_mod


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "send-prompt",
        help="Paste driver-prompt.md into the task pane",
        description=(
            "Re-loads <state>/tasks/task-<id>/driver-prompt.md into the "
            "named tmux buffer and pastes it into fleet-<project>:task-<id>."
        ),
    )
    p.add_argument("task_id", help="Task id")
    p.add_argument("--project", default=".", help="Project path (default: cwd)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    state_dir = state_mod.discover_state_dir(Path(args.project))
    if state_dir is None:
        print(
            f"error: no .fleet-state/ found under {Path(args.project).resolve()}",
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
    window = f"task-{args.task_id}"
    buffer_name = f"fleet-task-{args.task_id}"

    if not tmux_mod.session_exists(session):
        print(
            f"error: session not running: {session}\n"
            f"  start it:  fleet leader --project {args.project}",
            file=sys.stderr,
        )
        return 1
    if window not in tmux_mod.list_windows(session):
        print(
            f"error: window not found: {session}:{window}",
            file=sys.stderr,
        )
        return 1

    try:
        tmux_mod.load_buffer(buffer_name, str(prompt_path))
        tmux_mod.paste_buffer(session, window, buffer_name)
        # Some CLIs accept Enter to submit, others need a second one; we
        # send one explicitly so the paste is committed.
        tmux_mod.send_keys(session, window, "", enter=True)
    except tmux_mod.TmuxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"pasted prompt → {session}:{window}")
    return 0
