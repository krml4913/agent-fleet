"""``fleet-agent inbox <task-id> "<message>"`` — send a message to a driver's inbox.md.

A timestamped block is appended and the driver pane is woken via tmux send-keys
so it sees the notification even while waiting for input.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import state as state_mod
from .. import task_context
from .. import tmux as tmux_mod
from ..events import append_event, utcnow_iso


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "inbox",
        help="Append a message to a driver's inbox.md",
        description=(
            "Adds a timestamped block to <state>/tasks/task-<id>/inbox.md, "
            "emits an `inbox_message` event, and wakes the driver pane via tmux."
        ),
    )
    p.add_argument("task_id", help="Task id")
    p.add_argument(
        "message",
        nargs="+",
        help="Message body (joined with spaces if multiple words)",
    )
    p.add_argument(
        "--project",
        default=".",
        help=(
            "Project name (registry); required from a project-agnostic leader "
            "session, else resolved from FLEET_STATE_DIR / cwd"
        ),
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    try:
        state_dir = task_context.resolve_project_state_dir(project_name=project_name)
    except task_context.ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    task_dir = state_mod.task_dir(state_dir, args.task_id)
    if not task_dir.is_dir():
        print(f"error: no task dir: {task_dir}", file=sys.stderr)
        return 1

    inbox_path = task_dir / "inbox.md"
    body = " ".join(args.message)
    ts = utcnow_iso()
    block = f"### {ts}\n\n{body}\n\n"
    existing = inbox_path.read_text(encoding="utf-8") if inbox_path.exists() else ""
    inbox_path.write_text(existing + block, encoding="utf-8")

    append_event(
        state_dir / "events.jsonl",
        "inbox_message",
        task_id=args.task_id,
        message=body,
        inbox_ts=ts,
    )

    _wake_driver_pane(state_dir, args.task_id)

    print(f"sent to task-{args.task_id} inbox ({inbox_path})")
    return 0


def _wake_driver_pane(state_dir: Path, task_id: str) -> None:
    """Send a notification text into the driver's tmux pane.

    Silently skips if tmux is unavailable or the pane doesn't exist yet
    (e.g. driver not spawned or already finished). The driver window lives in
    its task's owner session (``fleet-<owner_session>``, Issue #166 §5.2), so we
    resolve the session from the task rather than the project. The inbox.md write
    is independent of this — only the live tmux nudge depends on the pane.
    """
    if not tmux_mod.available():
        return
    try:
        task = state_mod.load_task(state_dir, task_id)
    except FileNotFoundError:
        return
    session = f"fleet-{state_mod.task_owner_session(task)}"
    from .. import driver_prompt as dp

    fleet_bin = dp.fleet_agent_bin()
    try:
        windows = tmux_mod.task_window_names(session, task_id)
        for window in windows:
            tmux_mod.send_keys(
                session,
                window,
                f"[fleet] new message in inbox. run {fleet_bin} inbox-read to check",
            )
    except tmux_mod.TmuxError:
        # Pane not found or session gone — warn and continue.
        print(
            f"warn: could not wake driver pane {session}:{task_id} (not spawned or already done)",
            file=sys.stderr,
        )
