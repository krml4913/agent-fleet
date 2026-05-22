"""``fleet-agent start`` — start a task (create state + launch first stage driver).

The first stage is always launched; ``--role`` is not supported.
Shared ``launch_stage_driver()`` is called by the orchestrator for subsequent stages.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

from .. import agents as agents_mod
from .. import driver_prompt as dp
from .. import plugins as plugins_mod
from .. import prompt_pointer
from .. import state as state_mod
from .. import formation as formation_mod
from .. import tmux as tmux_mod
from ..events import append_event


def _fleet_clone_root() -> Path:
    """Return the agent-fleet clone root (where fleet-agent script lives)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "fleet-agent").exists() or (parent / ".git").is_dir():
            return parent
    return here.parent.parent.parent.parent


def _git_toplevel(cwd: Path) -> Path | None:
    """Return git toplevel for ``cwd``, or None when cwd is not in a git repo."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    root = r.stdout.strip()
    return Path(root).resolve() if root else None


def _guard_codex_trust(vendor: str, state_dir: Path, project_root: Path | None = None) -> int | None:
    if vendor != "codex":
        return None

    repo_root = _git_toplevel(project_root or state_dir.parent)
    if repo_root is None:
        return None
    if agents_mod.codex_repo_trusted(repo_root):
        return None

    print(
        f"error: codex はこの repo ({repo_root}) を信頼していません。\n"
        "       codex driver を起動する前に、一度このディレクトリで `codex` を起動し\n"
        "       「1. Yes, continue」で承認してください。その後 start を再実行してください。",
        file=sys.stderr,
    )
    return 1


def launch_stage_driver(
    *,
    state_dir: Path,
    task_id: str,
    task_dir: Path,
    stage_idx: int,
    stage: dict,
    project_name: str,
    auto_paste: bool = True,
    prompt_delay: float = 3.0,
    window_cwd: Path | None = None,
) -> int:
    """Open a tmux window for a specific stage driver.

    Shared between ``start`` (first stage) and the orchestrator (later stages).
    Expects the task directory and driver-prompt.md to already exist.
    """
    agent_spec = stage.get("agent", "")
    role_name = stage.get("role", "driver")

    prompt_path = task_dir / "driver-prompt.md"
    buffer_name = f"fleet-task-{task_id}"

    session = f"fleet-{project_name}"
    if not tmux_mod.session_exists(session):
        tmux_mod.new_session(session)
    window = f"{task_id}·{role_name}"
    effective_cwd = window_cwd or task_dir

    try:
        import os

        repo_root = _fleet_clone_root()
        driver_env = {
            "FLEET_TASK_ID": task_id,
            "FLEET_STATE_DIR": str(state_dir),
            "PATH": f"{repo_root}:{os.environ.get('PATH', '')}",
        }
        tmux_mod.kill_task_windows(session, task_id)
        tmux_mod.new_window(session, window, cwd=str(effective_cwd), env=driver_env)
        prompt_pointer.load_pointer_buffer(tmux_mod, buffer_name, prompt_path)

        cli = agents_mod.cli_command(agent_spec)
        cli_quoted = " ".join(shlex.quote(p) for p in cli)
        tmux_mod.send_keys(session, window, cli_quoted)

        if auto_paste:
            import time

            time.sleep(max(0.0, prompt_delay))
            tmux_mod.paste_buffer(session, window, buffer_name)
            time.sleep(0.8)
            tmux_mod.send_keys(session, window, "", enter=True)
    except tmux_mod.TmuxError as e:
        print(f"warn: tmux setup partially failed: {e}", file=sys.stderr)
        return 0

    print(f"tmux: session={session} window={window}")
    print(f"attach:        tmux attach -t {session}:{window}")
    if not auto_paste:
        print(f"paste pointer: inside the pane press C-b ], then Enter")
        print(f"           or: fleet-agent send-prompt {task_id}")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "start",
        help="Start a new task",
        description=(
            "Create the task's state, render driver-prompt.md, and open a "
            "tmux window running the first stage's agent. With --dry-run the "
            "tmux step is skipped (state is still written)."
        ),
    )
    p.add_argument("task_id", help="Unique task id within the project")
    p.add_argument(
        "description",
        nargs="?",
        help="Task description (becomes the body of driver-prompt.md)",
    )
    p.add_argument(
        "--prompt-file",
        metavar="PATH",
        help="Read the task description from PATH instead of a positional argument",
    )
    p.add_argument(
        "--project",
        default=".",
        help="Project name (default: resolved from cwd via registry)",
    )
    p.add_argument(
        "--formation",
        default="solo",
        help="Formation name (preset or custom). Default: solo.",
    )
    p.add_argument(
        "--agent",
        default=None,
        help="Override the formation's agent for the first stage (e.g. claude:sonnet)",
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
    p.add_argument(
        "--no-auto-paste",
        action="store_false",
        dest="auto_paste",
        help=(
            "Disable the default auto-paste of the driver-prompt pointer into the pane. "
            "The pointer is still preloaded into a tmux buffer for manual paste "
            "(C-b ] then Enter, or fleet-agent send-prompt)."
        ),
    )
    p.set_defaults(auto_paste=True)
    p.add_argument(
        "--prompt-delay",
        type=float,
        default=3.0,
        metavar="SEC",
        help="Seconds to wait before auto-paste (default: 3)",
    )
    p.set_defaults(func=run)


def _resolve_description(args: argparse.Namespace) -> str | None:
    description = getattr(args, "description", None)
    prompt_file = getattr(args, "prompt_file", None)
    has_description = description is not None
    has_prompt_file = prompt_file is not None

    if has_description and has_prompt_file:
        print("error: pass either description or --prompt-file, not both", file=sys.stderr)
        return None
    if not has_description and not has_prompt_file:
        print("error: pass either description or --prompt-file", file=sys.stderr)
        return None
    if has_prompt_file:
        path = Path(prompt_file)
        try:
            return path.read_text()
        except OSError as e:
            print(f"error: cannot read --prompt-file {path}: {e}", file=sys.stderr)
            return None
    return description


def run(args: argparse.Namespace) -> int:
    description = _resolve_description(args)
    if description is None:
        return 1

    project_arg = getattr(args, "project", ".")
    project_name = project_arg if project_arg != "." else None
    state_dir = state_mod.resolve_state_dir(Path.cwd(), project_name=project_name)
    if state_dir is None:
        print(
            f"error: no registered project found for {project_arg!r}",
            file=sys.stderr,
        )
        return 1

    task_dir_path = state_mod.task_dir(state_dir, args.task_id)
    if task_dir_path.exists():
        print(f"error: task-{args.task_id} already exists at {task_dir_path}", file=sys.stderr)
        return 1

    # Load and validate formation
    try:
        formation_data = formation_mod.load(args.formation, state_dir=state_dir)
        formation_mod.validate(formation_data)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Expand formation stages into task.yaml format (all status: pending initially)
    expanded_stages = formation_mod.expand_stages(formation_data)
    if not expanded_stages:
        print("error: formation has no stages to start", file=sys.stderr)
        return 1

    # Always start the first stage; --role is not supported
    current_stage_idx = 0

    # Apply --agent override to the first stage
    if args.agent:
        expanded_stages[current_stage_idx]["agent"] = args.agent

    current_stage = expanded_stages[current_stage_idx]
    agent_spec = current_stage.get("agent", "")
    role_name = current_stage.get("role", "driver")

    if not agent_spec:
        print(
            f"error: no agent for role {role_name!r}; pass --agent or set one in the formation",
            file=sys.stderr,
        )
        return 1

    try:
        vendor, _model = agents_mod.parse_spec(agent_spec)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Resolve project_root from project.yaml before the codex trust check.
    try:
        _pre_project = state_mod.load_project(state_dir)
        _project_root_for_trust = Path(_pre_project.get("repo", str(state_dir.parent)))
    except FileNotFoundError:
        _project_root_for_trust = None

    guard_rc = _guard_codex_trust(vendor, state_dir, _project_root_for_trust)
    if guard_rc is not None:
        return guard_rc

    # Mark current stage as running
    expanded_stages[current_stage_idx]["status"] = "running"

    title = args.title or (description.splitlines() or [""])[0][:80]

    # Workflow plugin: on_pre_start hook can attach extra task fields and/or
    # override the window cwd (e.g. git_worktree creates the worktree here).
    workflow = plugins_mod.load_workflow(state_dir)
    ctx: dict = {
        "state_dir": state_dir,
        "task_id": args.task_id,
        "formation": args.formation,
        "role": role_name,
        "agent": agent_spec,
        "description": description,
        "title": title,
        "project_root": _project_root_for_trust or state_dir.parent,
        "dry_run": bool(args.dry_run),
    }
    try:
        plugins_mod.run_hook(workflow, "on_pre_start", ctx)
    except Exception as e:  # noqa: BLE001 — plugin errors are reportable
        print(f"error: workflow on_pre_start failed: {e}", file=sys.stderr)
        return 1

    task_data: dict = {
        "id": args.task_id,
        "title": title,
        "description": description,
        "status": "spawning",
        "formation": args.formation,
        "workflow": getattr(workflow, "WORKFLOW_NAME", "bare"),
        "current_stage": current_stage_idx,
        "stages": expanded_stages,
    }
    task_data.update(ctx.get("task_extra", {}))
    state_mod.save_task(state_dir, args.task_id, task_data)

    task_dir_path = state_mod.task_dir(state_dir, args.task_id)
    (task_dir_path / "inbox.md").write_text("")
    (task_dir_path / "outbox.md").write_text("")
    prompt = dp.render(
        task_id=args.task_id,
        description=description,
        formation_name=args.formation,
        role=role_name,
        agent=agent_spec,
        workflow_fragment=getattr(workflow, "DRIVER_PROMPT_FRAGMENT", ""),
    )
    (task_dir_path / "driver-prompt.md").write_text(prompt)

    project_name = (_pre_project or {}).get("name", "?")
    append_event(
        state_dir / "events.jsonl",
        "start",
        task_id=args.task_id,
        formation=args.formation,
        role=role_name,
        agent=agent_spec,
        description=description,
        dry_run=bool(args.dry_run),
    )

    print(f"task-{args.task_id} prepared:")
    print(f"  state dir:      {state_dir}")
    print(f"  driver-prompt:  {task_dir_path / 'driver-prompt.md'}")
    print(f"  agent:          {agent_spec}")
    print(f"  role:           {role_name}")
    print(f"  formation:       {args.formation}")

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

    return launch_stage_driver(
        state_dir=state_dir,
        task_id=args.task_id,
        task_dir=task_dir_path,
        stage_idx=current_stage_idx,
        stage=current_stage,
        project_name=project_name,
        auto_paste=args.auto_paste,
        prompt_delay=args.prompt_delay,
        window_cwd=ctx.get("cwd"),
    )
