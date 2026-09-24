"""``fleet leader`` — launch a project-agnostic leader session.

Creates a detached multiplexer session named ``fleet-<label>`` (default label
``main``), opens a single ``leader`` window in the **agent-fleet clone root**,
and starts the chosen agent CLI inside. If the session already exists, prints
the attach command and exits (one session per label).

Since Issue #166 a leader session is **not bound to a project** (design §4.1,
§5.6): it drops project resolution, pins cwd to the clone root, and carries its
label in ``FLEET_SESSION`` so ``fleet-agent start`` can stamp ``owner_session``
onto each task. Per-session state lives at ``global/sessions/<label>/``. Per §4.1
the leader only does dialogue and ``fleet-agent start``, never state polling.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import agents as agents_mod
from .. import banner
from .. import config as config_mod
from .. import leader_prompt as lp
from .. import prompt_pointer
from .. import state as state_mod
from .. import mux
from ..events import append_event


#: Built-in default when neither --agent nor the ``leader_agent`` config key
#: is set. Canonical definition lives in :mod:`fleet.config` (also its
#: ``leader_agent`` default); re-exported here for callers of this module.
DEFAULT_LEADER_AGENT = config_mod.DEFAULT_LEADER_AGENT
DEFAULT_SESSION_LABEL = "main"

#: With ``--attach``, seconds to hold after printing the startup banner so the
#: user actually sees it before the multiplexer screen takes over.
BANNER_ATTACH_PAUSE = 2.0

# commands/leader.py → parents[0]=commands, [1]=fleet, [2]=src, [3]=clone root.
_CLONE_ROOT = Path(__file__).resolve().parents[3]


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "leader",
        help="Launch a project-agnostic leader session",
        description=(
            "Create a multiplexer session 'fleet-<label>' (default label 'main') with a "
            "single leader window running the chosen agent CLI in the agent-fleet "
            "clone root. One session per label: if it already exists, prints the "
            "attach command and exits. The session is project-agnostic — it serves "
            "any project, and every dispatch passes --project explicitly."
        ),
    )
    p.add_argument(
        "--name",
        default=DEFAULT_SESSION_LABEL,
        metavar="LABEL",
        help=f"Session label → session fleet-<label> (default: {DEFAULT_SESSION_LABEL})",
    )
    p.add_argument(
        "--agent",
        default=None,
        help=(
            "Agent spec (vendor:model) or agent alias for the leader pane. "
            "Precedence: this flag > 'leader_agent' in global config (fleet "
            "config set leader_agent <vendor:model|alias>) > built-in default "
            f"({DEFAULT_LEADER_AGENT})."
        ),
    )
    p.add_argument(
        "--attach",
        action="store_true",
        help="After starting, attach to the session (foreground).",
    )
    p.add_argument(
        "--no-auto-paste",
        dest="auto_paste",
        action="store_false",
        help="Skip pasting the leader prompt into the pane.",
    )
    p.add_argument(
        "--prompt-delay",
        type=float,
        default=3.0,
        help="Seconds to wait for the agent CLI to start before pasting (default: 3.0).",
    )
    p.add_argument(
        "--scope",
        default=None,
        metavar="PROJECTS",
        help=(
            "Comma-separated project names to set as this session's scope at launch. "
            "Sets scope in session.json before the prompt is pasted. Names are "
            "validated against the registry. Example: --scope image-gallery,bmweb,fleet"
        ),
    )
    p.set_defaults(func=run, auto_paste=True)


def run(args: argparse.Namespace) -> int:
    label = getattr(args, "name", None) or DEFAULT_SESSION_LABEL
    session = f"fleet-{label}"

    # Precedence: --agent (explicit) > 'leader_agent' in global config > the
    # built-in default. config.get() already falls back tolerantly (warns and
    # ignores) if the stored value is malformed, so agent_spec here is always
    # at least the built-in default.
    agent_spec = getattr(args, "agent", None) or config_mod.get("leader_agent")[0]

    m = mux.get()
    if not m.available():
        print(f"error: {m.name} not on PATH", file=sys.stderr)
        return 1

    if m.session_exists(session):
        print(f"leader session already exists: {session}")
        print(f"  attach: {m.attach_hint(session)}")
        if args.attach:
            return _attach(m, session)
        return 0

    # Resolve an agent alias (from --agent or leader_agent) to the full spec
    # at launch; the session record carries the resolved spec.
    agent_alias = agent_spec.strip() if agents_mod.is_alias(agent_spec) else None
    try:
        agent_spec = agents_mod.resolve_spec(agent_spec)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Validate --scope against the registry *before* any mux session or
    # session.json is created. An invalid project name must fail early — not
    # after the leader pane and record already exist with an unpasted prompt.
    scope_arg = getattr(args, "scope", None)
    scope_names: list[str] = []
    if scope_arg:
        scope_names = [n.strip() for n in scope_arg.split(",") if n.strip()]
        try:
            state_mod.validate_registered_projects(scope_names)
        except ValueError as e:
            print(f"error: --scope: {e}", file=sys.stderr)
            return 1

    session_dir = state_mod.session_dir(label)
    session_dir.mkdir(parents=True, exist_ok=True)

    # Session display name so the leader is identifiable in the session picker.
    session_name = state_mod.leader_agent_name(label)

    cli = agents_mod.cli_command(agent_spec)
    cli = cli + agents_mod.session_name_launch_args(agent_spec, session_name)

    try:
        m.new_session(
            session,
            window="leader",
            argv=cli,
            cwd=str(_CLONE_ROOT),
            env={
                "FLEET_SESSION": label,
                "FLEET_STATE_DIR": str(session_dir),
            },
        )
    except mux.MuxError as e:
        print(f"error: {m.name} setup failed: {e}", file=sys.stderr)
        return 1

    record: dict = {
        "label": label,
        "agent": agent_spec,
        **({"agent_alias": agent_alias} if agent_alias else {}),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pane": f"{session}:leader",
    }
    state_mod.session_record_path(label).write_text(
        json.dumps(record, indent=2),
        encoding="utf-8",
    )

    # Apply the already-validated scope (parsed above) before the prompt is
    # pasted. Names were registry-checked before the session existed, so this
    # only persists the scope onto the freshly written record.
    if scope_names:
        state_mod.set_session_scope(label, scope_names, mode="set")

    if args.auto_paste:
        prompt_text = lp.render(session_label=label)
        prompt_path = session_dir / "leader-prompt.md"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        try:
            time.sleep(max(0.0, args.prompt_delay))
            # Name the session BEFORE pasting: vendors with no launch-time flag
            # (codex) rename via post-ready keystrokes; claude is already named
            # at launch → session_rename_keys is [] → no-op.
            for step, enter in agents_mod.session_rename_keys(agent_spec, session_name):
                mux.send_step(session, "leader", step, enter=enter, backend=m)
                time.sleep(0.6)
            prompt_pointer.paste_pointer(
                m, session=session, window="leader", prompt_path=prompt_path
            )
            time.sleep(0.8)
            m.send_key(session, "leader", "Enter")
        except mux.MuxError as e:
            print(f"warn: leader prompt paste failed: {e}", file=sys.stderr)

    append_event(
        session_dir / "events.jsonl",
        "leader_start",
        agent=agent_spec,
        session=session,
        label=label,
    )

    banner.print_banner(label=label, agent=agent_spec, scope=scope_names)
    print(f"leader started: session={session}, agent={agent_spec}")
    print(f"  attach: {m.attach_hint(session)}")

    if args.attach:
        _flush_stdout()
        time.sleep(BANNER_ATTACH_PAUSE)
        return _attach(m, session)
    return 0


def _flush_stdout() -> None:
    """Flush stdout so the banner is on screen before the attach pause (piped stdout is block-buffered)."""
    try:
        sys.stdout.flush()
    except Exception:
        pass


def _attach(m: mux.Mux, session: str) -> int:
    try:
        return m.attach(session)
    except mux.MuxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
