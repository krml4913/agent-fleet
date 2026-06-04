"""Best-effort notifications: macOS Notification Center + Slack webhook.

Configuration lives in ``<state_dir>/notify.yaml`` (created on demand;
its absence means "default settings — silent slack, native macOS"):

```yaml
macos:
  enabled: true
slack:
  enabled: true
  webhook_url: "https://hooks.slack.com/..."
```

Both transports are best-effort — failures are reported to stderr but
never raise. Nothing in this module imports anything heavy.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

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
) -> None:
    if os.environ.get("FLEET_NO_NOTIFY"):
        return
    cfg = load_config(state_dir)
    _macos_notify(cfg.get("macos") or {}, title, message, level)
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
