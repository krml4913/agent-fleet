"""``fleet leader --respawn``: restart only the leader agent of a live session.

The drivers of a leader session live in the same multiplexer session as its
leader, so "kill the session and run ``fleet leader`` again" is not an option
once any task runs. A respawn replaces just the leader window:

1. recover a renamed leader window by its pane title (as the notifier does),
2. open a new window running exactly the command line a fresh ``fleet leader``
   would use (:func:`fleet.commands.leader.leader_launch`, incl. the #329 relay
   flags), under a temporary name so the session is never left windowless,
3. update session.json (agent / agent_name / delivery) through the state
   writer, keeping ``scope`` and ``started_at``,
4. close the old leader window, rename the new one to ``leader``,
5. re-paste the leader prompt.

Driver windows and the ``leader-pending.jsonl`` queue are not touched.

Step 4 closes the window the command may be running in (the human types it in
the leader pane after quitting the agent, or asks the leader to run it). So
when the caller runs inside the session, the work is handed to a detached
helper (``python -m fleet.leader_respawn``) that waits for the calling process
to exit first and logs to ``global/sessions/<label>/leader-respawn.log``.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import agents as agents_mod
from . import config as config_mod
from . import mux
from . import state as state_mod
from .events import append_event
from .proc import spawn_detached

LEADER_WINDOW = "leader"
#: The new leader window's name until the old one is closed.
TEMP_WINDOW = "leader-respawn"
LOG_NAME = "leader-respawn.log"

#: How long the detached helper waits for the calling command to exit.
DEFAULT_WAIT_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.2


def _clone_root() -> Path:
    # src/fleet/leader_respawn.py → parents[0]=fleet, [1]=src, [2]=clone root
    return Path(__file__).resolve().parents[2]


def called_from_session(label: str, session: str) -> bool:
    """True when this process (probably) runs inside the session's panes.

    A false positive only costs running detached, so any of: the leader pane's
    ``FLEET_SESSION``, zellij's ``ZELLIJ_SESSION_NAME``, or the tmux session
    of ``TMUX_PANE``.
    """
    if os.environ.get("FLEET_SESSION") == label:
        return True
    if os.environ.get("ZELLIJ_SESSION_NAME") == session:
        return True
    pane = os.environ.get("TMUX_PANE")
    if pane:
        try:
            r = subprocess.run(
                ["tmux", "display-message", "-p", "-t", pane, "#S"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0 and r.stdout.strip() == session
    return False


def run_cli(args: argparse.Namespace, label: str) -> int:
    """``fleet leader --respawn``: validate, then respawn here or detached."""
    session = f"fleet-{label}"
    m = mux.get()
    if not m.available():
        print(f"error: {m.name} not on PATH", file=sys.stderr)
        return 1
    if not m.session_exists(session):
        print(
            f"error: no leader session {session}; start one with: fleet leader --name {label}",
            file=sys.stderr,
        )
        return 1

    # Precedence: --agent > the agent this session was started with > the
    # 'leader_agent' config / built-in default (what a fresh launch would use).
    record = state_mod.read_session_record(label) or {}
    agent_arg = (
        getattr(args, "agent", None)
        or record.get("agent_alias")
        or record.get("agent")
        or config_mod.get("leader_agent")[0]
    )
    agent_arg = str(agent_arg)
    agent_alias = agent_arg.strip() if agents_mod.is_alias(agent_arg) else None
    try:
        agent_spec = agents_mod.resolve_spec(agent_arg)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    scope_names: list[str] = []
    scope_arg = getattr(args, "scope", None)
    if scope_arg:
        scope_names = [n.strip() for n in scope_arg.split(",") if n.strip()]
        try:
            state_mod.validate_registered_projects(scope_names)
        except ValueError as e:
            print(f"error: --scope: {e}", file=sys.stderr)
            return 1

    auto_paste = bool(getattr(args, "auto_paste", True))
    prompt_delay = float(getattr(args, "prompt_delay", 3.0))

    if called_from_session(label, session):
        log_path = start_detached(
            label=label,
            agent_spec=agent_spec,
            agent_alias=agent_alias,
            auto_paste=auto_paste,
            prompt_delay=prompt_delay,
            scope_names=scope_names,
            wait_pid=os.getpid(),
        )
        print(f"leader respawn scheduled: session={session}, agent={agent_spec}")
        print("  (running detached: this window is replaced once this command exits)")
        print(f"  log: {log_path}")
        return 0

    rc = respawn(
        m,
        label,
        agent_spec=agent_spec,
        agent_alias=agent_alias,
        auto_paste=auto_paste,
        prompt_delay=prompt_delay,
        scope_names=scope_names,
    )
    if rc == 0 and getattr(args, "attach", False) is True:
        try:
            return m.attach(session, LEADER_WINDOW)
        except mux.MuxError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    return rc


def respawn(
    m: mux.Mux,
    label: str,
    *,
    agent_spec: str,
    agent_alias: str | None = None,
    auto_paste: bool = True,
    prompt_delay: float = 3.0,
    scope_names: list[str] | None = None,
) -> int:
    """Replace the leader window of ``fleet-<label>`` (see the module docstring)."""
    from .commands import leader as leader_cmd
    from .leader_notifier import heal_leader_window

    session = f"fleet-{label}"
    session_dir = state_mod.session_dir(label)
    session_dir.mkdir(parents=True, exist_ok=True)
    if not m.session_exists(session):
        print(f"error: no leader session {session}", file=sys.stderr)
        return 1

    # A leader window the human renamed is found by its pane title — before the
    # new leader (same title) exists, or the match would be ambiguous.
    _healed, note = heal_leader_window(m, session, LEADER_WINDOW, session_dir)
    if note:
        print(f"note: {note}")

    cli, delivery, agent_name = leader_cmd.leader_launch(agent_spec, label)
    try:
        windows = m.list_windows(session)
        if TEMP_WINDOW in windows:
            # Left over from an interrupted respawn.
            m.kill_window(session, TEMP_WINDOW)
        m.new_window(
            session,
            TEMP_WINDOW,
            argv=cli,
            cwd=str(leader_cmd._CLONE_ROOT),
            env=leader_cmd.leader_env(label),
        )
    except mux.MuxError as e:
        print(f"error: could not start the new leader window: {e}", file=sys.stderr)
        return 1

    state_mod.update_session_record(
        label,
        {
            "agent": agent_spec,
            **({"agent_alias": agent_alias} if agent_alias else {}),
            "pane": f"{session}:{LEADER_WINDOW}",
            "agent_name": agent_name,
            "delivery": delivery,
            "respawned_at": datetime.now(timezone.utc).isoformat(),
        },
        drop=() if agent_alias else ("agent_alias",),
    )
    if scope_names:
        state_mod.set_session_scope(label, scope_names, mode="set")

    try:
        if LEADER_WINDOW in windows:
            m.kill_window(session, LEADER_WINDOW)
        new_ids = [p.window_id for p in m.list_panes(session) if p.window == TEMP_WINDOW]
        if not new_ids:
            raise mux.MuxError(f"new leader window {TEMP_WINDOW!r} is gone")
        m.rename_window(session, new_ids[0], LEADER_WINDOW)
    except mux.MuxError as e:
        print(
            f"error: new leader started in window {TEMP_WINDOW!r} but the swap failed: {e}",
            file=sys.stderr,
        )
        return 1

    if auto_paste:
        leader_cmd.paste_leader_prompt(
            m, session, label, agent_spec, agent_name, prompt_delay=prompt_delay
        )

    append_event(
        session_dir / "events.jsonl",
        "leader_respawn",
        agent=agent_spec,
        session=session,
        label=label,
        delivery=delivery,
    )
    print(f"leader respawned: session={session}, agent={agent_spec}, delivery={delivery}")
    print(f"  attach: {m.attach_hint(session)}")
    return 0


# ---------------------------------------------------------------------------
# Detached helper
# ---------------------------------------------------------------------------


def start_detached(
    *,
    label: str,
    agent_spec: str,
    agent_alias: str | None,
    auto_paste: bool,
    prompt_delay: float,
    scope_names: list[str],
    wait_pid: int,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> Path:
    """Spawn ``python -m fleet.leader_respawn`` to respawn once ``wait_pid`` exits."""
    session_dir = state_mod.session_dir(label)
    session_dir.mkdir(parents=True, exist_ok=True)
    log_path = session_dir / LOG_NAME
    repo_root = _clone_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root / "src"), str(repo_root / "vendor"), env.get("PYTHONPATH", "")]
    )
    argv = [
        sys.executable,
        "-m",
        "fleet.leader_respawn",
        "--label",
        label,
        "--agent",
        agent_spec,
        "--prompt-delay",
        str(prompt_delay),
        "--wait-pid",
        str(wait_pid),
        "--wait-seconds",
        str(wait_seconds),
    ]
    if agent_alias:
        argv += ["--agent-alias", agent_alias]
    if not auto_paste:
        argv.append("--no-auto-paste")
    if scope_names:
        argv += ["--scope", ",".join(scope_names)]
    spawn_detached(argv, cwd=repo_root, env=env, log_path=log_path)
    return log_path


def _wait_for_exit(pid: int, timeout: float) -> bool:
    from .pane_launch import pid_alive

    deadline = time.monotonic() + max(0.0, timeout)
    while pid_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL_SECONDS)
    return True


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fleet.leader_respawn")
    p.add_argument("--label", required=True)
    p.add_argument("--agent", required=True, help="resolved agent spec")
    p.add_argument("--agent-alias", default=None)
    p.add_argument("--no-auto-paste", dest="auto_paste", action="store_false")
    p.add_argument("--prompt-delay", type=float, default=3.0)
    p.add_argument("--scope", default=None)
    p.add_argument("--wait-pid", type=int, default=0)
    p.add_argument("--wait-seconds", type=float, default=DEFAULT_WAIT_SECONDS)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(f"[{datetime.now(timezone.utc).isoformat()}] respawn {args.label}", flush=True)
    if args.wait_pid > 0 and not _wait_for_exit(args.wait_pid, args.wait_seconds):
        print(
            f"warn: pid {args.wait_pid} still running after {args.wait_seconds:g}s; "
            "respawning anyway",
            flush=True,
        )
    scope = [n for n in (args.scope or "").split(",") if n]
    rc = respawn(
        mux.get(),
        args.label,
        agent_spec=args.agent,
        agent_alias=args.agent_alias,
        auto_paste=args.auto_paste,
        prompt_delay=args.prompt_delay,
        scope_names=scope,
    )
    sys.stdout.flush()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
