"""``fleet attach [<target>]`` — shortcut for ``tmux attach -t fleet-<name>:<window>``."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from .. import state as state_mod
from .. import task_context
from .. import tmux as tmux_mod


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "attach",
        help="Attach to the leader pane or a task driver pane",
        description=(
            "Shortcut for `tmux attach -t fleet-<project>:<window>`. "
            "Target is either 'leader' (default) or a task id."
        ),
    )
    p.add_argument(
        "target",
        nargs="?",
        default="leader",
        help="'leader' (default) or a task id (e.g. 1, 42)",
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

    if not tmux_mod.available():
        print("error: tmux not on PATH", file=sys.stderr)
        return 1

    project = state_mod.load_project(state_dir)
    name = project.get("name") or "fleet"
    session = f"fleet-{name}"

    if not tmux_mod.session_exists(session):
        print(f"error: tmux session not running: {session}", file=sys.stderr)
        print(f"  start it: fleet leader --project {args.project or name}", file=sys.stderr)
        return 1

    # Sweep stale view sessions left over from a previous attach (best-effort)
    _sweep_stale_view_sessions(session)

    try:
        windows = tmux_mod.list_windows(session)
    except tmux_mod.TmuxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.target == "leader":
        window = "leader"
    else:
        matches = tmux_mod.matching_task_window_names(windows, args.target)
        if len(matches) == 1:
            window = matches[0]
        elif len(matches) > 1:
            print(
                f"error: multiple windows found for task: {args.target}\n"
                f"  matching: {', '.join(matches)}",
                file=sys.stderr,
            )
            return 1
        else:
            window = args.target
    if window not in windows:
        print(
            f"error: window not found: {session}:{window}\n"
            f"  existing: {', '.join(windows)}",
            file=sys.stderr,
        )
        return 1

    # Give this client an independent active window via a grouped session (Issue #76)
    # so it does not affect windows of other clients attached to the same session
    view = f"{session}-view-{os.getpid()}"
    try:
        r = subprocess.run(
            ["tmux", "new-session", "-d", "-s", view, "-t", session],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            # If the view session already exists, retry with a random suffix on the name
            import secrets
            view = f"{session}-view-{os.getpid()}-{secrets.token_hex(4)}"
            r2 = subprocess.run(
                ["tmux", "new-session", "-d", "-s", view, "-t", session],
                capture_output=True,
                text=True,
            )
            if r2.returncode != 0:
                print(f"error: failed to create view session: {r2.stderr.strip()}", file=sys.stderr)
                return 1

        r3 = subprocess.run(
            ["tmux", "select-window", "-t", f"{view}:{window}"],
            capture_output=True,
            text=True,
        )
        if r3.returncode != 0:
            print(f"error: failed to select window in view session: {r3.stderr.strip()}", file=sys.stderr)
            subprocess.run(["tmux", "kill-session", "-t", view], capture_output=True)
            return 1

    except FileNotFoundError:
        print("error: tmux not on PATH", file=sys.stderr)
        return 1

    os.execvp("tmux", ["tmux", "attach", "-t", view])


def _sweep_stale_view_sessions(session: str) -> None:
    """Kill old view sessions that have no client attached (best-effort)."""
    try:
        r = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name} #{session_attached}"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return
        prefix = f"{session}-view-"
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name, attached = parts[0], parts[1]
            if name.startswith(prefix) and attached == "0":
                subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
    except FileNotFoundError:
        pass
