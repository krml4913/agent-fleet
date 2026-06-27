"""Presentation-agnostic state derivation — the single source of truth.

Moved from ``commands/status.py`` and ``commands/sessions.py`` so that the
HTML renderer (a core module) can import derivation helpers without an inverted
dependency on ``commands/*``.  No ANSI, no HTML, no argparse here.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import heartbeat
from . import leader_notifier
from . import state as state_mod
from . import tmux as tmux_mod
from .events import read_events, utcnow_iso
from . import __version__


# Event types whose latest occurrence marks a task's terminal transition. The
# completion timestamp for the dashboard's Completed section is the max ``ts``
# among a task's events of these types.
_TERMINAL_EVENT_TYPES = frozenset({"merge", "cleanup", "done"})

# Hard cap on rendered completed rows (regardless of window) to bound HTML size.
COMPLETED_ROW_CAP = 200


# ---------------------------------------------------------------------------
# Task / stage derivation (moved verbatim from commands/status.py)
# ---------------------------------------------------------------------------


def current_stage_index(task: dict) -> int:
    """Return the valid current-stage index, or -1 if the task has no stages."""
    stages = task.get("stages")
    if not isinstance(stages, list) or not stages:
        return -1
    idx = task.get("current_stage", 0)
    if not isinstance(idx, int) or idx < 0 or idx >= len(stages):
        return -1
    return idx


def gate_result(task: dict) -> str | None:
    """Return the current stage's gate/approval result, or ``None``."""
    idx = current_stage_index(task)
    stages = task.get("stages")
    if idx < 0 or not isinstance(stages, list):
        return None
    stage = stages[idx]
    if not isinstance(stage, dict):
        return None
    result = stage.get("result")
    if result:
        return str(result)
    ua = stage.get("user_approval")
    if isinstance(ua, dict):
        ua_status = ua.get("status")
        if ua_status in {"approved", "rejected"}:
            return str(ua_status)
    return None


def peer_review_progress(peer_review: object) -> str:
    """Return a short label like ``'review ×2'`` for an in-progress peer review."""
    if not isinstance(peer_review, dict):
        return ""
    if peer_review.get("phase") not in {"implementing", "reviewing"}:
        return ""
    iteration = peer_review.get("iteration")
    if iteration is None or iteration == "":
        return "review"
    return f"review ×{iteration}"


def stage_descriptor(task: dict) -> dict | None:
    """Return a presentation-agnostic stage summary dict, or None.

    Keys: ``index``, ``count``, ``role``, ``agent``, ``review``.
    """
    idx = current_stage_index(task)
    stages = task.get("stages") or []
    if idx < 0:
        return None
    stage = stages[idx]
    if not isinstance(stage, dict):
        return None
    role = str(stage.get("role") or "-")
    agent = str(stage.get("agent") or "-")
    review = peer_review_progress(stage.get("peer_review"))
    return {
        "index": idx,
        "count": len(stages),
        "role": role,
        "agent": agent,
        "review": review,
    }


def status_severity(status: str) -> str:
    """Map a task status string to a canonical severity bucket.

    Returns one of ``'ok'``, ``'active'``, ``'attention'``, ``'neutral'``.
    """
    if status in {"done", "approved", "completed"}:
        return "ok"
    if status in {"running", "spawning"}:
        return "active"
    if status in {"awaiting_orders", "failed", "changes-requested"}:
        return "attention"
    return "neutral"


# ---------------------------------------------------------------------------
# Session derivation (moved verbatim from commands/sessions.py)
# ---------------------------------------------------------------------------


def scan_inflight_tasks() -> dict[str, list[dict]]:
    """Map owner-session label → non-terminal tasks across all projects.

    Each task entry is ``{project, id, status, title}``.
    """
    by_label: dict[str, list[dict]] = {}
    reg = state_mod.load_registry()
    for name in sorted(reg.get("projects", {})):
        state_dir = state_mod.project_state_dir(name)
        if not state_dir.is_dir():
            continue
        for task in state_mod.list_tasks(state_dir):
            status = str(task.get("status", "?"))
            if status in state_mod.TERMINAL_STATUSES:
                continue
            label = state_mod.task_owner_session(task)
            by_label.setdefault(label, []).append(
                {
                    "project": name,
                    "id": str(task.get("id", "?")),
                    "status": status,
                    "title": str(task.get("title") or "-"),
                }
            )
    return by_label


