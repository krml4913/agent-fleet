"""Best-effort notifications: macOS / Windows desktop + Slack webhook.

Configuration lives in ``<state_dir>/notify.yaml`` (created on demand;
its absence means "default settings — silent slack, native desktop"):

```yaml
macos:
  enabled: true
windows:
  enabled: true
slack:
  enabled: true
  webhook_url: "https://hooks.slack.com/..."
```

All transports are best-effort — failures are reported to stderr but
never raise. Nothing in this module imports anything heavy.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

import yaml

CONFIG_FILE = "notify.yaml"

# Status levels, shared across all transports.
LEVELS = ("success", "waiting", "progress", "error", "info")
DEFAULT_LEVEL = "info"

# Level → leading status emoji (every transport gets this).
_LEVEL_EMOJI = {
    "success": "✅",
    "waiting": "🟡",
    "progress": "▶️",
    "error": "❌",
    "info": "ℹ️",
}

# Level → Slack attachment color keyword.
_LEVEL_COLOR = {
    "success": "good",
    "waiting": "warning",
    "progress": "#439FE0",  # neutral blue
    "error": "danger",
    "info": "#cccccc",  # neutral grey
}


def level_emoji(level: str) -> str:
    """Leading status emoji for a level (falls back to the default level)."""
    return _LEVEL_EMOJI.get(level, _LEVEL_EMOJI[DEFAULT_LEVEL])


def level_color(level: str) -> str:
    """Slack attachment color for a level (falls back to the default level)."""
    return _LEVEL_COLOR.get(level, _LEVEL_COLOR[DEFAULT_LEVEL])


def _context_line(title: str, message: str) -> str:
    """Derive a ``project · task`` context line from the title/message.

    Titles are conventionally ``fleet <project>: task-<id> …``. Extract the
    project and task when present; otherwise fall back to just the project,
    and to nothing if neither is derivable.
    """
    text = f"{title}\n{message}"
    proj = re.search(r"fleet\s+(\S+):", title)
    task = re.search(r"task-(\S+)", text)
    parts = []
    if proj:
        parts.append(proj.group(1))
    if task:
        parts.append(f"task-{task.group(1)}")
    return " · ".join(parts)


def load_config(state_dir: Path) -> dict[str, Any]:
    path = state_dir / CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 — config errors must not propagate
        print(f"warn: notify config unreadable: {e}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def send(
    state_dir: Path,
    title: str,
    message: str,
    level: str = DEFAULT_LEVEL,
    *,
    project: str | None = None,
    task_id: str | None = None,
) -> None:
    """Best-effort notify across every transport.

    ``project`` / ``task_id`` are optional structured context: callers that
    know them (driver ``done`` / ``ask``, the orchestrator's ``awaiting_orders``
    / approval paths) pass them so the Windows transport can attach a
    ``fleet://attach`` click target (#317) once ``fleet notify setup-windows``
    has run. Titles are never parsed for this — it is always the explicit
    kwargs. Other transports ignore them.
    """
    if os.environ.get("FLEET_NO_NOTIFY"):
        return
    cfg = load_config(state_dir)
    _macos_notify(cfg.get("macos") or {}, title, message, level)
    _windows_notify(
        cfg.get("windows") or {}, title, message, level, project=project, task_id=task_id
    )
    _slack_notify(cfg.get("slack") or {}, title, message, level)


def _macos_notify(
    cfg: dict[str, Any], title: str, message: str, level: str = DEFAULT_LEVEL
) -> None:
    if cfg.get("enabled") is False:  # default-on
        return
    if platform.system() != "Darwin":
        return
    if not shutil.which("osascript"):
        return
    body = f"{level_emoji(level)} {message}"
    safe_msg = body.replace('"', '\\"').replace("\n", " ")[:300]
    safe_title = title.replace('"', '\\"')[:60]
    script = f'display notification "{safe_msg}" with title "{safe_title}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
    except Exception as e:  # noqa: BLE001 — best effort
        print(f"warn: macOS notify failed: {e}", file=sys.stderr)


# AppUserModelID of Windows PowerShell's own Start-menu shortcut: toasts shown
# under it need no AUMID registration of our own.
_WINDOWS_TOAST_APP_ID = (
    r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
)

_WINDOWS_TOAST_SCRIPT = """\
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('{xml}')
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""


