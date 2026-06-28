"""Detached driver-prompt delivery after an agent CLI reaches its prompt."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import agents, notify, prompt_pointer, state as state_mod, tmux
from .adapters import REGISTRY, VendorAdapter
from .events import append_event, utcnow_iso


DEFAULT_TIMEOUT_SECONDS = 10 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
PASTE_SETTLE_SECONDS = 0.25
# Settle between each session-rename keystroke step (and after the last) so a
# TUI rename popup has a beat to open and close before the prompt paste.
RENAME_SETTLE_SECONDS = 0.6


@dataclass(frozen=True)
class EventCheckpoint:
    ts: str
    offset: int


def start_detached(
    *,
    state_dir: Path,
    task_id: str,
    session: str,
    window: str,
    prompt_path: Path,
    buffer_name: str,
    agent_spec: str,
    session_name: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    initial_delay: float = 0.0,
) -> Path:
    """Start a detached process that waits for readiness, then pastes the prompt."""
    log_path = state_mod.task_dir(state_dir, task_id) / "prompt-deliverer.log"
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
        "fleet.prompt_deliverer",
        "--state-dir",
        str(state_dir),
        "--task-id",
        task_id,
        "--session",
        session,
        "--window",
        window,
        "--prompt-path",
        str(prompt_path),
        "--buffer-name",
        buffer_name,
        "--agent",
        agent_spec,
        "--timeout",
        str(timeout),
        "--poll-interval",
        str(poll_interval),
        "--initial-delay",
        str(initial_delay),
    ]
    if session_name:
        args.extend(["--session-name", session_name])
    with log_path.open("ab") as log:
        subprocess.Popen(  # noqa: S603 - argv is constructed, no shell.
            args,
            cwd=str(repo_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    append_event(
        state_dir / "events.jsonl",
        "prompt_deliverer_started",
        task_id=task_id,
        window=window,
        agent=agent_spec,
        timeout_seconds=timeout,
    )
    return log_path


def deliver(
    *,
    state_dir: Path,
    task_id: str,
    session: str,
    window: str,
    prompt_path: Path,
    buffer_name: str,
    agent_spec: str,
    session_name: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    initial_delay: float = 0.0,
) -> int:
    vendor, _model = agents.parse_spec(agent_spec)
    adapter = REGISTRY[vendor]
    deadline = time.monotonic() + max(0.0, timeout)
    gate_notified = False

    if initial_delay > 0:
        time.sleep(initial_delay)

    while time.monotonic() <= deadline:
        try:
            pane = tmux.capture_pane(session, window)
        except tmux.TmuxError as e:
            _fail(state_dir, task_id, f"prompt deliverer cannot capture pane: {e}", window)
            return 1

        if adapter.ready.search(pane):
            try:
                # Name the session BEFORE pasting the prompt: for vendors with
                # no launch-time naming flag (codex), this drives the TUI rename
                # popup, and codex blocks some ops once a task is running.
                # claude named itself at launch → session_rename_keys is [] → no-op.
                if session_name:
                    for text, enter in adapter.session_rename_keys(session_name):
                        tmux.send_keys(session, window, text, enter=enter)
                        time.sleep(RENAME_SETTLE_SECONDS)
                # Paste a short pointer to the prompt file, not the prompt
                # body — pasting the full body trips agent-CLI input quirks
                # (mixed-character corruption, see Issue #90).
                checkpoint = _event_checkpoint(state_dir / "events.jsonl", task_id)
                prompt_pointer.load_pointer_buffer(tmux, buffer_name, prompt_path)
                tmux.paste_buffer(session, window, buffer_name)
                acknowledged = _submit_and_wait_for_inbox_seen(
                    state_dir=state_dir,
                    task_id=task_id,
                    session=session,
                    window=window,
                    checkpoint=checkpoint,
                    deadline=deadline,
                    poll_interval=poll_interval,
                    adapter=adapter,
                )
            except tmux.TmuxError as e:
                _fail(state_dir, task_id, f"prompt deliverer cannot paste prompt: {e}", window)
                return 1
            if not acknowledged:
                _fail(
                    state_dir,
                    task_id,
                    "prompt deliverer pasted prompt but did not receive inbox_seen ack",
                    window,
                )
                return 1
            _mark_running_if_needed(state_dir, task_id)
            append_event(
                state_dir / "events.jsonl",
                "prompt_delivered",
                task_id=task_id,
                window=window,
                agent=agent_spec,
            )
            return 0

        if not gate_notified and adapter.gate.search(pane):
            gate_notified = True
            _awaiting_orders(state_dir, task_id, window, vendor)

        time.sleep(max(0.1, poll_interval))

    _fail(
        state_dir,
        task_id,
        f"prompt deliverer timed out after {timeout:g}s waiting for {vendor} readiness",
        window,
    )
    return 1


def _awaiting_orders(state_dir: Path, task_id: str, window: str, vendor: str) -> None:
    question = (
        f"{vendor} boot gate detected in task-{task_id} ({window}). "
        "Attach to the pane, clear the prompt/login/update gate, and the prompt "
        "deliverer will continue automatically."
    )
    try:
        task = state_mod.load_task(state_dir, task_id)
        task["status"] = "awaiting_orders"
        state_mod.save_task(state_dir, task_id, task)
    except FileNotFoundError:
        pass
    qpath = state_mod.task_dir(state_dir, task_id) / "questions.md"
    existing = qpath.read_text(encoding="utf-8") if qpath.exists() else ""
    qpath.write_text(existing + f"### {utcnow_iso()}\n\n{question}\n\n", encoding="utf-8")
    append_event(
        state_dir / "events.jsonl",
        "awaiting_orders",
        task_id=task_id,
        question=question,
        source="prompt_deliverer",
        window=window,
    )
    try:
        project = state_mod.load_project(state_dir)
    except FileNotFoundError:
        project = {"name": "?"}
    notify.send(
        state_dir,
        title=f"fleet {project.get('name', '?')}: task-{task_id} boot gate",
        message=question,
        level="error",
    )


def _fail(state_dir: Path, task_id: str, message: str, window: str) -> None:
    try:
        task = state_mod.load_task(state_dir, task_id)
        task["status"] = "failed"
        # Terminal transition: record per-task token usage before persisting.
        state_mod.record_task_usage(task, state_dir=state_dir, task_id=task_id)
        state_mod.save_task(state_dir, task_id, task)
    except FileNotFoundError:
        pass
    append_event(
        state_dir / "events.jsonl",
        "error",
        task_id=task_id,
        source="prompt_deliverer",
        window=window,
        message=message,
    )


def _mark_running_if_needed(state_dir: Path, task_id: str) -> None:
    try:
        task = state_mod.load_task(state_dir, task_id)
    except FileNotFoundError:
        return
    if task.get("status") == "awaiting_orders":
        task["status"] = state_mod.derive_task_status(task.get("stages") or [])
        state_mod.save_task(state_dir, task_id, task)


def _submit_and_wait_for_inbox_seen(
    *,
    state_dir: Path,
    task_id: str,
    session: str,
    window: str,
    checkpoint: EventCheckpoint,
    deadline: float,
    poll_interval: float,
    adapter: type[VendorAdapter],
) -> bool:
    """Press Enter after paste settles, then wait for the driver inbox-read ack.

    Some vendors (codex) intermittently drop the bare submit Enter, leaving
    the pasted pointer in the composer unsubmitted. When ``adapter`` asks for
    submit retries, the Enter is re-pressed every
    ``submit_retry_interval_seconds`` until the ack lands — a no-op once the
    prompt has already submitted, so it never double-submits. claude sets
    ``submit_retries=0`` and keeps the single-Enter behaviour.
    """
    time.sleep(PASTE_SETTLE_SECONDS)
    tmux.send_keys(session, window, "", enter=True)

    events_path = state_dir / "events.jsonl"
    offset = checkpoint.offset
    retries_left = adapter.submit_retries
    next_retry_at = time.monotonic() + adapter.submit_retry_interval_seconds
    while time.monotonic() <= deadline:
        matched, offset = _scan_for_inbox_seen_ack(events_path, task_id, checkpoint, offset)
        if matched:
            return True
        if retries_left > 0 and time.monotonic() >= next_retry_at:
            tmux.send_keys(session, window, "", enter=True)
            retries_left -= 1
            next_retry_at = time.monotonic() + adapter.submit_retry_interval_seconds
        time.sleep(max(0.1, poll_interval))

    return False


def _event_checkpoint(events_path: Path, task_id: str) -> EventCheckpoint:
    """Return this task's latest event timestamp and the log offset before paste."""
    if not events_path.exists():
        return EventCheckpoint(ts="", offset=0)

    latest_ts = ""
    offset = 0
    with events_path.open("rb") as f:
        for raw in f:
            offset += len(raw)
            try:
                event = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if event.get("task_id") == task_id:
                latest_ts = max(latest_ts, str(event.get("ts") or ""))
    return EventCheckpoint(ts=latest_ts, offset=offset)


