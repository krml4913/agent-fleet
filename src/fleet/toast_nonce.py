"""Single-use nonces for the Approve / Reject toast buttons (Issue #318).

Any app or web page can open a ``fleet://`` URL, so a bare
``fleet://approve?project=…&task=…`` must never be enough to settle a gate. An
approval-gate toast therefore carries a nonce that :func:`issue` stores in the
task dir (``toast-nonces.json``), bound to the project, the task and the stage
index the gate was raised at, with an expiry (:data:`TTL`, 24h). The URL handler
(:mod:`fleet.commands.url_handler`) refuses an action whose nonce is missing,
unknown, already used, expired or bound to another stage.

Only a SHA-256 of each nonce is stored. :func:`check` is a read-only pre-flight
(used before a confirmation dialog); :func:`consume` re-validates and marks the
nonce used atomically, so two racing clicks cannot both act.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import state as state_mod
from .locking import atomic_update

STORE_NAME = "toast-nonces.json"
TTL = timedelta(hours=24)

#: What :func:`issue` mints: 32 lowercase hex chars. The URL handler validates
#: the URI's ``nonce`` against this shape before looking anything up.
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")


def store_path(state_dir: Path, task_id: str) -> Path:
    return state_mod.task_dir(state_dir, task_id) / STORE_NAME


def _digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load(text: str) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read(state_dir: Path, task_id: str) -> dict:
    path = store_path(state_dir, task_id)
    try:
        return _load(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def _expired(entry: dict, now: datetime) -> bool:
    try:
        return now >= datetime.fromisoformat(str(entry.get("expires")))
    except ValueError:
        return True


def _refusal(
    entry: object, project: str, task_id: str, stage: int, now: datetime
) -> str | None:
    """Why *entry* may not be used for this project/task/stage, or ``None`` if valid."""
    if not isinstance(entry, dict):
        return "unknown nonce"
    if entry.get("used"):
        return "nonce already used"
    if _expired(entry, now):
        return "nonce expired"
    if entry.get("project") != project or entry.get("task") != task_id:
        return "nonce is bound to another task"
    if entry.get("stage") != stage:
        return "nonce is bound to another stage"
    return None


def issue(
    state_dir: Path,
    project: str,
    task_id: str,
    stage: int,
    *,
    now: datetime | None = None,
) -> str:
    """Mint a nonce bound to ``project`` + ``task_id`` + ``stage`` and persist it.

    Expired and used entries are dropped on the way, so the store stays small.
    """
    now = now or _now()
    nonce = secrets.token_hex(16)

    def _mutate(old: str) -> str:
        entries = {
            k: v
            for k, v in _load(old).items()
            if isinstance(v, dict) and not v.get("used") and not _expired(v, now)
        }
        entries[_digest(nonce)] = {
            "project": project,
            "task": task_id,
            "stage": stage,
            "expires": (now + TTL).isoformat(timespec="seconds"),
            "used": False,
        }
        return json.dumps(entries, indent=2) + "\n"

    atomic_update(store_path(state_dir, task_id), _mutate)
    return nonce


def check(
    state_dir: Path,
    project: str,
    task_id: str,
    stage: int,
    nonce: str,
    *,
    now: datetime | None = None,
) -> str | None:
    """Read-only: the refusal reason for *nonce*, or ``None`` when it is usable."""
    entry = _read(state_dir, task_id).get(_digest(nonce))
    return _refusal(entry, project, task_id, stage, now or _now())


def consume(
    state_dir: Path,
    project: str,
    task_id: str,
    stage: int,
    nonce: str,
    *,
    now: datetime | None = None,
) -> str | None:
    """Validate *nonce* and mark it used in one locked step.

    Returns ``None`` when it was valid (and is now spent), else the refusal
    reason (nothing is marked used then).
    """
    now = now or _now()
    key = _digest(nonce)
    outcome: list[str | None] = [None]

    def _mutate(old: str) -> str:
        entries = _load(old)
        outcome[0] = _refusal(entries.get(key), project, task_id, stage, now)
        if outcome[0] is not None:
            return old
        entries[key]["used"] = True
        return json.dumps(entries, indent=2) + "\n"

    path = store_path(state_dir, task_id)
    if not path.exists():
        return "unknown nonce"
    atomic_update(path, _mutate)
    return outcome[0]
