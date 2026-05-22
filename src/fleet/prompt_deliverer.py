"""Detached driver-prompt delivery after an agent CLI reaches its prompt."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import agents, notify, prompt_pointer, state as state_mod, tmux
from .events import append_event, utcnow_iso


DEFAULT_TIMEOUT_SECONDS = 10 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class PromptAdapter:
    ready: re.Pattern[str]
    gate: re.Pattern[str]


ADAPTERS: dict[str, PromptAdapter] = {
    "claude": PromptAdapter(
        ready=re.compile(r"(?m)^\s*❯(?:\s|$)"),
        gate=re.compile(
            r"(?im)"
            r"(?:login|log in|sign in|authentication|authenticate|"
            r"^\s*1\.\s*Update now\b|trust (?:this )?(?:folder|directory|workspace)|"
            r"do you trust|continue\?)"
        ),
    ),
    "codex": PromptAdapter(
        ready=re.compile(r"(?im)^\s*›(?:\s|$)|ask codex|what can i help"),
        gate=re.compile(
            r"(?im)"
            r"(?:^\s*1\.\s*Update now\b|"
            r"do you trust the contents of this directory|yes,\s*continue|"
            r"sign in|login|log in|authentication|authenticate|api key)"
        ),
    ),
}


def start_detached(
    *,
    state_dir: Path,
    task_id: str,
    session: str,
    window: str,
    prompt_path: Path,
    buffer_name: str,
    agent_spec: str,
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
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    initial_delay: float = 0.0,
) -> int:
    vendor, _model = agents.parse_spec(agent_spec)
    adapter = ADAPTERS[vendor]
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
                # Paste a short pointer to the prompt file, not the prompt
                # body — pasting the full body trips agent-CLI input quirks
                # (mixed-character corruption, see Issue #90).
                prompt_pointer.load_pointer_buffer(tmux, buffer_name, prompt_path)
                tmux.paste_buffer(session, window, buffer_name)
                tmux.send_keys(session, window, "", enter=True)
            except tmux.TmuxError as e:
                _fail(state_dir, task_id, f"prompt deliverer cannot paste prompt: {e}", window)
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
            _needs_input(state_dir, task_id, window, vendor)

        time.sleep(max(0.1, poll_interval))

    _fail(
        state_dir,
        task_id,
        f"prompt deliverer timed out after {timeout:g}s waiting for {vendor} readiness",
        window,
    )
    return 1


def _needs_input(state_dir: Path, task_id: str, window: str, vendor: str) -> None:
    question = (
        f"{vendor} boot gate detected in task-{task_id} ({window}). "
        "Attach to the pane, clear the prompt/login/update gate, and the prompt "
        "deliverer will continue automatically."
    )
    try:
        task = state_mod.load_task(state_dir, task_id)
        task["status"] = "needs_input"
        state_mod.save_task(state_dir, task_id, task)
    except FileNotFoundError:
        pass
    qpath = state_mod.task_dir(state_dir, task_id) / "questions.md"
    existing = qpath.read_text(encoding="utf-8") if qpath.exists() else ""
    qpath.write_text(existing + f"### {utcnow_iso()}\n\n{question}\n\n", encoding="utf-8")
    append_event(
        state_dir / "events.jsonl",
        "needs_input",
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
    )


def _fail(state_dir: Path, task_id: str, message: str, window: str) -> None:
    try:
        task = state_mod.load_task(state_dir, task_id)
        task["status"] = "failed"
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
    if task.get("status") == "needs_input":
        task["status"] = state_mod.derive_task_status(task.get("stages") or [])
        state_mod.save_task(state_dir, task_id, task)


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
        timeout=args.timeout,
        poll_interval=args.poll_interval,
        initial_delay=args.initial_delay,
    )


if __name__ == "__main__":
    raise SystemExit(main())
