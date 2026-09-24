"""``fleet notify`` — show / enable / disable the opt-in leader-pane push,
and (Windows only) set up / tear down the fleet-owned toast sender + click-to-attach.

The leader-pane push (design §10.3; driver ``done`` / approval gate and ``ask``)
is gated on ``notify_leader_on_driver_done`` in ``project.yaml``. This is the
only supported way to flip it: the leader protocol forbids hand-editing state
files.

``setup-windows`` / ``teardown-windows`` (#317) are unrelated to that gate —
they are a per-user, per-machine Windows registration (HKCU only, no admin),
not a project setting, so they ignore ``--project``. See
:mod:`fleet.windows_notify_setup`.
"""
from __future__ import annotations

import argparse
import sys

from .. import state as state_mod
from .. import task_context
from .done import _truthy

KEY = "notify_leader_on_driver_done"
STATES = ("on", "off", "status", "setup-windows", "teardown-windows")
_WINDOWS_ONLY_STATES = ("setup-windows", "teardown-windows")


def add_parser(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser(
        "notify",
        help="Show/set the leader-pane push, or set up Windows toast click-to-attach",
        description=(
            "on | off | status: show or set the opt-in leader-pane push. When "
            "on, a driver's done / approval gate and ask question are injected "
            f"into the owning leader's pane ({KEY} in project.yaml). Default: "
            "off. With no argument (or 'status'), print the current state.\n"
            "setup-windows | teardown-windows (Windows only): register/remove "
            "a fleet-owned AUMID (toasts show as \"agent-fleet\" instead of "
            "\"Windows PowerShell\") and the fleet:// URL protocol (click a "
            "toast to attach to the task's pane). HKCU only, no admin; "
            "ignores --project."
        ),
    )
    p.add_argument(
        "--project",
        default=".",
        help=(
            "Project name (registry); required from a project-agnostic leader "
            "session, else resolved from FLEET_STATE_DIR / cwd. Ignored by "
            "setup-windows / teardown-windows."
        ),
    )
    p.add_argument(
        "state",
        nargs="?",
        choices=STATES,
        default="status",
        help="on | off | status | setup-windows | teardown-windows (default: status)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    if args.state in _WINDOWS_ONLY_STATES:
        return _run_windows_setup(teardown=args.state == "teardown-windows")

    project_name = args.project if args.project != "." else None
    try:
        sd = task_context.resolve_project_state_dir(project_name=project_name)
    except task_context.ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    project = state_mod.load_project(sd)

    if args.state == "status":
        print(f"notify_leader_on_driver_done: {_label(project)}")
        return 0

    project[KEY] = "true" if args.state == "on" else "false"
    state_mod.save_project(sd, project)
    print(f"notify_leader_on_driver_done: {args.state}")
    return 0


def _label(project: dict) -> str:
    return "on" if _truthy(project.get(KEY)) else "off"


def _run_windows_setup(*, teardown: bool) -> int:
    from .. import windows_notify_setup as wns

    if not wns.is_windows():
        verb = "teardown-windows" if teardown else "setup-windows"
        print(f"error: `fleet notify {verb}` is only available on Windows", file=sys.stderr)
        return 1

    if teardown:
        result = wns.teardown()
        print(
            f"{wns.AUMID_KEY_DISPLAY}: "
            f"{'removed' if result.aumid_removed else 'was not present'}"
        )
        print(
            f"{wns.PROTOCOL_KEY_DISPLAY}: "
            f"{'removed' if result.protocol_removed else 'was not present'}"
        )
        return 0

    result = wns.setup()
    print(
        f"{wns.AUMID_KEY_DISPLAY}: "
        f"{'created' if result.aumid_created else 'already present'} "
        f"(DisplayName={wns.AUMID_DISPLAY_NAME!r})"
    )
    print(
        f"{wns.PROTOCOL_KEY_DISPLAY}: "
        f"{'created' if result.protocol_created else 'already present'}"
    )
    print(f"  shell\\open\\command: {result.command}")
    print("route: registry-only AppUserModelId (no Start-menu shortcut needed)")
    return 0
