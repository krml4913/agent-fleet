"""``fleet url-handler <uri>`` — hidden: invoked by the registered ``fleet://`` protocol.

Windows only, registered by ``fleet notify setup-windows``
(:mod:`fleet.windows_notify_setup`) as the ``fleet://`` protocol's
``shell\\open\\command``, run under pythonw (no console of its own). Clicking a
fleet toast (or one of its buttons) is what invokes this.

Three actions are supported:

- ``fleet://attach?project=<name>&task=<id>`` (#317) — open a terminal attached
  to the task (the toast body click, and the **Open** button).
- ``fleet://approve?project=…&task=…&stage=<n>&nonce=<hex>`` and
  ``fleet://reject?…`` (#318) — the **Approve** / **Reject** buttons of an
  approval-gate toast. Any app or web page can open a ``fleet://`` URL, so these
  are honoured only with a live single-use nonce (:mod:`fleet.toast_nonce`)
  bound to project + task + stage, and only while the task is still at that
  gate. Approve asks for confirmation in a native message box first; Reject asks
  for the reason in a native input box (cancel / empty reason does nothing).
  Both then run the same code path as ``fleet-agent approve`` / ``reject
  --reason`` and tell the owning leader (a queued ``toast_decision`` record) so
  it does not re-approve.

Anything else — an unknown action, an unregistered project, a nonexistent task,
a refused nonce, or a value that fails strict validation (rejecting injection
attempts like ``&``, ``"``, ``%``, spaces, ``..``) — is refused and logged to
``fleet-state/global/url-handler.log``, never acted on. URI parts are never
passed through a shell: everything here is argv-based subprocess calls with a
project/task id that has already been validated against the registry.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .. import leader_notifier
from .. import orchestrator as orch
from .. import state as state_mod
from .. import task_context
from .. import toast_nonce
from ..events import append_event, truncate_text
from . import approval

LOG_NAME = "url-handler.log"

# Project / task ids are always plain slugs (registry names, task ids). No
# dot: that alone keeps ".." out without a separate traversal check. Reject
# anything outside this shape before it ever reaches a lookup or a subprocess
# argv — this is what turns "&", '"', "%", spaces and ".." into an ordinary
# rejection instead of a parsing surprise.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_STAGE_RE = re.compile(r"^[0-9]{1,3}$")

GATE_ACTIONS = ("approve", "reject")


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


@dataclass(frozen=True)
class GateTarget:
    """An ``approve`` / ``reject`` toast button: the gate plus its single-use nonce."""

    action: str
    project: str
    task_id: str
    stage: int
    nonce: str


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


def parse_uri(uri: str) -> AttachTarget | GateTarget | None:
    """Parse+validate a ``fleet://`` toast URI.

    ``attach?project=…&task=…`` gives an :class:`AttachTarget`; ``approve`` /
    ``reject`` with ``project``, ``task``, ``stage`` and ``nonce`` give a
    :class:`GateTarget`. Returns ``None`` for anything else: a different
    scheme, another action, a missing/duplicated field, or a value that fails
    its shape check. Never raises.
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
    if action != "attach" and action not in GATE_ACTIONS:
        return None

    query = parse_qs(parts.query, keep_blank_values=True)
    projects = query.get("project") or []
    tasks = query.get("task") or []
    if len(projects) != 1 or len(tasks) != 1:
        return None
    project, task_id = projects[0], tasks[0]
    if not _NAME_RE.match(project) or not _NAME_RE.match(task_id):
        return None
    if action == "attach":
        return AttachTarget(project=project, task_id=task_id)

    stages = query.get("stage") or []
    nonces = query.get("nonce") or []
    if len(stages) != 1 or len(nonces) != 1:
        return None
    if not _STAGE_RE.match(stages[0]) or not toast_nonce.NONCE_RE.match(nonces[0]):
        return None
    return GateTarget(
        action=action, project=project, task_id=task_id, stage=int(stages[0]), nonce=nonces[0]
    )


def _reject(uri: str, reason: str) -> int:
    _log(f"reject uri={uri!r}: {reason}")
    print(f"error: {reason}", file=sys.stderr)
    return 1


