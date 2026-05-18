"""``fleet spawn`` — open a tmux window for a new driver task.

Phase 3b scope: spawn **one** driver, picked from the topology's first
role/stage/candidate (unless ``--role`` overrides). True multi-role and
race orchestration come later.
"""
from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Any

from .. import agents as agents_mod
from .. import driver_prompt as dp
from .. import plugins as plugins_mod
from .. import state as state_mod
from .. import topology as topology_mod
from .. import tmux as tmux_mod
from ..events import append_event


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "spawn",
        help="Spawn a driver for a new task",
        description=(
            "Create the task's state, render driver-prompt.md, and open a "
            "tmux window running the chosen agent. With --dry-run the "
            "tmux step is skipped (state is still written)."
        ),
    )
    p.add_argument("task_id", help="Unique task id within the project")
    p.add_argument(
        "description",
        help="Task description (becomes the body of driver-prompt.md)",
    )
    p.add_argument(
        "--project",
        default=".",
        help="Project path (default: cwd; .fleet-state/ is discovered upward)",
    )
    p.add_argument(
        "--topology",
        default="solo",
        help="Topology name (preset or custom). Default: solo.",
    )
    p.add_argument(
        "--role",
        default=None,
        help="Role to spawn within the topology (default: first role/stage)",
    )
    p.add_argument(
        "--agent",
        default=None,
        help="Override the topology's agent for this role (e.g. claude:sonnet)",
    )
    p.add_argument(
        "--title",
        default=None,
        help="Short title for the dashboard (default: first line of description)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Write state but don't touch tmux. Useful in CI / tests.",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    state_dir = state_mod.discover_state_dir(Path(args.project))
    if state_dir is None:
        print(
            f"error: no .fleet-state/ found under {Path(args.project).resolve()}",
            file=sys.stderr,
        )
        return 1

    task_dir = state_mod.task_dir(state_dir, args.task_id)
    if task_dir.exists():
        print(f"error: task-{args.task_id} already exists at {task_dir}", file=sys.stderr)
        return 1

    # Topology → role → agent
    try:
        topo = topology_mod.load(args.topology, state_dir=state_dir)
        topology_mod.validate(topo)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        role_name, agent_spec = _pick_role_and_agent(topo, args.role, args.agent)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Validate agent spec early
    try:
        agents_mod.parse_spec(agent_spec)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Build state artifacts
    title = args.title or args.description.splitlines()[0][:80]

    # Workflow plugin: pre_spawn hook can attach extra task fields and/or
    # override the spawn window's cwd (e.g. git_worktree points it at the
    # freshly-created worktree).
    workflow = plugins_mod.load_workflow(state_dir)
    ctx: dict = {
        "state_dir": state_dir,
        "task_id": args.task_id,
        "topology": args.topology,
        "role": role_name,
        "agent": agent_spec,
        "description": args.description,
        "title": title,
        "project_root": state_dir.parent,
        "dry_run": bool(args.dry_run),
    }
    try:
        plugins_mod.run_hook(workflow, "on_pre_spawn", ctx)
    except Exception as e:  # noqa: BLE001 — plugin errors are reportable
        print(f"error: workflow pre_spawn failed: {e}", file=sys.stderr)
        return 1

    task_data = {
        "id": args.task_id,
        "title": title,
        "status": "spawning",
        "agent": agent_spec,
        "topology": args.topology,
        "role": role_name,
        "progress": "0/0",
        "workflow": getattr(workflow, "WORKFLOW_NAME", "bare"),
    }
    task_data.update(ctx.get("task_extra", {}))
    state_mod.save_task(state_dir, args.task_id, task_data)

    task_dir = state_mod.task_dir(state_dir, args.task_id)
    (task_dir / "inbox.md").write_text("")
    (task_dir / "outbox.md").write_text("")
    prompt = dp.render(
        task_id=args.task_id,
        description=args.description,
        topology_name=args.topology,
        role=role_name,
        agent=agent_spec,
    )
    (task_dir / "driver-prompt.md").write_text(prompt)

    project = state_mod.load_project(state_dir)
    project_name = project.get("name", "?")
    append_event(
        state_dir / "events.jsonl",
        "spawn",
        task_id=args.task_id,
        topology=args.topology,
        role=role_name,
        agent=agent_spec,
        dry_run=bool(args.dry_run),
    )

    print(f"task-{args.task_id} prepared:")
    print(f"  state dir:      {state_dir}")
    print(f"  driver-prompt:  {task_dir / 'driver-prompt.md'}")
    print(f"  agent:          {agent_spec}")
    print(f"  role:           {role_name}")
    print(f"  topology:       {args.topology}")

    if args.dry_run:
        print("dry-run: tmux step skipped.")
        return 0

    if not tmux_mod.available():
        print(
            "warn: tmux not on PATH; skipping window creation. "
            "Re-run with tmux installed, or use --dry-run.",
            file=sys.stderr,
        )
        return 0

    session = f"fleet-{project_name}"
    if not tmux_mod.session_exists(session):
        tmux_mod.new_session(session)
    window = f"task-{args.task_id}"
    window_cwd = ctx.get("cwd") or task_dir
    try:
        tmux_mod.new_window(session, window, cwd=str(window_cwd))
        # Launch the agent CLI; the prompt path is passed as an arg-style hint.
        cli = agents_mod.cli_command(agent_spec)
        cli_quoted = " ".join(shlex.quote(p) for p in cli)
        tmux_mod.send_keys(session, window, cli_quoted)
        # Paste the driver prompt as the first message after the CLI is up.
        # For MVP we keep this naive: a short sleep would help in practice;
        # tuning belongs to a later phase.
        tmux_mod.send_keys(
            session,
            window,
            f"cat {shlex.quote(str(task_dir / 'driver-prompt.md'))} | head -100",
        )
    except tmux_mod.TmuxError as e:
        print(f"warn: tmux setup partially failed: {e}", file=sys.stderr)
        return 0

    print(f"tmux: session={session} window={window}")
    print(f"attach: tmux attach -t {session}:{window}")
    return 0


def _pick_role_and_agent(
    topo: dict[str, Any],
    role_override: str | None,
    agent_override: str | None,
) -> tuple[str, str]:
    """Resolve which role to start and what agent runs it.

    Looks in ``roles`` → ``stages`` → ``candidates`` in that order.
    Raises ``ValueError`` if nothing matches.
    """
    entries: list[dict[str, Any]] = []
    for key in ("roles", "stages"):
        if key in topo and topo[key]:
            entries = list(topo[key])
            break
    candidates_only = False
    if not entries and "candidates" in topo:
        # race shape — synthesize role names so the rest of the pipeline can flow.
        entries = [
            {"role": c.get("role", f"candidate{i}"), **c}
            for i, c in enumerate(topo["candidates"])
        ]
        candidates_only = True

    if not entries:
        raise ValueError("topology has no roles/stages/candidates to spawn")

    if role_override:
        match = next(
            (e for e in entries if e.get("role") == role_override),
            None,
        )
        if not match:
            available = sorted({e.get("role", "?") for e in entries})
            raise ValueError(
                f"role {role_override!r} not in topology; choices: {available}"
            )
        chosen = match
    else:
        chosen = entries[0]

    role_name = chosen.get("role") or ("candidate" if candidates_only else "driver")
    agent = agent_override or chosen.get("agent")
    if not agent:
        raise ValueError(
            f"no agent for role {role_name!r}; pass --agent or set one in the topology"
        )
    return role_name, agent