def _scan_for_inbox_seen_ack(
    events_path: Path,
    task_id: str,
    checkpoint: EventCheckpoint,
    offset: int,
) -> tuple[bool, int]:
    if not events_path.exists():
        return False, 0

    with events_path.open("rb") as f:
        try:
            f.seek(offset)
        except OSError:
            f.seek(0)
        new_offset = f.tell()
        for raw in f:
            new_offset += len(raw)
            try:
                event = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            ts = str(event.get("ts") or "")
            if (
                event.get("type") == "inbox_seen"
                and event.get("task_id") == task_id
                and (ts > checkpoint.ts or (ts == checkpoint.ts and new_offset > checkpoint.offset))
            ):
                return True, new_offset
    return False, new_offset


def _fleet_clone_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "fleet-agent").exists() or (parent / ".git").is_dir():
            return parent
    return here.parent.parent.parent


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", required=True, type=Path)
    p.add_argument("--task-id", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--window", required=True)
    p.add_argument("--prompt-path", required=True, type=Path)
    p.add_argument("--buffer-name", required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--session-name", default=None)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    p.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    p.add_argument("--initial-delay", type=float, default=0.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return deliver(
        state_dir=args.state_dir,
        task_id=args.task_id,
        session=args.session,
        window=args.window,
        prompt_path=args.prompt_path,
        buffer_name=args.buffer_name,
        agent_spec=args.agent,
        session_name=args.session_name,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
        initial_delay=args.initial_delay,
    )


if __name__ == "__main__":
    raise SystemExit(main())
