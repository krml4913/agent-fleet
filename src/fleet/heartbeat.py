"""Derive 'last seen' info from events.jsonl.

Drivers should call ``fleet-agent event emit heartbeat`` (or any event —
heartbeat is just the convention) between long tool calls. This module
exposes helpers that the dashboard and ``fleet status`` use to surface
who's been quiet.

Design choice: **no daemon, no auto-cleanup**. forge's lifecycle layer
sprouted six separate ticks (heartbeat / liveness / tamagotchi / ...);
agent-fleet exposes the data and lets the human decide. (See design
doc §10.2 "lifecycle 6 features" line.)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_ts(ts: str | None) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def last_per_task(events: list[dict[str, Any]]) -> dict[str, str]:
    """Map ``task_id`` → humanised "last seen" string ("12s ago", "3m ago"…)."""
    latest: dict[str, str] = {}
    for ev in events:
        tid = ev.get("task_id")
        ts = ev.get("ts")
        if tid is None or not ts:
            continue
        # events.jsonl is append-only → later occurrences win naturally.
        latest[str(tid)] = ts

    now = datetime.now(timezone.utc)
    out: dict[str, str] = {}
    for tid, ts in latest.items():
        parsed = parse_ts(ts)
        if parsed is None:
            out[tid] = ts
        else:
            out[tid] = humanize_age((now - parsed).total_seconds())
    return out


def humanize_age(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"