def _toast_text(text: str) -> str:
    """XML-escape ``text`` into pure ASCII (non-ASCII → ``&#N;``).

    Quotes become entities too, so the result sits safely inside a
    PowerShell single-quoted string (PowerShell also treats the typographic
    quotes U+2018..U+201B as ``'``; being non-ASCII they become ``&#N;``).
    """
    escaped = _xml_escape(text, {'"': "&quot;", "'": "&apos;"})
    return escaped.encode("ascii", "xmlcharrefreplace").decode("ascii")


def _windows_toast_script(
    title: str, message: str, level: str, *, app_id: str, launch: str | None = None
) -> str:
    body = f"{level_emoji(level)} {message}".replace("\n", " ")[:300]
    # activationType="protocol" is what makes Windows invoke launch (fleet://…)
    # on click, via the registered fleet:// command (#317) — only added when
    # there is somewhere to send the click.
    toast_attrs = f' launch="{_toast_text(launch)}" activationType="protocol"' if launch else ""
    xml = (
        f"<toast{toast_attrs}><visual><binding template=\"ToastGeneric\">"
        f"<text>{_toast_text(title.replace(chr(10), ' ')[:60])}</text>"
        f"<text>{_toast_text(body)}</text>"
        "</binding></visual></toast>"
    )
    return _WINDOWS_TOAST_SCRIPT.format(xml=xml, app_id=app_id)


def _windows_launch_uri(project: str | None, task_id: str | None) -> str | None:
    """``fleet://attach?project=…&task=…`` for a toast click, or ``None`` without both."""
    if not project or not task_id:
        return None
    query = urllib.parse.urlencode({"project": project, "task": task_id})
    return f"fleet://attach?{query}"


def _windows_notify(
    cfg: dict[str, Any],
    title: str,
    message: str,
    level: str = DEFAULT_LEVEL,
    *,
    project: str | None = None,
    task_id: str | None = None,
) -> None:
    if cfg.get("enabled") is False:  # default-on
        return
    if platform.system() != "Windows":
        return
    from . import windows_notify_setup as _win_setup

    app_id = _win_setup.chosen_app_id(_WINDOWS_TOAST_APP_ID)
    launch = None
    if app_id == _win_setup.AUMID and _win_setup.is_protocol_configured():
        launch = _windows_launch_uri(project, task_id)
    script = _windows_toast_script(title, message, level, app_id=app_id, launch=launch)
    # -EncodedCommand (base64 UTF-16LE) sidesteps all command-line quoting.
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:  # noqa: BLE001 — best effort
        print(f"warn: Windows notify failed: {e}", file=sys.stderr)
        return
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        print(
            f"warn: Windows notify failed (exit {proc.returncode}): {err[:200]}",
            file=sys.stderr,
        )


def _slack_notify(
    cfg: dict[str, Any], title: str, message: str, level: str = DEFAULT_LEVEL
) -> None:
    if not cfg.get("enabled"):  # default-off
        return
    url = cfg.get("webhook_url")
    if not url:
        return
    emoji = level_emoji(level)
    attachment: dict[str, Any] = {
        "color": level_color(level),
        "fallback": f"{emoji} {title}: {message}",
        "title": f"{emoji} {title}",
        "text": message,
    }
    context = _context_line(title, message)
    if context:
        attachment["footer"] = context
    payload = json.dumps({"attachments": [attachment]}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"warn: slack notify failed: {e}", file=sys.stderr)
