"""Orchestrator — advance a task to the next stage after a driver calls done.

Called by done.py after the current stage driver completes. This module
owns all stage-transition logic; done.py stays thin.

Stage 5 will add the peer_review loop and user_approval gate. This module
only handles one-directional sequential flow.
"""
from __future__ import annotations

from pathlib import Path

from . import state as state_mod


def advance(
    state_dir: Path,
    task_id: str,
    task: dict,
    *,
    result: str = "approved",
    dry_run: bool = False,
) -> None:
    """Advance the task state machine after the current stage driver calls done.

    result="approved"           → mark stage done; launch next stage or complete task.
    result="changes-requested"  → placeholder for stage 5 peer_review loop; leaves
                                  current stage in place and records the result.
    """
    stages = task.get("stages") or []

    if not stages:
        task["status"] = "completed"
        state_mod.save_task(state_dir, task_id, task)
        return

    current_idx = task.get("current_stage", 0)
    if not isinstance(current_idx, int):
        current_idx = 0

    if result == "changes-requested":
        # Stage 5 will implement the peer_review loop; record result as placeholder.
        if 0 <= current_idx < len(stages):
            stages[current_idx]["result"] = "changes-requested"
        task["stages"] = stages
        state_mod.save_task(state_dir, task_id, task)
        return

    # result == "approved": mark current stage done, then find next
    if 0 <= current_idx < len(stages):
        stages[current_idx]["status"] = "done"

    next_idx: int | None = None
    for i in range(current_idx + 1, len(stages)):
        if stages[i].get("status") == "pending":
            next_idx = i
            break

    if next_idx is not None:
        stages[next_idx]["status"] = "running"
        task["stages"] = stages
        task["current_stage"] = next_idx
        task["status"] = state_mod.derive_task_status(stages)
        state_mod.save_task(state_dir, task_id, task)

        if not dry_run:
            _launch_next_stage(state_dir, task_id, task, next_idx, stages[next_idx])
    else:
        task["stages"] = stages
        task["current_stage"] = state_mod.get_current_stage_index(stages)
        task["status"] = "completed"
        state_mod.save_task(state_dir, task_id, task)


def _launch_next_stage(
    state_dir: Path,
    task_id: str,
    task: dict,
    stage_idx: int,
    stage: dict,
) -> None:
    """Render driver-prompt.md for the next stage and open its tmux window."""
    from . import driver_prompt as dp
    from . import tmux as tmux_mod
    from .commands.start import launch_stage_driver

    if not tmux_mod.available():
        return

    task_dir_path = state_mod.task_dir(state_dir, task_id)
    project = state_mod.load_project(state_dir)
    project_name = project.get("name", "?")

    role_name = stage.get("role", "driver")
    agent_spec = stage.get("agent", "")
    topology_name = task.get("topology", "unknown")
    description = task.get("description") or task.get("title", "")

    prompt = dp.render(
        task_id=task_id,
        description=description,
        topology_name=topology_name,
        role=role_name,
        agent=agent_spec,
    )
    (task_dir_path / "driver-prompt.md").write_text(prompt, encoding="utf-8")

    launch_stage_driver(
        state_dir=state_dir,
        task_id=task_id,
        task_dir=task_dir_path,
        stage_idx=stage_idx,
        stage=stage,
        project_name=project_name,
    )
