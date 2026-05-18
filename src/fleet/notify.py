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
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILE = "notify.yaml"


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


def send(state_dir: Path, title: str, message: str) -> None:
    cfg = load_config(state_dir)
    _macos_notify(cfg.get("macos") or {}, title, message)
    _slack_notify(cfg.get("slack") or {}, title, message)


def _macos_notify(cfg: dict[str, Any], title: str, message: str) -> None:
    if cfg.get("enabled") is False:  # default-on
        return
    if platform.system() != "Darwin":
        return
    if not shutil.which("osascript"):
        return
    safe_msg = message.replace('"', '\\"').replace("\n", " ")[:300]
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


def _slack_notify(cfg: dict[str, Any], title: str, message: str) -> None:
    if not cfg.get("enabled"):  # default-off
        return
    url = cfg.get("webhook_url")
    if not url:
        return
    payload = json.dumps({"text": f"*{title}*\n{message}"}).encode("utf-8")
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
