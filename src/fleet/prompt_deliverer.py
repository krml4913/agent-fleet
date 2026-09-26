"""Detached driver-prompt delivery after an agent CLI reaches its prompt."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import agents, leader_notifier, mux, notify, prompt_pointer, state as state_mod
from .adapters import REGISTRY, VendorAdapter
from .events import append_event, utcnow_iso
from .proc import spawn_detached


DEFAULT_TIMEOUT_SECONDS = 10 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
PASTE_SETTLE_SECONDS = 0.25
# A MuxError from the multiplexer is transient (Issue #289: zellij briefly lists
# panes inconsistently while another tab is closed, docs/windows-support.md §4.8):
# retry with this doubling backoff until the delivery deadline. It only counts as
# fatal if it persists that long or the session is confirmed gone.
TRANSIENT_BACKOFF_INITIAL_SECONDS = 0.5
TRANSIENT_BACKOFF_MAX_SECONDS = 8.0
# ``source`` of this module's ``error`` events (also how a later recovery
# recognises a failure it caused itself).
DELIVERER_SOURCE = "prompt_deliverer"
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
    spawn_detached(args, cwd=repo_root, env=env, log_path=log_path)
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

    backend = mux.get()
    capture_error: mux.MuxError | None = None
    capture_backoff = TRANSIENT_BACKOFF_INITIAL_SECONDS
    while time.monotonic() <= deadline:
        try:
            pane = backend.capture(session, window)
        except mux.MuxError as e:
            # Transient unless the session is really gone (Issue #289): keep
            # polling with a backoff until the deadline.
            if leader_notifier.session_confirmed_gone(backend, session):
                _fail(
                    state_dir,
                    task_id,
                    f"prompt deliverer cannot capture pane: session {session} is gone: {e}",
                    window,
                )
                return 1
            if capture_error is None:
                _log(f"capture failed ({e}); retrying with backoff until the deadline")
            capture_error = e
            time.sleep(min(capture_backoff, max(0.1, deadline - time.monotonic())))
            capture_backoff = min(capture_backoff * 2, TRANSIENT_BACKOFF_MAX_SECONDS)
            continue
        if capture_error is not None:
            _log("capture recovered")
            capture_error = None
            capture_backoff = TRANSIENT_BACKOFF_INITIAL_SECONDS

        if adapter.is_ready(pane):
            try:
                # Name the session BEFORE pasting the prompt: for vendors with
                # no launch-time naming flag (codex), this drives the TUI rename
                # popup, and codex blocks some ops once a task is running.
                # claude named itself at launch → session_rename_keys is [] → no-op.
                if session_name:
                    for step, enter in adapter.session_rename_keys(session_name):
                        _retry_mux(
                            lambda step=step, enter=enter: mux.send_step(
                                session, window, step, enter=enter, backend=backend
                            ),
                            backend=backend,
                            session=session,
                            deadline=deadline,
                            what="rename step",
                        )
                        time.sleep(RENAME_SETTLE_SECONDS)
                # Paste a short pointer to the prompt file, not the prompt
                # body — pasting the full body trips agent-CLI input quirks
                # (mixed-character corruption, see Issue #90).
                checkpoint = _event_checkpoint(state_dir / "events.jsonl", task_id)
                _retry_mux(
                    lambda: prompt_pointer.paste_pointer(
                        backend, session=session, window=window, prompt_path=prompt_path
                    ),
                    backend=backend,
                    session=session,
                    deadline=deadline,
                    what="paste",
                )
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
            except mux.MuxError as e:
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

        if not gate_notified and adapter.is_gated(pane):
            gate_notified = True
            _awaiting_orders(
                state_dir, task_id, window, vendor, trust=adapter.is_trust_gate(pane)
            )

        time.sleep(max(0.1, poll_interval))

    if capture_error is not None:
        _fail(
            state_dir,
            task_id,
            f"prompt deliverer cannot capture pane (still failing after {timeout:g}s): "
            f"{capture_error}",
            window,
        )
        return 1
    _fail(
        state_dir,
        task_id,
        f"prompt deliverer timed out after {timeout:g}s waiting for {vendor} readiness",
        window,
    )
    return 1


def _awaiting_orders(
    state_dir: Path, task_id: str, window: str, vendor: str, *, trust: bool = False
) -> None:
    try:
        project = state_mod.load_project(state_dir)
    except FileNotFoundError:
        project = {"name": "?"}
    if trust:
        # The dialog is the one gate with a known fix: name it, so the reason is
        # not "boot gate" (fleet never answers it; a human confirms once).
        question = (
            f"{vendor} is waiting on its workspace trust prompt in task-{task_id} "
            f"({window}). Attach to the pane and choose the \"Yes\" option to trust the "
            "folder (fleet does not answer it); the prompt deliverer will continue "
            "automatically."
        )
        repo = project.get("repo")
        if repo:
            question += f" To avoid this next time, run `{vendor}` once in {repo} and accept it."
        title = f"fleet {project.get('name', '?')}: task-{task_id} waiting on {vendor} trust prompt"
    else:
        question = (
            f"{vendor} boot gate detected in task-{task_id} ({window}). "
            "Attach to the pane, clear the prompt/login/update gate, and the prompt "
            "deliverer will continue automatically."
        )
        title = f"fleet {project.get('name', '?')}: task-{task_id} boot gate"
    try:
        task = state_mod.load_task(state_dir, task_id)
        task["status"] = "awaiting_orders"
        state_mod.save_task(state_dir, task_id, task)
    except FileNotFoundError:
        pass
    qpath = state_mod.task_dir(state_dir, task_id) / "questions.md"
    existing = qpath.read_text(encoding="utf-8") if qpath.exists() else ""
    qpath.write_text(existing + f"### {utcnow_iso()}\n\n{question}\n\n", encoding="utf-8")
    extra = {"gate": "trust"} if trust else {}
    append_event(
        state_dir / "events.jsonl",
        "awaiting_orders",
        task_id=task_id,
        question=question,
        source="prompt_deliverer",
        window=window,
        **extra,
    )
    notify.send(state_dir, title=title, message=question, level="error")


def _log(message: str) -> None:
    """One timestamped line on stderr, i.e. in ``prompt-deliverer.log`` when detached."""
    print(f"{utcnow_iso()} [pid {os.getpid()}] {message}", file=sys.stderr, flush=True)


def _retry_mux(op, *, backend, session: str, deadline: float, what: str):
    """Run ``op()``; a ``MuxError`` is transient: back off and retry until ``deadline``.

    Raises the (annotated) ``MuxError`` only when it persisted to the deadline or the
    session is confirmed gone (re-checked, see :func:`leader_notifier.session_confirmed_gone`).
    """
    delay = TRANSIENT_BACKOFF_INITIAL_SECONDS
    attempt = 0
    while True:
        try:
            result = op()
        except mux.MuxError as e:
            attempt += 1
            if leader_notifier.session_confirmed_gone(backend, session):
                raise mux.MuxError(f"session {session} is gone: {e}") from e
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise mux.MuxError(
                    f"{e} (still failing at the delivery deadline after {attempt} attempts)"
                ) from e
            _log(f"{what} failed ({e}); attempt {attempt}, retrying in {min(delay, remaining):g}s")
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, TRANSIENT_BACKOFF_MAX_SECONDS)
            continue
        if attempt:
            _log(f"{what} recovered after {attempt} failed attempt(s)")
        return result


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
        source=DELIVERER_SOURCE,
        window=window,
        message=message,
    )
    _push_failure_to_leader(state_dir, task_id, message)


def _push_failure_to_leader(state_dir: Path, task_id: str, message: str) -> None:
    """Opt-in (``notify_leader_on_driver_done``): tell the owning leader delivery failed.

    Without this a failed delivery leaves an idle driver with no prompt until
    someone happens to look at ``fleet status`` (Issue #289). Best-effort: it must
    never turn the failure report itself into a crash.
    """
    try:
        task = state_mod.load_task(state_dir, task_id)
        project = state_mod.load_project(state_dir)
        leader_notifier.push_to_leader(
            state_dir,
            task_id,
            task,
            project,
            project.get("name", "?"),
            status="failed",
            summary=message,
            kind=leader_notifier.KIND_DELIVERY_FAILED,
        )
    except Exception:  # noqa: BLE001 - reporting must not break the failure path
        pass


def _last_error_source(state_dir: Path, task_id: str) -> str | None:
    """``source`` of the most recent ``error`` event of ``task_id``, if any."""
    events_path = state_dir / "events.jsonl"
    if not events_path.exists():
        return None
    source: str | None = None
    with events_path.open("rb") as f:
        for raw in f:
            try:
                event = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if event.get("type") == "error" and event.get("task_id") == task_id:
                source = event.get("source")
    return source


def _mark_running_if_needed(state_dir: Path, task_id: str) -> None:
    """After a delivery: lift ``awaiting_orders`` (boot gate) or our own ``failed``.

    A task this deliverer marked ``failed`` (Issue #289) and that was later
    delivered anyway (e.g. ``fleet-agent send-prompt`` recovery) goes back to the
    status derived from its stages, with a ``prompt_delivery_recovered`` event. A
    ``failed`` status from anywhere else is left alone.
    """
    try:
        task = state_mod.load_task(state_dir, task_id)
    except FileNotFoundError:
        return
    status = task.get("status")
    if status == "awaiting_orders":
        task["status"] = state_mod.derive_task_status(task.get("stages") or [])
        state_mod.save_task(state_dir, task_id, task)
    elif status == "failed" and _last_error_source(state_dir, task_id) == DELIVERER_SOURCE:
        task["status"] = state_mod.derive_task_status(task.get("stages") or [])
        state_mod.save_task(state_dir, task_id, task)
        append_event(
            state_dir / "events.jsonl",
            "prompt_delivery_recovered",
            task_id=task_id,
            source=DELIVERER_SOURCE,
            previous_status="failed",
            status=task["status"],
        )


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
    backend = mux.get()
    time.sleep(PASTE_SETTLE_SECONDS)
    _retry_mux(
        lambda: backend.send_key(session, window, "Enter"),
        backend=backend,
        session=session,
        deadline=deadline,
        what="submit Enter",
    )

    events_path = state_dir / "events.jsonl"
    offset = checkpoint.offset
    retries_left = adapter.submit_retries
    next_retry_at = time.monotonic() + adapter.submit_retry_interval_seconds
    while time.monotonic() <= deadline:
        matched, offset = _scan_for_inbox_seen_ack(events_path, task_id, checkpoint, offset)
        if matched:
            return True
        if retries_left > 0 and time.monotonic() >= next_retry_at:
            try:
                backend.send_key(session, window, "Enter")
                retries_left -= 1
            except mux.MuxError as e:
                # The pointer is already pasted: a failed resubmit does not use
                # up a retry and is tried again next interval; the ack wait
                # still bounds the delivery.
                _log(f"resubmit Enter failed ({e}); will retry")
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
        agent_spec=args.agent,
        session_name=args.session_name,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
        initial_delay=args.initial_delay,
    )


if __name__ == "__main__":
    raise SystemExit(main())
