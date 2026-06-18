"""``fleet-agent send-prompt <task-id>`` — deliver a driver-prompt pointer.

Starts a detached prompt deliverer that waits for the agent CLI to be
ready, then pastes a small pointer to ``driver-prompt.md`` (not the prompt
body) into the task pane.
"""
from __future__ import annotations

import argparse
import sys

from .. import prompt_deliverer
from .. import state as state_mod
from .. import task_context
from .. import tmux as tmux_mod


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "send-prompt",
        help="Deliver a driver-prompt.md pointer into the task pane",
        description=(
            "Starts a detached deliverer that pastes a small pointer to "
            "<state>/tasks/task-<id>/driver-prompt.md into the task's tmux "
            "window once the agent CLI is ready."
        ),
    )
    p.add_argument("task_id", help="Task id")
    p.add_argument(
        "--project",
        default=".",
        help=(
            "Project name (registry); required from a project-agnostic leader "
            "session, else resolved from FLEET_STATE_DIR / cwd"
        ),
    )
    p.add_argument(
        "--prompt-timeout",
        type=float,
        default=prompt_deliverer.DEFAULT_TIMEOUT_SECONDS,
        metavar="SEC",
        help="Hard timeout for detached prompt delivery (default: 600)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    project_name = args.project if args.project != "." else None
    try:
        state_dir = task_context.resolve_project_state_dir(project_name=project_name)
    except task_context.ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not tmux_mod.available():
        print("error: tmux not on PATH", file=sys.stderr)
        return 1

    prompt_path = state_mod.task_dir(state_dir, args.task_id) / "driver-prompt.md"
    if not prompt_path.is_file():
        print(f"error: no driver-prompt.md at {prompt_path}", file=sys.stderr)
        return 1

    try:
        task = state_mod.load_task(state_dir, args.task_id)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # The driver window lives in the task's owner session (Issue #166 §5.2).
    label = state_mod.task_owner_session(task)
    session = f"fleet-{label}"
    buffer_name = f"fleet-task-{args.task_id}"

    if not tmux_mod.session_exists(session):
        print(
            f"error: session not running: {session}\n"
            f"  start it:  fleet leader --name {label}",
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

    agent_spec = _current_agent(task)
    if agent_spec is None:
        print(f"error: no current agent in task-{args.task_id}", file=sys.stderr)
        return 1

    try:
        log_path = prompt_deliverer.start_detached(
            state_dir=state_dir,
            task_id=args.task_id,
            session=session,
            window=window,
            prompt_path=prompt_path,
            buffer_name=buffer_name,
            agent_spec=agent_spec,
            timeout=args.prompt_timeout,
        )
    except tmux_mod.TmuxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"prompt deliverer detached → {session}:{window} (log: {log_path})")
    return 0


def _current_agent(task: dict) -> str | None:
    stages = task.get("stages") or []
    idx = task.get("current_stage", 0)
    if not isinstance(idx, int) or idx < 0 or idx >= len(stages):
        return None
    stage = stages[idx]
    if not isinstance(stage, dict):
        return None
    agent = stage.get("agent")
    return agent if isinstance(agent, str) and agent else None
