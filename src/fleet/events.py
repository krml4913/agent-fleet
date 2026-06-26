"""Append-only audit log (``events.jsonl``).

Each line is one JSON object. POSIX ``O_APPEND`` makes a single ``write``
atomic only for payloads below ``PIPE_BUF`` (≥ 512 bytes guaranteed;
typically 4 KiB on Linux/macOS). Larger appends can be torn by a
concurrent writer, and parallel drivers (workspace=worktree by default)
do write concurrently. Two consequences follow, both handled here:

* **Writers keep payloads small.** Unbounded free text (a task
  ``description``, a handoff ``message``) is truncated with
  :func:`truncate_text` before it goes on the wire — the full body always
  lives elsewhere (``driver-prompt.md`` / ``inbox.md``), so the event only
  needs an audit-sized snippet. This keeps records well under ``PIPE_BUF``
  and shrinks the torn-write window.
* **Readers tolerate damage.** A torn or truncated line must not take down
  observability (``fleet status`` / the dashboard). :func:`read_events`
  skips malformed lines instead of propagating the exception, mirroring
  the defensive read in ``leader_notifier.read_queue``.

The schema is intentionally open — every record has ``ts`` and ``type``
fields; everything else is event-specific.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Upper bound (in characters) for free-text event fields. Keeps a record's
# serialized line comfortably below PIPE_BUF so an O_APPEND write stays atomic;
# the full text is always retained elsewhere, so this is purely the audit copy.
MAX_EVENT_TEXT = 500


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def truncate_text(text: str, limit: int = MAX_EVENT_TEXT) -> str:
    """Clamp free text destined for an event to ``limit`` characters.

    Long bodies are stored in full elsewhere (``driver-prompt.md`` /
    ``inbox.md``); the event only carries an audit snippet. When truncation
    happens a marker records how much was dropped so the log stays honest.
    """
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [truncated, {len(text)} chars total]"


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
    """Read all events as a list (small files only — for tests / `fleet status`).

    Malformed lines (a torn append, a non-object record) are skipped rather
    than raising, so a single damaged line cannot crash ``fleet status`` or
    the dashboard. Skips are surfaced as one stderr summary per read — loud
    enough to notice a corrupt log, quiet enough not to spam per line.
    """
    if not events_path.exists():
        return []
    out: list[dict[str, Any]] = []
    skipped = 0
    with open(events_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(record, dict):
                skipped += 1
                continue
            out.append(record)
    if skipped:
        print(
            f"warn: skipped {skipped} malformed line(s) in {events_path}",
            file=sys.stderr,
        )
    return out
