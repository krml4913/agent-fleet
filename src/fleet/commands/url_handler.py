"""``fleet url-handler <uri>`` — hidden: invoked by the registered ``fleet://`` protocol.

Windows only, registered by ``fleet notify setup-windows``
(:mod:`fleet.windows_notify_setup`) as the ``fleet://`` protocol's
``shell\\open\\command``, run under pythonw (no console of its own). Clicking a
fleet toast that carries a ``fleet://attach?...`` launch target (#317) is what
invokes this.

Phase 1 supports exactly one action, ``attach``:
``fleet://attach?project=<name>&task=<id>``. Anything else — an unknown
action, an unregistered project, a nonexistent task, or a value that fails
strict validation (rejecting injection attempts like ``&``, ``"``, ``%``,
spaces, ``..``) — is refused and logged to
``fleet-state/global/url-handler.log``, never acted on. URI parts are never
passed through a shell: everything here is argv-based subprocess calls with a
project/task id that has already been validated against the registry.

Approve/reject buttons (nonce + confirmation) are phase 2 (a separate issue);
this module only implements ``attach``, but keeps the parse/dispatch split
(:func:`parse_uri` / :func:`run`) so a second action is a small addition, not
a rewrite.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .. import state as state_mod
from .. import task_context

LOG_NAME = "url-handler.log"

# Project / task ids are always plain slugs (registry names, task ids). No
# dot: that alone keeps ".." out without a separate traversal check. Reject
# anything outside this shape before it ever reaches a lookup or a subprocess
# argv — this is what turns "&", '"', "%", spaces and ".." into an ordinary
# rejection instead of a parsing surprise.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "url-handler",
        help=argparse.SUPPRESS,
        description=(
            "Internal: handles a fleet:// URI registered by `fleet notify "
            "setup-windows`. Not meant for direct/manual use."
        ),
    )
    p.add_argument("uri")
    p.set_defaults(func=run)


@dataclass(frozen=True)
class AttachTarget:
    project: str
    task_id: str


def _log(message: str) -> None:
    """Append one timestamped line to the global url-handler log. Never raises."""
    path = state_mod.global_dir() / LOG_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {message}\n")
    except OSError:
        pass


def parse_uri(uri: str) -> AttachTarget | None:
    """Parse+validate a ``fleet://attach?project=…&task=…`` URI.

    Returns ``None`` for anything that is not exactly that shape: a different
    scheme, an action other than ``attach``, a missing/duplicated field, or a
    project/task value that fails :data:`_NAME_RE`. Never raises.
    """
    try:
        parts = urlsplit(uri)
    except ValueError:
        return None
    if parts.scheme.lower() != "fleet":
        return None
    # fleet://attach?... -> netloc="attach"; be lenient about a fleet:attach?...
    # or fleet:///attach?... shape too (path-only), since URL parsers differ.
    action = parts.netloc or parts.path.strip("/")
    if action != "attach":
        return None

    query = parse_qs(parts.query, keep_blank_values=True)
    projects = query.get("project") or []
    tasks = query.get("task") or []
    if len(projects) != 1 or len(tasks) != 1:
        return None
    project, task_id = projects[0], tasks[0]
    if not _NAME_RE.match(project) or not _NAME_RE.match(task_id):
        return None
    return AttachTarget(project=project, task_id=task_id)


def _reject(uri: str, reason: str) -> int:
    _log(f"reject uri={uri!r}: {reason}")
    print(f"error: {reason}", file=sys.stderr)
    return 1


def run(args: argparse.Namespace) -> int:
    uri = args.uri
    target = parse_uri(uri)
    if target is None:
        return _reject(uri, "unrecognized or invalid fleet:// URI (only attach is supported)")

    try:
        state_dir = task_context.resolve_project_state_dir(project_name=target.project)
    except task_context.ProjectNotFound:
        return _reject(uri, f"unknown project: {target.project}")

    try:
        state_mod.load_task(state_dir, target.task_id)
    except FileNotFoundError:
        return _reject(uri, f"unknown task: {target.project}/task-{target.task_id}")

    _log(f"attach uri={uri!r} project={target.project} task={target.task_id}")
    return open_attach_terminal(target.project, target.task_id)


def _clone_root() -> Path:
    # src/fleet/commands/url_handler.py -> parents[3] = clone root
    return Path(__file__).resolve().parents[3]


def attach_argv(project: str, task_id: str) -> list[str]:
    """The argv that opens a visible console attached to ``task_id``.

    Runs through ``cmd.exe /c`` (rather than execing ``fleet.cmd`` directly)
    so the console stays open on the attach's own exit — matching how a human
    would run it by hand. Safe with a bare argv (no shell string is built):
    ``project`` / ``task_id`` are already validated against ``_NAME_RE``, so
    no shell metacharacter ever reaches ``cmd.exe``.
    """
    fleet_cmd = _clone_root() / "fleet.cmd"
    comspec = os.environ.get("ComSpec") or "cmd.exe"
    return [comspec, "/c", str(fleet_cmd), "attach", task_id, "--project", project]


def open_attach_terminal(project: str, task_id: str) -> int:
    """Open a **visible** terminal running the attach (the handler itself has none).

    Prefers Windows Terminal (``wt.exe``) when present; otherwise a new
    console window. Best-effort: a failure to spawn is logged and reported,
    never raised (this runs under pythonw with nothing to show a traceback to).
    """
    argv = attach_argv(project, task_id)
    wt = shutil.which("wt.exe") or shutil.which("wt")
    try:
        if wt:
            subprocess.Popen([wt, *argv])
        else:
            subprocess.Popen(argv, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    except OSError as e:
        _log(f"failed to open a terminal for project={project} task={task_id}: {e}")
        print(f"error: could not open a terminal: {e}", file=sys.stderr)
        return 1
    return 0
