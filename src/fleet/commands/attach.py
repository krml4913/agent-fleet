"""``fleet attach [<target>]`` — shortcut for ``tmux attach -t fleet-<name>:<window>``."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .. import state as state_mod
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
    p.add_argument("--project", default=".", help="Project name (default: resolved from cwd)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    state_dir = state_mod.resolve_state_dir(Path.cwd(), project_name=args.project if args.project != "." else None)
    if state_dir is None:
        print(
            f"error: no registered project found for {args.project!r}",
            file=sys.stderr,
        )
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

    if args.target == "leader":
        window = "leader"
    else:
        window = f"task-{args.target}"

    try:
        windows = tmux_mod.list_windows(session)
    except tmux_mod.TmuxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if window not in windows:
        print(
            f"error: window not found: {session}:{window}\n"
            f"  existing: {', '.join(windows)}",
            file=sys.stderr,
        )
        return 1

    # grouped session でアクティブウィンドウを独立させる (Issue #76)
    # 同一 session に attach した他クライアントのウィンドウに影響しない
    view = f"{session}-view-{os.getpid()}"
    try:
        r = subprocess.run(
            ["tmux", "new-session", "-d", "-s", view, "-t", session],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            # view session が既に存在する場合は名前に乱数を足してリトライ
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
            ["tmux", "set-option", "-t", view, "destroy-unattached", "on"],
            capture_output=True,
            text=True,
        )
        if r3.returncode != 0:
            print(f"error: failed to configure view session: {r3.stderr.strip()}", file=sys.stderr)
            subprocess.run(["tmux", "kill-session", "-t", view], capture_output=True)
            return 1

        r4 = subprocess.run(
            ["tmux", "select-window", "-t", f"{view}:{window}"],
            capture_output=True,
            text=True,
        )
        if r4.returncode != 0:
            print(f"error: failed to select window in view session: {r4.stderr.strip()}", file=sys.stderr)
            subprocess.run(["tmux", "kill-session", "-t", view], capture_output=True)
            return 1

    except FileNotFoundError:
        print("error: tmux not on PATH", file=sys.stderr)
        return 1

    os.execvp("tmux", ["tmux", "attach", "-t", view])