def load_session_records() -> dict[str, dict]:
    """Map session label → its ``session.json`` record."""
    out: dict[str, dict] = {}
    sessions_root = state_mod.global_sessions_dir()
    if not sessions_root.is_dir():
        return out
    for child in sorted(sessions_root.iterdir()):
        if not child.is_dir():
            continue
        rec_path = child / state_mod.SESSION_RECORD_NAME
        if not rec_path.is_file():
            continue
        try:
            data = json.loads(rec_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        out[child.name] = data if isinstance(data, dict) else {}
    return out


def session_liveness(label: str, tmux_ok: bool) -> bool | None:
    """Return True (live), False (stale), or None (tmux unavailable)."""
    if not tmux_ok:
        return None
    return tmux_mod.session_exists(f"fleet-{label}")


# ---------------------------------------------------------------------------
# High-level cross-project snapshot
# ---------------------------------------------------------------------------


def collect_projects() -> list[dict]:
    """Return a per-project summary list for all registered projects."""
    reg = state_mod.load_registry()
    out: list[dict] = []
    for name in sorted(reg.get("projects", {})):
        entry = reg["projects"][name]
        repo = entry.get("repo", "?")
        state_dir = state_mod.project_state_dir(name)
        repo_exists = Path(repo).is_dir()
        state_exists = state_dir.is_dir()

        if not state_exists:
            out.append(
                {
                    "name": name,
                    "repo": repo,
                    "repo_exists": repo_exists,
                    "state_exists": False,
                    "by_status": {},
                    "awaiting": [],
                    "tasks": [],
                }
            )
            continue

        try:
            tasks = state_mod.list_tasks(state_dir)
        except Exception:  # noqa: BLE001
            tasks = []

        try:
            events = read_events(state_dir / "events.jsonl")
            last_seen = heartbeat.last_per_task(events)
        except Exception:  # noqa: BLE001
            last_seen = {}

        by_status: dict[str, int] = {}
        awaiting: list[dict] = []
        task_views: list[dict] = []
        for t in tasks:
            s = str(t.get("status", "?"))
            by_status[s] = by_status.get(s, 0) + 1
            tid = str(t.get("id", "?"))
            pr_url: str | None = None
            try:
                pr_url = leader_notifier.scan_pr_url(state_dir, tid)
            except Exception:  # noqa: BLE001
                pass
            view = {
                "id": tid,
                "title": str(t.get("title") or "-"),
                "status": s,
                "severity": status_severity(s),
                "formation": str(t.get("formation") or "-"),
                "stage": stage_descriptor(t),
                "last_seen": last_seen.get(tid, "—"),
                "pr_url": pr_url,
            }
            task_views.append(view)
            if s == "awaiting_orders":
                awaiting.append(view)

        out.append(
            {
                "name": name,
                "repo": repo,
                "repo_exists": repo_exists,
                "state_exists": True,
                "by_status": by_status,
                "awaiting": awaiting,
                "tasks": task_views,
            }
        )
    return out


def collect_sessions() -> list[dict]:
    """Return a per-session summary list."""
    records = load_session_records()
    tasks_by_label = scan_inflight_tasks()
    labels = sorted(set(records) | set(tasks_by_label))
    tmux_ok = tmux_mod.available()

    out: list[dict] = []
    for label in labels:
        record = records.get(label) or {}
        tasks = tasks_by_label.get(label, [])
        live = session_liveness(label, tmux_ok)
        scope = state_mod.session_scope(label)
        task_views: list[dict] = []
        for t in tasks:
            out_of_scope = scope is not None and t["project"] not in scope
            task_views.append(
                {
                    "project": t["project"],
                    "id": t["id"],
                    "status": t["status"],
                    "title": t["title"],
                    "out_of_scope": out_of_scope,
                }
            )
        out.append(
            {
                "label": label,
                "live": live,
                "agent": record.get("agent", "—"),
                "pane": record.get("pane", f"fleet-{label}:leader"),
                "since": record.get("started_at", ""),
                "has_record": label in records,
                "scope": scope,
                "tasks": task_views,
            }
        )
    return out


def collect_recent_events(limit: int = 15) -> list[dict]:
    """Return the last *limit* events merged across all registered projects."""
    reg = state_mod.load_registry()
    all_events: list[dict] = []
    for name in sorted(reg.get("projects", {})):
        state_dir = state_mod.project_state_dir(name)
        evfile = state_dir / "events.jsonl"
        if not evfile.is_file():
            continue
        try:
            for ev in read_events(evfile):
                all_events.append(dict(ev, project=name))
        except Exception:  # noqa: BLE001
            continue
    all_events.sort(key=lambda e: e.get("ts", ""))
    return all_events[-limit:]


def _terminal_ts_by_task(events: list[dict]) -> dict[str, str]:
    """Map ``task_id`` → max ``ts`` among its terminal events.

    Terminal events are ``merge`` / ``cleanup`` / ``done`` (:data:`_TERMINAL_EVENT_TYPES`).
    The ISO-8601 UTC strings produced by :func:`events.utcnow_iso` sort
    lexicographically by time, so a plain string ``max`` is the latest event.
    """
    latest: dict[str, str] = {}
    for ev in events:
        if ev.get("type") not in _TERMINAL_EVENT_TYPES:
            continue
        tid = ev.get("task_id")
        ts = ev.get("ts")
        if tid is None or not ts:
            continue
        tid = str(tid)
        if tid not in latest or ts > latest[tid]:
            latest[tid] = ts
    return latest


def collect_completed(
    within_days: int = 30, *, row_cap: int = COMPLETED_ROW_CAP
) -> dict:
    """Return archived (terminal) tasks completed within *within_days*.

    Enumerates each project's ``tasks/_archive/task-*/task.yaml``, derives each
    task's completion timestamp from the project ``events.jsonl`` (the max
    ``ts`` of its terminal ``merge`` / ``cleanup`` / ``done`` events, falling
    back to the archive dir mtime), filters to the ``within_days`` window,
    sorts most-recent-first, and caps the result at *row_cap* rows to bound
    HTML size.

    Returns ``{"tasks": [...], "within_days": int, "truncated": int}`` where
    ``truncated`` counts in-window rows dropped by the cap (logged, never
    silently). Each task row carries ``completed_ts`` (ISO) and
    ``completed_epoch`` (int seconds) for the client-side window selector.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=within_days)
    reg = state_mod.load_registry()
    rows: list[dict] = []
    for name in sorted(reg.get("projects", {})):
        state_dir = state_mod.project_state_dir(name)
        if not state_dir.is_dir():
            continue
        try:
            archived = state_mod.list_archived_tasks(state_dir)
        except Exception:  # noqa: BLE001
            continue
        if not archived:
            continue
        try:
            events = read_events(state_dir / "events.jsonl")
        except Exception:  # noqa: BLE001
            events = []
        terminal_ts = _terminal_ts_by_task(events)
        for t in archived:
            tid = str(t.get("id", "?"))
            ts_iso = terminal_ts.get(tid)
            completed_dt = heartbeat.parse_ts(ts_iso) if ts_iso else None
            if completed_dt is None:
                mtime = t.get("archive_mtime")
                if mtime is not None:
                    completed_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            if completed_dt is None or completed_dt < cutoff:
                continue
            status = str(t.get("status") or "?")
            rows.append(
                {
                    "project": name,
                    "id": tid,
                    "title": str(t.get("title") or "-"),
                    "status": status,
                    "severity": status_severity(status),
                    "formation": str(t.get("formation") or "-"),
                    "completed_ts": completed_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "completed_epoch": int(completed_dt.timestamp()),
                }
            )
    rows.sort(key=lambda r: r["completed_ts"], reverse=True)
    truncated = 0
    if len(rows) > row_cap:
        truncated = len(rows) - row_cap
        print(
            f"warn: completed section capped at {row_cap} rows "
            f"({truncated} older in-window row(s) dropped)",
            file=sys.stderr,
        )
        rows = rows[:row_cap]
    return {"tasks": rows, "within_days": within_days, "truncated": truncated}


def collect_global_snapshot() -> dict:
    """Return the full cross-project snapshot used by the HTML renderer."""
    return {
        "generated_at": utcnow_iso(),
        "version": __version__,
        "projects": collect_projects(),
        "sessions": collect_sessions(),
        # Collect extra so the renderer can filter noise and still show
        # RECENT_EVENTS_DISPLAYED significant events.
        "recent_events": collect_recent_events(limit=100),
        # Archived tasks within the largest selector window (30 days); the
        # client-side time-window selector filters within this server-side bound.
        "completed": collect_completed(within_days=30),
    }
