"""Orchestrator — advance a task to the next stage after a driver calls done.

Called by done.py after the current stage driver completes. This module
owns all stage-transition logic; done.py stays thin.

Stage 5 adds the peer_review loop (max 3 iterations) and user_approval gate.
The processing order within a stage is:

    implement → peer_review loop (max 3) → user_approval gate → stage done
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

    result="approved":
        peer_review.phase="implementing" → launch reviewer.
        peer_review.phase="reviewing"    → peer_review passed; check user_approval.
        user_approval.status="asked"     → approve; mark stage done; launch next.
        (no peer_review, no user_approval) → mark stage done; launch next.

    result="changes-requested":
        peer_review.phase="reviewing" → re-launch implementer (iteration++) or
                                        escalate to user if max iterations exceeded.
        (no peer_review on stage)     → record result, leave stage in place (legacy).
    """
    stages = task.get("stages") or []

    if not stages:
        task["status"] = "completed"
        state_mod.save_task(state_dir, task_id, task)
        return

    current_idx = task.get("current_stage", 0)
    if not isinstance(current_idx, int):
        current_idx = 0

    if not (0 <= current_idx < len(stages)):
        task["status"] = "completed"
        state_mod.save_task(state_dir, task_id, task)
        return

    stage = stages[current_idx]

    # ── peer_review loop ──────────────────────────────────────────────────
    pr = stage.get("peer_review")
    if isinstance(pr, dict) and pr.get("role"):
        phase = pr.get("phase", "implementing")

        if phase == "implementing":
            # Implementer called done; launch reviewer.
            pr.setdefault("iteration", 1)
            pr["phase"] = "reviewing"
            stage["peer_review"] = pr
            stages[current_idx] = stage
            task["stages"] = stages
            state_mod.save_task(state_dir, task_id, task)
            if not dry_run:
                reviewer_stage = {
                    "role": pr["role"],
                    "agent": pr.get("agent") or stage.get("agent") or "claude:sonnet",
                    "status": "running",
                }
                _launch_driver_for_stage(
                    state_dir, task_id, task, current_idx, reviewer_stage
                )
            return

        if phase == "reviewing":
            if result == "changes-requested":
                iteration = pr.get("iteration", 1)
                if iteration >= 3:
                    # Max iterations exceeded; escalate to user.
                    task["stages"] = stages
                    task["status"] = "needs_input"
                    state_mod.save_task(state_dir, task_id, task)
                    _notify_escalation(state_dir, task_id, task, stage)
                    return
                # Re-launch implementer for next iteration.
                pr["iteration"] = iteration + 1
                pr["phase"] = "implementing"
                stage["peer_review"] = pr
                stages[current_idx] = stage
                task["stages"] = stages
                state_mod.save_task(state_dir, task_id, task)
                if not dry_run:
                    _launch_driver_for_stage(
                        state_dir, task_id, task, current_idx, stage
                    )
                return

            # result == "approved": peer_review passed; fall through to user_approval.
            pr["phase"] = "approved"
            stage["peer_review"] = pr
            stages[current_idx] = stage

        # phase == "approved": peer_review already passed; fall through.

    else:
        # No peer_review: legacy changes-requested records result and stops.
        if result == "changes-requested":
            stages[current_idx]["result"] = "changes-requested"
            task["stages"] = stages
            state_mod.save_task(state_dir, task_id, task)
            return

    # ── user_approval gate ────────────────────────────────────────────────
    ua = stage.get("user_approval")
    if isinstance(ua, dict) and ua.get("required"):
        ua_status = ua.get("status", "pending")

        if ua_status == "pending":
            # First time through: ask user for approval.
            ua["status"] = "asked"
            stage["user_approval"] = ua
            stages[current_idx] = stage
            task["stages"] = stages
            task["status"] = "needs_input"
            state_mod.save_task(state_dir, task_id, task)
            _request_user_approval(state_dir, task_id, task, stage)
            return

        if ua_status == "asked":
            # User is approving this call.
            ua["status"] = "approved"
            stage["user_approval"] = ua
            stages[current_idx] = stage
            task["stages"] = stages
            state_mod.save_task(state_dir, task_id, task)
            # Fall through to mark stage done.

    # ── mark stage done and advance ───────────────────────────────────────
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
            _launch_driver_for_stage(
                state_dir, task_id, task, next_idx, stages[next_idx]
            )
    else:
        task["stages"] = stages
        task["current_stage"] = state_mod.get_current_stage_index(stages)
        task["status"] = "completed"
        state_mod.save_task(state_dir, task_id, task)


# ---------------------------------------------------------------------------
# Driver launch helpers
# ---------------------------------------------------------------------------


def _launch_driver_for_stage(
    state_dir: Path,
    task_id: str,
    task: dict,
    stage_idx: int,
    stage: dict,
) -> None:
    """Render driver-prompt.md for a stage and open its tmux window."""
    from . import driver_prompt as dp
    from . import plugins as plugins_mod
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
    workflow = plugins_mod.load_workflow(state_dir)

    prompt = dp.render(
        task_id=task_id,
        description=description,
        topology_name=topology_name,
        role=role_name,
        agent=agent_spec,
        workflow_fragment=getattr(workflow, "DRIVER_PROMPT_FRAGMENT", ""),
    )
    (task_dir_path / "driver-prompt.md").write_text(prompt, encoding="utf-8")

    worktree = task.get("worktree")
    launch_stage_driver(
        state_dir=state_dir,
        task_id=task_id,
        task_dir=task_dir_path,
        stage_idx=stage_idx,
        stage=stage,
        project_name=project_name,
        window_cwd=Path(worktree) if worktree else None,
    )


# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------


def _notify_escalation(
    state_dir: Path,
    task_id: str,
    task: dict,
    stage: dict,
) -> None:
    """Notify user that peer_review has exceeded max iterations."""
    from . import events as events_mod
    from . import notify

    role = stage.get("role", "?")
    question = (
        f"peer_review for stage '{role}' exceeded the maximum 3 iterations. "
        f"Please review manually and run: fleet-agent done {task_id} --result=approved"
    )
    _write_question(state_dir, task_id, question)
    events_mod.append_event(
        state_dir / "events.jsonl",
        "needs_input",
        task_id=task_id,
        question=question,
    )
    project = state_mod.load_project(state_dir)
    notify.send(
        state_dir,
        title=f"fleet {project.get('name', '?')}: task-{task_id} needs input",
        message=question,
    )


def _request_user_approval(
    state_dir: Path,
    task_id: str,
    task: dict,
    stage: dict,
) -> None:
    """Ask user to approve the completed stage."""
    from . import events as events_mod
    from . import notify

    role = stage.get("role", "?")
    question = (
        f"Stage '{role}' is ready for your approval. "
        f"Run: fleet-agent done {task_id} --result=approved"
    )
    _write_question(state_dir, task_id, question)
    events_mod.append_event(
        state_dir / "events.jsonl",
        "needs_input",
        task_id=task_id,
        question=question,
    )
    project = state_mod.load_project(state_dir)
    notify.send(
        state_dir,
        title=f"fleet {project.get('name', '?')}: task-{task_id} needs approval",
        message=question,
    )


def _write_question(state_dir: Path, task_id: str, question: str) -> None:
    from . import events as events_mod

    qpath = state_mod.task_dir(state_dir, task_id) / "questions.md"
    block = f"### {events_mod.utcnow_iso()}\n\n{question}\n\n"
    existing = qpath.read_text(encoding="utf-8") if qpath.exists() else ""
    qpath.write_text(existing + block, encoding="utf-8")
