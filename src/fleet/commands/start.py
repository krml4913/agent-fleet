"""``fleet-agent start`` — start a task (create state + launch first stage driver).

The first stage is always launched; ``--role`` is not supported.
Shared ``launch_stage_driver()`` is called by the orchestrator for subsequent stages.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from .. import agents as agents_mod
from .. import driver_prompt as dp
from .. import prompt_deliverer
from .. import workspace as workspace_mod
from .. import prompt_pointer
from .. import state as state_mod
from .. import task_context
from .. import formation as formation_mod
from .. import tmux as tmux_mod
from ..events import append_event, truncate_text


_TASK_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_TASK_ID_MAX_LEN = 24


def _validate_task_id(task_id: str) -> str | None:
    """Validate a task id as a slug.

    Returns an error message describing the violation, or ``None`` when the
    id is valid. The id must be kebab-case (lowercase ``a-z``, ``0-9``, single
    hyphens, no leading/trailing/consecutive hyphens) and at most
    ``_TASK_ID_MAX_LEN`` characters.
    """
    if not _TASK_ID_RE.match(task_id):
        return (
            "error: task id must be kebab-case "
            f"(lowercase a-z, 0-9, single hyphens): {task_id}"
        )
    if len(task_id) > _TASK_ID_MAX_LEN:
        return f"error: task id too long ({len(task_id)} chars, max {_TASK_ID_MAX_LEN}): {task_id}"
    return None


def _resolve_owner_session(args: argparse.Namespace) -> str:
    """Resolve the task's owner session label (Issue #166 §10.3).

    Precedence (decided in #166): an explicit ``--session`` override wins, else
    the leader pane's ``FLEET_SESSION`` env, else the default ``"main"``. The label
    is stamped onto ``task.yaml`` as ``owner_session`` and keys both driver-window
    placement (``fleet-<label>``) and leader notification routing.
    """
    explicit = getattr(args, "session", None)
    return explicit or os.environ.get("FLEET_SESSION") or "main"


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
        f"error: codex does not trust this repo ({repo_root}).\n"
        "       Before launching the codex driver, run `codex` once in this directory\n"
        "       and approve it with \"1. Yes, continue\". Then re-run start.",
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
    owner_session: str,
    auto_paste: bool = True,
    prompt_delay: float = 3.0,
    prompt_timeout: float = prompt_deliverer.DEFAULT_TIMEOUT_SECONDS,
    window_cwd: Path | None = None,
    replace_task_windows: bool = True,
) -> int:
    """Open a tmux window for a specific stage driver.

    Shared between ``start`` (first stage) and the orchestrator (later stages).
    Expects the task directory and driver-prompt.md to already exist.

    The driver window opens in the **owner session's** tmux (``fleet-<owner_session>``,
    Issue #166 §5.2) — the session that spawned the task holds both the leader
    window and its drivers' windows. ``project_name`` is used only for the session
    *display* name so resumable panes are distinguishable in the picker.
    """
    agent_spec = stage.get("agent", "")
    role_name = stage.get("role", "driver")

    prompt_path = task_dir / "driver-prompt.md"
    buffer_name = f"fleet-task-{task_id}"

    session = f"fleet-{owner_session}"
    if not tmux_mod.session_exists(session):
        tmux_mod.new_session(session)
    window = f"{task_id}·{role_name}"
    effective_cwd = window_cwd or task_dir

    try:
        repo_root = _fleet_clone_root()
        driver_env = {
            "FLEET_TASK_ID": task_id,
            "FLEET_STATE_DIR": str(state_dir),
            "PATH": f"{repo_root}:{os.environ.get('PATH', '')}",
        }
        if replace_task_windows:
            tmux_mod.kill_task_windows(session, task_id)
        tmux_mod.new_window(session, window, cwd=str(effective_cwd), env=driver_env)
        prompt_pointer.load_pointer_buffer(tmux_mod, buffer_name, prompt_path)

        # Session display name so the user can tell resumable sessions apart
        # in the picker: <project>-<task_id>-<role> (role disambiguates
        # pair_review / multi_stage panes on the same task).
        session_name = f"{project_name}-{task_id}-{role_name}"

        cli = agents_mod.cli_command(agent_spec)
        cli = cli + agents_mod.session_name_launch_args(agent_spec, session_name)
        cli_quoted = " ".join(shlex.quote(p) for p in cli)
        tmux_mod.send_keys(session, window, cli_quoted)

        if auto_paste:
            log_path = prompt_deliverer.start_detached(
                state_dir=state_dir,
                task_id=task_id,
                session=session,
                window=window,
                prompt_path=prompt_path,
                buffer_name=buffer_name,
                agent_spec=agent_spec,
                session_name=session_name,
                timeout=prompt_timeout,
                initial_delay=max(0.0, prompt_delay),
            )
    except tmux_mod.TmuxError as e:
        print(f"warn: tmux setup partially failed: {e}", file=sys.stderr)
        return 0

    print(f"tmux: session={session} window={window}")
    print(f"attach:        tmux attach -t {session}:{window}")
    if auto_paste:
        print(f"prompt:        deliverer detached (log: {log_path})")
    if not auto_paste:
        print("paste pointer: inside the pane press C-b ], then Enter")
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
        help=(
            "Project name (registry); required from a project-agnostic leader "
            "session, else resolved from FLEET_STATE_DIR / cwd"
        ),
    )
    p.add_argument(
        "--session",
        default=None,
        metavar="LABEL",
        help=(
            "Owner session label stamped onto the task (default: env FLEET_SESSION, "
            "else 'main'). Decides driver-window placement and notification routing."
        ),
    )
    p.add_argument(
        "--formation",
        default=None,
        help=(
            "Formation name. Explicit names resolve project -> global -> template; "
            "when omitted, only project formations are auto-picked."
        ),
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
        help="Seconds the detached prompt deliverer waits before polling (default: 3)",
    )
    p.add_argument(
        "--prompt-timeout",
        type=float,
        default=prompt_deliverer.DEFAULT_TIMEOUT_SECONDS,
        metavar="SEC",
        help="Hard timeout for detached prompt delivery (default: 600)",
    )
    p.add_argument(
        "--allow-out-of-scope",
        action="store_true",
        dest="allow_out_of_scope",
        help=(
            "Allow dispatching to a project outside the owner session's scope. "
            "Bypasses the scope guard; use when cross-scope dispatch is intentional."
        ),
    )
    p.set_defaults(func=run)


def _infer_project_from_promptfile(prompt_file: str) -> str | None:
    """Infer the project name from a ``--prompt-file`` path (Issue #150).

    When ``--project`` is omitted, the prompt-file path already names the project
    unambiguously: fleet state lives at ``fleet_home()/projects/<name>/…``. If
    *prompt_file* resolves to a path under exactly one such tree, return
    ``<name>`` so the caller can use it instead of resolving from cwd.

    This replaces the #143 cross-project *error*: a leader invoking ``fleet-agent``
    by absolute path from another repo gets cwd pointing at the wrong project, but
    the prompt-file path names the intended project directly, so we infer and
    proceed rather than rejecting.

    Returns ``None`` when the prompt-file is outside any project tree, in which
    case the caller falls back to cwd resolution exactly as before.
    """
    projects_root = state_mod.fleet_home() / state_mod.PROJECTS_SUBDIR
    pf = Path(prompt_file).resolve()
    try:
        rel = pf.relative_to(projects_root)
    except ValueError:
        return None  # prompt-file is not under any project tree
    if not rel.parts:
        return None
    return rel.parts[0]


def _resolve_description(args: argparse.Namespace) -> tuple[str, str | None] | None:
    """Resolve the task body and an optional fallback title.

    Returns ``(body, fallback_title)``, or ``None`` on error. ``fallback_title``
    is non-``None`` only in the lenient both-passed case: when a positional
    ``description`` and ``--prompt-file`` are *both* given, the prompt-file wins
    the body (richer, intentional input) and the stray positional description is
    relegated to the title. ``--title`` still wins downstream in every path.
    """
    description = getattr(args, "description", None)
    prompt_file = getattr(args, "prompt_file", None)
    has_description = description is not None
    has_prompt_file = prompt_file is not None

    if not has_description and not has_prompt_file:
        print("error: pass either description or --prompt-file", file=sys.stderr)
        return None
    if has_prompt_file:
        path = Path(prompt_file)
        try:
            body = path.read_text()
        except OSError as e:
            print(f"error: cannot read --prompt-file {path}: {e}", file=sys.stderr)
            return None
        if has_description:
            # Lenient: both given is a common leader slip, not a hard error.
            # prompt-file wins the body; the positional description becomes the
            # title fallback. Surface the slip so it is visible, not silent.
            print(
                "warn: both description and --prompt-file given; using --prompt-file "
                "as the body and treating the positional description as the title",
                file=sys.stderr,
            )
            return body, description
        return body, None
    return description, None


def run(args: argparse.Namespace) -> int:
    task_id_error = _validate_task_id(args.task_id)
    if task_id_error is not None:
        print(task_id_error, file=sys.stderr)
        return 1

    resolved = _resolve_description(args)
    if resolved is None:
        return 1
    description, fallback_title = resolved

    project_arg = getattr(args, "project", ".")
    project_name = project_arg if project_arg != "." else None

    # Project inference from --prompt-file (Issue #150, follow-up to #143).
    # When --project is omitted and --prompt-file lives under projects/<name>/,
    # the path names the project unambiguously — infer <name> and use it BEFORE
    # resolve_state_dir, instead of resolving from cwd and erroring on mismatch.
    # Explicit --project always wins (legit cross-project use is possible).
    prompt_file = getattr(args, "prompt_file", None)
    inferred_name: str | None = None
    if project_name is None and prompt_file is not None:
        inferred_name = _infer_project_from_promptfile(prompt_file)

    resolve_name = project_name or inferred_name
    try:
        state_dir = task_context.resolve_project_state_dir(project_name=resolve_name)
    except task_context.ProjectNotFound as e:
        if inferred_name is not None:
            # Inferred a project from the prompt-file path that isn't registered.
            # Fail loudly rather than silently falling back to the cwd project.
            print(
                f"error: --prompt-file names project {inferred_name!r}, but it is "
                f"not a registered project",
                file=sys.stderr,
            )
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1

    task_dir_path = state_mod.task_dir(state_dir, args.task_id)
    if task_dir_path.exists():
        print(f"error: task-{args.task_id} already exists at {task_dir_path}", file=sys.stderr)
        return 1

    owner_session = _resolve_owner_session(args)

    # Scope guard: block dispatch to projects outside the owner session's scope.
    # Only applies when the session has a declared scope (unscoped ⇒ no-op).
    # The guard runs even in --dry-run so unit tests can exercise it without tmux.
    if not getattr(args, "allow_out_of_scope", False):
        try:
            _project_meta = state_mod.load_project(state_dir)
            target_name = _project_meta.get("name") or state_dir.name
        except FileNotFoundError:
            target_name = state_dir.name
        if not state_mod.in_scope(owner_session, target_name):
            scope = state_mod.session_scope(owner_session)
            scope_str = ", ".join(scope) if scope else ""
            print(
                f"error: project {target_name!r} is outside session {owner_session!r} scope "
                f"({scope_str}); pass --allow-out-of-scope to override or run "
                f"`fleet scope {owner_session} --add {target_name}`",
                file=sys.stderr,
            )
            return 1

    # Load and validate formation
    try:
        formation_name, formation_data = formation_mod.resolve_formation(
            state_dir=state_dir,
            requested=args.formation,
            owner_session=owner_session,
        )
        formation_mod.validate(formation_data)
        dp.validate_formation_roles(formation_data, state_dir)
    except (formation_mod.ResolutionError, dp.RoleResolutionError, ValueError) as e:
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

    # Title precedence: explicit --title > relegated positional (both-passed
    # case) > first line of the body. Derive the implicit title from the right
    # source so the prompt-file body never masquerades as the title.
    if args.title:
        title = args.title
    else:
        title_source = fallback_title if fallback_title is not None else description
        title = (title_source.splitlines() or [""])[0][:80]

    # workspace: on_pre_start hook can attach extra task fields and/or
    # override the window cwd (workspace=worktree creates the worktree here).
    ctx: dict = {
        "state_dir": state_dir,
        "task_id": args.task_id,
        "formation": formation_name,
        "role": role_name,
        "agent": agent_spec,
        "description": description,
        "title": title,
        "project_root": _project_root_for_trust or state_dir.parent,
        "dry_run": bool(args.dry_run),
    }
    if not args.dry_run:
        try:
            workspace_mod.on_pre_start(ctx)
        except Exception as e:  # noqa: BLE001 — workspace errors are reportable
            print(f"error: workspace on_pre_start failed: {e}", file=sys.stderr)
            return 1

    task_data: dict = {
        "id": args.task_id,
        "title": title,
        "description": description,
        # Derive the task-level status from the stages instead of hardcoding
        # "spawning": stage[0] was just set to "running" above, so this resolves
        # to "running" and the task is self-consistent from birth. (A solo task
        # is a single stage = the whole task, so it would otherwise show
        # "spawning" the entire time it runs and never report "running".)
        "status": state_mod.derive_task_status(expanded_stages),
        "formation": formation_name,
        "owner_session": owner_session,
        "workspace": workspace_mod.load(state_dir),
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
        formation_name=formation_name,
        role=role_name,
        agent=agent_spec,
        state_dir=state_dir,
    )
    (task_dir_path / "driver-prompt.md").write_text(prompt)

    project_name = (_pre_project or {}).get("name", "?")
    append_event(
        state_dir / "events.jsonl",
        "start",
        task_id=args.task_id,
        formation=formation_name,
        role=role_name,
        agent=agent_spec,
        # Audit snippet only — the full description lives in driver-prompt.md.
        description=truncate_text(description),
        dry_run=bool(args.dry_run),
    )

    print(f"task-{args.task_id} prepared:")
    print(f"  state dir:      {state_dir}")
    print(f"  driver-prompt:  {task_dir_path / 'driver-prompt.md'}")
    print(f"  agent:          {agent_spec}")
    print(f"  role:           {role_name}")
    print(f"  formation:       {formation_name}")

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
        owner_session=owner_session,
        auto_paste=args.auto_paste,
        prompt_delay=args.prompt_delay,
        prompt_timeout=getattr(args, "prompt_timeout", prompt_deliverer.DEFAULT_TIMEOUT_SECONDS),
        window_cwd=ctx.get("cwd"),
    )