def run(args: argparse.Namespace) -> int:
    uri = args.uri
    target = parse_uri(uri)
    if target is None:
        return _reject(uri, "unrecognized or invalid fleet:// URI")

    try:
        state_dir = task_context.resolve_project_state_dir(project_name=target.project)
    except task_context.ProjectNotFound:
        return _reject(uri, f"unknown project: {target.project}")

    try:
        task = state_mod.load_task(state_dir, target.task_id)
    except FileNotFoundError:
        return _reject(uri, f"unknown task: {target.project}/task-{target.task_id}")

    if isinstance(target, GateTarget):
        return _run_gate(uri, target, state_dir, task)

    _log(f"attach uri={uri!r} project={target.project} task={target.task_id}")
    _emit_toast_event(state_dir, target.task_id, "attach")
    return open_attach_terminal(target.project, target.task_id)


def _emit_toast_event(state_dir: Path, task_id: str, action: str) -> None:
    """Audit event for a toast action (``source: toast``). Never raises."""
    try:
        append_event(
            state_dir / "events.jsonl",
            "toast_action",
            task_id=task_id,
            action=action,
            source="toast",
        )
    except OSError as e:
        _log(f"could not append toast_action event for task={task_id}: {e}")


def _gate_refusal(state_dir: Path, task: dict, target: GateTarget) -> str | None:
    """Why the button may not act, or ``None``: live nonce + task still at that gate."""
    refusal = toast_nonce.check(
        state_dir, target.project, target.task_id, target.stage, target.nonce
    )
    if refusal:
        return refusal
    if (
        task.get("current_stage") != target.stage
        or task.get("status") != "awaiting_orders"
        or not orch._has_user_relay_target(task)
    ):
        return f"task is no longer at the approval gate of stage {target.stage}"
    return None


def _run_gate(uri: str, target: GateTarget, state_dir: Path, task: dict) -> int:
    where = f"project={target.project} task={target.task_id} stage={target.stage}"
    refusal = _gate_refusal(state_dir, task, target)
    if refusal:
        return _reject(uri, f"{target.action} refused: {refusal}")

    reason: str | None = None
    if target.action == "approve":
        if not confirm_approve(target.project, target.task_id):
            _log(f"approve cancelled at the confirmation dialog {where}")
            return 0
    else:
        reason = (ask_reject_reason(target.project, target.task_id) or "").strip()
        if not reason:
            _log(f"reject cancelled (no reason given) {where}")
            return 0

    # The dialog can stay open a long time: re-check the gate, then spend the
    # nonce atomically so a second click on the same toast cannot act twice.
    try:
        task = state_mod.load_task(state_dir, target.task_id)
    except FileNotFoundError:
        return _reject(uri, f"unknown task: {target.project}/task-{target.task_id}")
    refusal = _gate_refusal(state_dir, task, target) or toast_nonce.consume(
        state_dir, target.project, target.task_id, target.stage, target.nonce
    )
    if refusal:
        return _reject(uri, f"{target.action} refused: {refusal}")

    _log(f"{target.action} uri={uri!r} {where}")
    args = argparse.Namespace(
        task_id=target.task_id,
        project=target.project,
        source="toast",
        reason=reason,
        reason_file=None,
    )
    if target.action == "approve":
        rc = approval.run_approve(args)
        summary = "[approved by user via toast]"
    else:
        rc = approval.run_reject(args)
        summary = f"[rejected by user via toast: {truncate_text(reason or '', 200)}]"
    if rc != 0:
        _log(f"{target.action} failed rc={rc} {where}")
        return rc
    _tell_leader(state_dir, target.task_id, summary)
    return 0


