"""Orchestrator — advance a task to the next stage after a driver calls done.

Called by done.py after the current stage driver completes. This module
owns all stage-transition logic; done.py stays thin.

Stage 5 adds the peer_review loop and user_approval gate. The iteration cap
comes from the stage's ``peer_review.max_iterations`` field (default 3). The
processing order within a stage is:

    implement → peer_review loop → user_approval gate → stage done
"""
from __future__ import annotations

from pathlib import Path

from . import state as state_mod
from .events import append_event, truncate_text, utcnow_iso

# Default peer_review iteration cap when a stage omits peer_review.max_iterations.
DEFAULT_MAX_ITERATIONS = 3


def _peer_review_max_iterations(pr: dict) -> int:
    """Return the peer_review iteration cap for a stage's ``peer_review`` block.

    Reads ``peer_review.max_iterations``, falling back to
    ``DEFAULT_MAX_ITERATIONS`` when the field is absent or unusable so that
    formations without the field behave identically to the old hardcoded cap.
    """
    try:
        return int(pr.get("max_iterations", DEFAULT_MAX_ITERATIONS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ITERATIONS


class UserApprovalError(ValueError):
    """Raised when approve/reject is not valid for the current task state."""


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
        peer_review.phase="implementing" → hand off to reviewer.
        peer_review.phase="reviewing"    → peer_review passed; check user_approval.
        user_approval.status="asked"     → gate is raised; idempotent no-op (re-asserts
                                           awaiting_orders). Only approve_user_approval /
                                           reject_user_approval (via ``fleet-agent
                                           approve``/``reject``) settle a raised gate.
        (no peer_review, no user_approval) → mark stage done; launch next.

    result="changes-requested":
        peer_review.phase="reviewing" → return to the live implementer (iteration++) or
                                        escalate to user if max iterations exceeded.
        (no peer_review on stage)     → record result, leave stage in place (legacy).

    ``awaiting_orders`` invariant: once a task is parked in awaiting_orders — whether a
    user_approval gate is raised or a peer_review escalation has exceeded the iteration
    cap — only the explicit leader relay (approve_user_approval / reject_user_approval)
    settles it. A driver's ``done`` (the sole caller of this function) is a no-op while
    the task awaits a human decision; it can neither self-approve nor self-reject the
    gate.
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

    # ── leader-gated pause: awaiting_orders is the human's turn ──────────
    # Once a task is parked in ``awaiting_orders`` — a user_approval gate is
    # raised, or a peer_review escalation exceeded the iteration cap — only the
    # explicit leader relay (``fleet-agent approve``/``reject`` →
    # ``approve_user_approval``/``reject_user_approval``) may settle it. A
    # driver's ``done`` is the ONLY caller of ``advance`` and must never move the
    # task past this point. Re-assert the pause and return (idempotent no-op;
    # state is already persisted as awaiting_orders, so no save is needed).
    if task.get("status") == "awaiting_orders":
        return

    # ── peer_review loop ──────────────────────────────────────────────────
    pr = stage.get("peer_review")
    if isinstance(pr, dict) and pr.get("role"):
        phase = pr.get("phase", "implementing")

        if phase == "implementing":
            # Implementer called done; hand off to reviewer.
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
                _handoff_to_stage_driver(
                    state_dir,
                    task_id,
                    task,
                    current_idx,
                    reviewer_stage,
                    message=_peer_review_handoff_message(
                        target_role=reviewer_stage["role"],
                        phase="reviewing",
                        iteration=int(pr.get("iteration", 1) or 1),
                    ),
                )
            return

        if phase == "reviewing":
            if result == "changes-requested":
                iteration = pr.get("iteration", 1)
                if iteration >= _peer_review_max_iterations(pr):
                    # Max iterations exceeded; escalate to user.
                    task["stages"] = stages
                    task["status"] = "awaiting_orders"
                    state_mod.save_task(state_dir, task_id, task)
                    _notify_escalation(state_dir, task_id, task, stage)
                    return
                # Return to the already-running implementer for the next iteration.
                pr["iteration"] = iteration + 1
                pr["phase"] = "implementing"
                stage["peer_review"] = pr
                stages[current_idx] = stage
                task["stages"] = stages
                state_mod.save_task(state_dir, task_id, task)
                if not dry_run:
                    _handoff_to_stage_driver(
                        state_dir,
                        task_id,
                        task,
                        current_idx,
                        stage,
                        message=_peer_review_handoff_message(
                            target_role=stage.get("role", "driver"),
                            phase="implementing",
                            iteration=int(pr.get("iteration", 1) or 1),
                        ),
                    )
                return

            # result == "approved": peer_review passed; fall through to user_approval.
            pr["phase"] = "approved"
            stage["peer_review"] = pr
            stages[current_idx] = stage

        # phase == "approved": peer_review already passed; fall through.

    else:
        # No peer_review: legacy changes-requested records result and stops.
        ua = stage.get("user_approval")
        waiting_for_user = (
            isinstance(ua, dict)
            and ua.get("required")
            and ua.get("status") == "asked"
        )
        if result == "changes-requested" and not waiting_for_user:
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
            task["status"] = "awaiting_orders"
            state_mod.save_task(state_dir, task_id, task)
            _request_user_approval(state_dir, task_id, task, stage)
            return

        if ua_status == "asked":
            # Defensive: the gate is raised and awaiting the leader/user.
            # Normally unreachable because the awaiting_orders guard above
            # returns first; but if task.status and the gate ever desync, a
            # driver's ``done`` must STILL NOT settle the gate — neither approve
            # nor reject. Re-assert the pause and return. Settling the gate is the
            # sole responsibility of approve_user_approval / reject_user_approval,
            # reached only via ``fleet-agent approve``/``reject``.
            ua["status"] = "asked"
            stage["user_approval"] = ua
            stages[current_idx] = stage
            task["stages"] = stages
            task["status"] = "awaiting_orders"
            state_mod.save_task(state_dir, task_id, task)
            return

    # ── mark stage done and advance ───────────────────────────────────────
    _mark_stage_done_and_advance(state_dir, task_id, task, current_idx, dry_run=dry_run)


def approve_user_approval(
    state_dir: Path,
    task_id: str,
    task: dict,
    *,
    dry_run: bool = False,
) -> None:
    """Relay explicit user approval for the current stage's approval gate."""
    stages, current_idx, stage, ua, is_escalation = _require_user_relay_target(task)

    if is_escalation:
        pr = stage.get("peer_review")
        if isinstance(pr, dict):
            pr["phase"] = "approved"
            stage["peer_review"] = pr

    if ua is not None:
        ua["status"] = "approved"
        stage["user_approval"] = ua
    stages[current_idx] = stage
    task["stages"] = stages

    _mark_stage_done_and_advance(state_dir, task_id, task, current_idx, dry_run=dry_run)


def reject_user_approval(
    state_dir: Path,
    task_id: str,
    task: dict,
    *,
    dry_run: bool = False,
) -> None:
    """Relay explicit user rejection and return the stage to implementation."""
    stages, current_idx, stage, ua, _is_escalation = _require_user_relay_target(task)

    if ua is not None:
        ua["status"] = "pending"
        stage["user_approval"] = ua

    pr = stage.get("peer_review")
    if isinstance(pr, dict) and pr.get("role"):
        pr["phase"] = "implementing"
        pr["iteration"] = int(pr.get("iteration", 1) or 1) + 1
        stage["peer_review"] = pr

    stage["status"] = "running"
    stages[current_idx] = stage
    task["stages"] = stages
    task["current_stage"] = current_idx
    task["status"] = "running"
    state_mod.save_task(state_dir, task_id, task)

    if not dry_run:
        pr = stage.get("peer_review")
        if isinstance(pr, dict) and pr.get("role"):
            _handoff_to_stage_driver(
                state_dir,
                task_id,
                task,
                current_idx,
                stage,
                message=_peer_review_handoff_message(
                    target_role=stage.get("role", "driver"),
                    phase="implementing",
                    iteration=int(pr.get("iteration", 1) or 1),
                ),
            )
        else:
            _launch_driver_for_stage(state_dir, task_id, task, current_idx, stage)


def _require_user_relay_target(
    task: dict,
) -> tuple[list[dict], int, dict, dict | None, bool]:
    stages = task.get("stages") or []
    if not stages:
        raise UserApprovalError("task has no stages")

    current_idx = task.get("current_stage", 0)
    if not isinstance(current_idx, int) or not (0 <= current_idx < len(stages)):
        raise UserApprovalError("task has no current stage")

    stage = stages[current_idx]
    ua = stage.get("user_approval")
    if isinstance(ua, dict) and ua.get("required") and ua.get("status") == "asked":
        return stages, current_idx, stage, ua, False

    pr = stage.get("peer_review")
    is_escalation = (
        task.get("status") == "awaiting_orders"
        and isinstance(pr, dict)
        and pr.get("phase") == "reviewing"
        and int(pr.get("iteration", 1) or 1) >= _peer_review_max_iterations(pr)
    )
    if is_escalation:
        return stages, current_idx, stage, ua if isinstance(ua, dict) else None, True

    if not (isinstance(ua, dict) and ua.get("required")):
        raise UserApprovalError("current stage does not require user approval")
    raise UserApprovalError("current stage is not waiting for user approval")


def _mark_stage_done_and_advance(
    state_dir: Path,
    task_id: str,
    task: dict,
    current_idx: int,
    *,
    dry_run: bool = False,
) -> None:
    stages = task.get("stages") or []
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
    *,
    replace_task_windows: bool = True,
) -> None:
    """Render driver-prompt.md for a stage and open its tmux window."""
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
    formation_name = task.get("formation", "unknown")
    description = task.get("description") or task.get("title", "")

    prompt = dp.render(
        task_id=task_id,
        description=description,
        formation_name=formation_name,
        role=role_name,
        agent=agent_spec,
        state_dir=state_dir,
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
        owner_session=state_mod.task_owner_session(task),
        window_cwd=Path(worktree) if worktree else None,
        replace_task_windows=replace_task_windows,
    )


def _handoff_to_stage_driver(
    state_dir: Path,
    task_id: str,
    task: dict,
    stage_idx: int,
    stage: dict,
    *,
    message: str,
) -> None:
    """Wake a live stage driver, launching it first if its pane does not exist."""
    from . import tmux as tmux_mod

    if not tmux_mod.available():
        return

    session = f"fleet-{state_mod.task_owner_session(task)}"
    window = _stage_window_name(task_id, stage)

    try:
        windows = tmux_mod.task_window_names(session, task_id)
    except tmux_mod.TmuxError:
        windows = []

    if window not in windows:
        _launch_driver_for_stage(
            state_dir,
            task_id,
            task,
            stage_idx,
            stage,
            replace_task_windows=False,
        )
        append_event(
            state_dir / "events.jsonl",
            "handoff_launch",
            task_id=task_id,
            role=stage.get("role", "driver"),
        )
        return

    _append_handoff_inbox(state_dir, task_id, message)
    try:
        from . import driver_prompt as dp

        fleet_bin = dp.fleet_agent_bin()
        tmux_mod.send_keys(
            session,
            window,
            f"[fleet] new message in inbox. run {fleet_bin} inbox-read to check",
        )
    except tmux_mod.TmuxError:
        return
    append_event(
        state_dir / "events.jsonl",
        "handoff_message",
        task_id=task_id,
        role=stage.get("role", "driver"),
    )


def _stage_window_name(task_id: str, stage: dict) -> str:
    role_name = stage.get("role", "driver")
    return f"{task_id}·{role_name}"


def _append_handoff_inbox(state_dir: Path, task_id: str, message: str) -> None:
    task_dir_path = state_mod.task_dir(state_dir, task_id)
    inbox_path = task_dir_path / "inbox.md"
    ts = utcnow_iso()
    block = f"### {ts}\n\n{message}\n\n"
    existing = inbox_path.read_text(encoding="utf-8") if inbox_path.exists() else ""
    inbox_path.write_text(existing + block, encoding="utf-8")
    append_event(
        state_dir / "events.jsonl",
        "inbox_message",
        task_id=task_id,
        # Audit snippet only — the full message lives in inbox.md.
        message=truncate_text(message),
        inbox_ts=ts,
    )


def _peer_review_handoff_message(
    *,
    target_role: str,
    phase: str,
    iteration: int,
) -> str:
    from . import driver_prompt as dp

    fleet_bin = dp.fleet_agent_bin()
    return (
        f"[fleet handoff] role={target_role} phase={phase} iteration={iteration}. "
        f"It is your turn now. Run `{fleet_bin} inbox-read` if you have not "
        "already, continue from the retained pane context, and call "
        f"`{fleet_bin} done --result approved` when finished. Reviewers should "
        "use `--result changes-requested` when rework is needed."
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
    pr = stage.get("peer_review")
    max_iterations = (
        _peer_review_max_iterations(pr)
        if isinstance(pr, dict)
        else DEFAULT_MAX_ITERATIONS
    )
    question = (
        f"peer_review for stage '{role}' exceeded the maximum {max_iterations} "
        "iterations. Please review manually and tell the leader whether to "
        "approve or reject."
    )
    _write_question(state_dir, task_id, question)
    events_mod.append_event(
        state_dir / "events.jsonl",
        "awaiting_orders",
        task_id=task_id,
        question=question,
    )
    project = state_mod.load_project(state_dir)
    notify.send(
        state_dir,
        title=f"fleet {project.get('name', '?')}: task-{task_id} awaiting orders",
        message=question,
        level="waiting",
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
        "Tell the leader whether to approve or reject it."
    )
    _write_question(state_dir, task_id, question)
    events_mod.append_event(
        state_dir / "events.jsonl",
        "awaiting_orders",
        task_id=task_id,
        question=question,
    )
    project = state_mod.load_project(state_dir)
    notify.send(
        state_dir,
        title=f"fleet {project.get('name', '?')}: task-{task_id} needs approval",
        message=question,
        level="waiting",
    )


def _write_question(state_dir: Path, task_id: str, question: str) -> None:
    from . import events as events_mod

    qpath = state_mod.task_dir(state_dir, task_id) / "questions.md"
    block = f"### {events_mod.utcnow_iso()}\n\n{question}\n\n"
    existing = qpath.read_text(encoding="utf-8") if qpath.exists() else ""
    qpath.write_text(existing + block, encoding="utf-8")
