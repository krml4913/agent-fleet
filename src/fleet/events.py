"""Append-only audit log (``events.jsonl``).

Each line is one JSON object. POSIX ``O_APPEND`` guarantees atomic
appends for writes shorter than ``PIPE_BUF`` (≥ 512 bytes; typically
4 KiB on Linux/macOS), which is plenty for our event records.

The schema is intentionally open — every record has ``ts`` and ``type``
fields; everything else is event-specific.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_event(events_path: Path, event_type: str, **fields: Any) -> dict[str, Any]:
    """Append a single event record and return the serialized dict."""
    record: dict[str, Any] = {
        "ts": utcnow_iso(),
        "type": event_type,
    }
    record.update(fields)
    line = json.dumps(record, ensure_ascii=False) + "\n"

    fd = os.open(events_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return record


def read_events(events_path: Path) -> list[dict[str, Any]]:
    """Read all events as a list (small files only — for tests / `fleet status`)."""
    if not events_path.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(events_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out