def _tell_leader(state_dir: Path, task_id: str, summary: str) -> None:
    """Queue a ``toast_decision`` record for the owning leader (opt-in, best-effort).

    Goes through :func:`leader_notifier.push_to_leader`, so it respects
    ``notify_leader_on_driver_done`` and never raises.
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
            status=str(task.get("status", "")),
            summary=summary,
            kind=leader_notifier.KIND_TOAST_DECISION,
        )
    except Exception as e:  # noqa: BLE001 — the decision is already applied
        _log(f"could not tell the leader about task={task_id}: {e}")


# -- native dialogs (Windows). The handler runs under pythonw with no console, so
# these are a MessageBox (ctypes) and a PowerShell WinForms form — never tkinter.
# Tests patch confirm_approve / ask_reject_reason; nothing else pops a dialog.

_MB_YESNO = 0x4
_MB_ICONQUESTION = 0x20
_MB_DEFBUTTON2 = 0x100  # default to "No": Enter alone must not approve
_MB_SETFOREGROUND = 0x10000
_MB_TOPMOST = 0x40000
_IDYES = 6

# A small WinForms dialog rather than VB's InputBox: InputBox cannot be made
# topmost, and launched from a background process it opened *behind* other
# windows. TopMost + Activate + SetForegroundWindow brings this one to the front.
# Prompt/title come from the environment (never spliced into the script). OK with
# text prints it; Cancel / closing prints nothing.
_INPUT_BOX_SCRIPT = """Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -Namespace FleetDlg -Name Native -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetForegroundWindow(System.IntPtr h);'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[System.Windows.Forms.Application]::EnableVisualStyles()
$f = New-Object System.Windows.Forms.Form
$f.Text = $env:FLEET_DIALOG_TITLE
$f.StartPosition = 'CenterScreen'
$f.FormBorderStyle = 'FixedDialog'
$f.MaximizeBox = $false
$f.MinimizeBox = $false
$f.TopMost = $true
$f.ClientSize = New-Object System.Drawing.Size(440, 120)
$l = New-Object System.Windows.Forms.Label
$l.Text = $env:FLEET_DIALOG_PROMPT
$l.SetBounds(12, 10, 416, 34)
$t = New-Object System.Windows.Forms.TextBox
$t.SetBounds(12, 50, 416, 24)
$ok = New-Object System.Windows.Forms.Button
$ok.Text = 'OK'
$ok.DialogResult = 'OK'
$ok.SetBounds(272, 84, 75, 26)
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = 'Cancel'
$cancel.DialogResult = 'Cancel'
$cancel.SetBounds(353, 84, 75, 26)
$f.AcceptButton = $ok
$f.CancelButton = $cancel
$f.Controls.AddRange(@($l, $t, $ok, $cancel))
$f.Add_Shown({ $f.Activate(); [FleetDlg.Native]::SetForegroundWindow($f.Handle) | Out-Null; $t.Focus() | Out-Null })
if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($t.Text) }
"""


def _allow_foreground() -> None:
    """Let the dialog we are about to open take the foreground. Best-effort.

    This process was just started by the user's click on the toast, so it may
    hand foreground rights on (``ASFW_ANY``) — including to the PowerShell child.
    """
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    except Exception:  # noqa: BLE001 — cosmetic only
        pass


def confirm_approve(project: str, task_id: str) -> bool:
    """Native Yes/No box: really approve this stage? ``False`` off Windows or on any error."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        _allow_foreground()
        flags = _MB_YESNO | _MB_ICONQUESTION | _MB_DEFBUTTON2 | _MB_SETFOREGROUND | _MB_TOPMOST
        answer = ctypes.windll.user32.MessageBoxW(
            0,
            f"Approve the current stage of task-{task_id} ({project})?",
            "agent-fleet: confirm approval",
            flags,
        )
    except Exception as e:  # noqa: BLE001 — no dialog means no approval
        _log(f"approve confirmation dialog failed: {e}")
        return False
    return answer == _IDYES


def ask_reject_reason(project: str, task_id: str) -> str | None:
    """Native input box for the rejection reason; ``None`` when cancelled or unavailable."""
    if sys.platform != "win32":
        return None
    _allow_foreground()
    encoded = base64.b64encode(_INPUT_BOX_SCRIPT.encode("utf-16-le")).decode("ascii")
    env = dict(os.environ)
    env["FLEET_DIALOG_TITLE"] = "agent-fleet: reject"
    env["FLEET_DIALOG_PROMPT"] = (
        f"Why reject task-{task_id} ({project})? The driver will see this reason."
    )
    try:
        proc = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-STA",
                "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
            ],
            capture_output=True,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        _log(f"reject reason dialog failed: {e}")
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace").lstrip("﻿").strip() or None


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
