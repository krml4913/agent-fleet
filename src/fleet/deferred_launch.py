"""Deferred stage launch: open the next stage after the calling pane's ``done`` exits.

A cross-stage advance replaces the task's windows: the previous stage's window
is killed, then the next stage's window is opened. The advance normally runs
inside ``fleet-agent done`` (or an in-pane ``approve``) — i.e. *inside* the
very window that gets killed.

On zellij, on every platform, closing a tab ends every process attached to
it, including that ``fleet-agent done`` process. It died inside
``kill_window``, before the next stage's tab was opened: the task stayed
``running`` on a stage with no window, the ``done`` event and notification
were lost, and the pane-env file was left behind (found in the Windows E2E of
a two-stage formation, and again on macOS in #313).

So when the backend reports :attr:`fleet.mux.Mux.window_close_kills_caller`
and the caller runs in one of the task's own panes, the orchestrator hands the
launch to this detached helper instead. It waits until the calling process
(``done``) has exited, re-reads the task, and runs the ordinary launch
(``kill`` old windows → ``new`` window → prompt deliverer). The helper has no
console and breaks away from the pane's job (:func:`fleet.proc.spawn_detached`),
so closing the old tab does not end it.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import state as state_mod
from .events import append_event
from .proc import spawn_detached
from .task_context import driver_pane_task_id

#: How long the helper waits for the calling ``done`` process to exit before
#: launching anyway (the launch stays correct either way; waiting only avoids
#: killing the caller mid-way).
DEFAULT_WAIT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 0.2

LOG_NAME = "stage-launch.log"


def _fleet_clone_root() -> Path:
    # src/fleet/deferred_launch.py → parents[0]=fleet, [1]=src, [2]=clone root
    return Path(__file__).resolve().parents[2]


def running_in_task_pane(task_id: str) -> bool:
    """True when this process runs inside one of ``task_id``'s driver panes.

    Driver panes get ``FLEET_TASK_ID`` from ``launch_stage_driver``; the leader
    pane and a user's own terminal do not.
    """
    return driver_pane_task_id() == str(task_id)


def start_detached(
    *,
    state_dir: Path,
    task_id: str,
    stage_idx: int,
    wait_pid: int,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> Path:
    """Spawn the detached helper that launches ``stage_idx`` once ``wait_pid`` exits."""
    log_path = state_mod.task_dir(state_dir, task_id) / LOG_NAME
    repo_root = _fleet_clone_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(repo_root / "src"),
            str(repo_root / "vendor"),
            env.get("PYTHONPATH", ""),
        ]
    )
    args = [
        sys.executable,
        "-m",
        "fleet.deferred_launch",
        "--state-dir",
        str(state_dir),
        "--task-id",
        str(task_id),
        "--stage-idx",
        str(stage_idx),
        "--wait-pid",
        str(wait_pid),
        "--wait-seconds",
        str(wait_seconds),
    ]
    spawn_detached(args, cwd=repo_root, env=env, log_path=log_path)
    append_event(
        state_dir / "events.jsonl",
        "stage_launch_deferred",
        task_id=task_id,
        stage=stage_idx,
    )
    return log_path


def _wait_for_exit(pid: int, timeout: float) -> bool:
    from .pane_launch import pid_alive

    deadline = time.monotonic() + max(0.0, timeout)
    while pid_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL_SECONDS)
    return True


def run(
    *,
    state_dir: Path,
    task_id: str,
    stage_idx: int,
    wait_pid: int,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> int:
    """Wait for ``wait_pid``, then launch ``stage_idx`` if it is still current."""
    from . import orchestrator

    if wait_pid > 0 and not _wait_for_exit(wait_pid, wait_seconds):
        print(
            f"warn: pid {wait_pid} still running after {wait_seconds:g}s; launching anyway",
            flush=True,
        )

    task = state_mod.load_task(state_dir, task_id)
    stages = task.get("stages") or []
    if task.get("current_stage") != stage_idx or not (0 <= stage_idx < len(stages)):
        print(
            f"skip: task-{task_id} moved on (current_stage={task.get('current_stage')!r}, "
            f"wanted {stage_idx})",
            flush=True,
        )
        return 0
    stage = stages[stage_idx]
    if stage.get("status") != "running" or task.get("status") in ("completed", "cancelled"):
        print(
            f"skip: task-{task_id} stage {stage_idx} is {stage.get('status')!r} "
            f"(task {task.get('status')!r})",
            flush=True,
        )
        return 0

    orchestrator._launch_driver_for_stage(
        state_dir, task_id, task, stage_idx, stage, defer_if_in_pane=False
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fleet.deferred_launch")
    p.add_argument("--state-dir", required=True, type=Path)
    p.add_argument("--task-id", required=True)
    p.add_argument("--stage-idx", required=True, type=int)
    p.add_argument("--wait-pid", required=True, type=int)
    p.add_argument("--wait-seconds", type=float, default=DEFAULT_WAIT_SECONDS)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(
        state_dir=args.state_dir,
        task_id=args.task_id,
        stage_idx=args.stage_idx,
        wait_pid=args.wait_pid,
        wait_seconds=args.wait_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
