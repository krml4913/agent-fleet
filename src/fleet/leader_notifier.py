"""Detached leader-pane notifier: push driver done/gate into the leader.

Sibling of :mod:`fleet.prompt_deliverer`. Where the prompt deliverer waits
for a *driver* pane to reach its CLI prompt and pastes the task prompt, this
module waits for the *leader* pane to go idle (vendor ``ready`` regex) and
injects a coalesced, idempotent summary of finished/gated tasks so the leader
can review without polling.

fleet is daemon-less, so there is nothing watching for the leader to become
idle. ``done`` resolves the task's ``owner_session`` and enqueues a persisted
record into that session's queue (`global/sessions/<label>/leader-pending.jsonl`),
then spawns this detached poller against the ``fleet-<label>`` pane. The queue
survives process exit and leader detach: if the leader pane is absent the records
stay queued and the next ``done`` (or re-attach) re-spawns a notifier that
flushes them. A non-blocking flock (`global/sessions/<label>/leader-notifier.lock`)
keeps only one notifier per session live at a time so we never double-inject.
Routing is keyed by session, not project (Issue #166 §10.3): one notifier flushes
a session's pending notifications across every project that session spawned.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import agents, state as state_mod, tmux
from .adapters import REGISTRY
from .events import append_event, utcnow_iso
from .locking import atomic_update

DEFAULT_TIMEOUT_SECONDS = 10 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
# Let the composer settle after the leader goes idle before submitting, so a
# just-finished turn's trailing render does not eat the injected keystrokes.
INJECT_SETTLE_SECONDS = 0.5

QUEUE_NAME = "leader-pending.jsonl"
LOCK_NAME = "leader-notifier.lock"

# Best-effort PR-URL scrape from a task's outbox.md.
PR_URL_RE = re.compile(r"https://github\.com/[^\s)\]]+/pull/\d+")


# ---------------------------------------------------------------------------
# Queue (persisted, never-drop)
# ---------------------------------------------------------------------------


def queue_path(session_dir: Path) -> Path:
    """Return the session's pending-notification queue (Issue #166 §10.3).

    Keyed by session, not project: the queue lives under
    ``global/sessions/<label>/`` so one notifier flushes a session's pending
    notifications across all the projects that session spawned.
    """
    return Path(session_dir) / QUEUE_NAME


def scan_pr_url(state_dir: Path, task_id: str) -> str | None:
    """Best-effort: return the last PR URL mentioned in the task outbox.md."""
    outbox = state_mod.task_dir(state_dir, task_id) / "outbox.md"
    try:
        text = outbox.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    matches = PR_URL_RE.findall(text)
    return matches[-1] if matches else None


def build_record(
    *,
    state_dir: Path,
    task_id: str,
    status: str,
    branch: str | None,
    worktree: str | None,
    summary: str,
    result: str | None = None,
) -> dict:
    """Build an idempotent pending-notification record for ``task_id``.

    Carries everything the leader needs to no-op an already-handled task and,
    otherwise, to make its first move "pull the diff and run the gate".

    ``state_dir`` here is the **project** state dir the task lives in. It is
    recorded on the record so the (session-keyed, cross-project) notifier can
    re-scan that task's outbox at flush time — the queue itself lives under the
    owner session's dir, away from any one project (Issue #166 §10.3).
    """
    record: dict = {
        "nonce": uuid.uuid4().hex,
        "ts": utcnow_iso(),
        "task_id": task_id,
        "status": status,
        "branch": branch,
        "worktree": worktree,
        "state_dir": str(state_dir),
        "pr_url": scan_pr_url(state_dir, task_id),
        "summary": summary,
    }
    if result:
        record["result"] = result
    return record


def enqueue(session_dir: Path, record: dict) -> None:
    """Append a record to the session's persisted queue (lock-guarded, never lost)."""
    line = json.dumps(record, ensure_ascii=False) + "\n"
    atomic_update(queue_path(session_dir), lambda old: old + line)


def read_queue(session_dir: Path) -> list[dict]:
    """Return all queued records (skips malformed lines)."""
    path = queue_path(session_dir)
    if not path.exists():
        return []
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def clear_records(session_dir: Path, nonces: set[str]) -> None:
    """Remove exactly the flushed records, preserving any appended meanwhile.

    Records are matched by ``nonce`` so a record enqueued *during* an injection
    is never dropped — only what we actually flushed is cleared.
    """
    if not nonces:
        return

    def _mutate(old: str) -> str:
        kept: list[str] = []
        for raw in old.splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                kept.append(stripped)
                continue
            if rec.get("nonce") in nonces:
                continue
            kept.append(stripped)
        return ("\n".join(kept) + "\n") if kept else ""

    atomic_update(queue_path(session_dir), _mutate)


# ---------------------------------------------------------------------------
# Coalesced injection text
# ---------------------------------------------------------------------------


def render_block(records: list[dict]) -> str:
    """Coalesce N records into ONE single-line, auto-submittable block.

    Single-line on purpose: ``tmux send-keys`` turns an embedded newline into
    Enter, which would submit prematurely. So fields are joined inline and the
    whole block is submitted with one trailing Enter.
    """
    n = len(records)
    head = (
        f"[fleet] {n} driver notification(s) — for each: pull the diff and run "
        f"the gate. Skip any task already completed+merged."
    )
    segs: list[str] = []
    for r in records:
        parts = [f"task-{r.get('task_id', '?')} [{r.get('status', '?')}]"]
        summary = (r.get("summary") or "").strip()
        if summary:
            parts.append(summary)
        if r.get("result"):
            parts.append(f"result={r['result']}")
        if r.get("branch"):
            parts.append(f"branch={r['branch']}")
        if r.get("worktree"):
            parts.append(f"worktree={r['worktree']}")
        parts.append(f"PR={r.get('pr_url') or '(none yet)'}")
        segs.append(" ".join(parts))
    return head + " :: " + " || ".join(segs)


# ---------------------------------------------------------------------------
# Detached spawn
# ---------------------------------------------------------------------------


def _fleet_clone_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "fleet-agent").exists() or (parent / ".git").is_dir():
            return parent
    return here.parent.parent.parent


def start_detached(
    *,
    session_dir: Path,
    session: str,
    window: str,
    agent_spec: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> Path:
    """Spawn a detached notifier that flushes the session's queue into its pane.

    Defensive: callers should already have enqueued the record. If a notifier
    is already running it will no-op (lock), so spawning is always safe.
    """
    log_path = Path(session_dir) / "leader-notifier.log"
    repo_root = _fleet_clone_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(repo_root / "src"),
            str(repo_root / "vendor"),
            env.get("PYTHONPATH", ""),
        ]
    )
    args = [
        sys.executable,
        "-m",
        "fleet.leader_notifier",
        "--session-dir",
        str(session_dir),
        "--session",
        session,
        "--window",
        window,
        "--agent",
        agent_spec,
        "--timeout",
        str(timeout),
        "--poll-interval",
        str(poll_interval),
    ]
    with log_path.open("ab") as log:
        subprocess.Popen(  # noqa: S603 - argv is constructed, no shell.
            args,
            cwd=str(repo_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    return log_path


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------


def notify(
    *,
    session_dir: Path,
    session: str,
    window: str,
    agent_spec: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> int:
    """Poll the leader pane; inject the coalesced queue on the next idle boundary.

    Returns 0 on a clean exit (flushed, queue empty, or leader absent/busy at
    timeout — records stay queued for the next spawn). Never raises on tmux
    trouble; a missing pane just leaves records queued.
    """
    session_dir = Path(session_dir)
    lock_fp = _acquire_lock(session_dir)
    if lock_fp is None:
        # Another notifier holds the queue; it will flush what we enqueued.
        return 0

    try:
        vendor, _model = agents.parse_spec(agent_spec)
        adapter = REGISTRY[vendor]
        deadline = time.monotonic() + max(0.0, timeout)

        while time.monotonic() <= deadline:
            if not read_queue(session_dir):
                return 0  # nothing pending → done
            if not tmux.session_exists(session):
                return 0  # leader detached → leave queued, re-spawn later
            try:
                pane = tmux.capture_pane(session, window)
            except tmux.TmuxError:
                return 0  # window gone → leave queued

            if adapter.ready.search(pane):
                if _flush_once(session_dir, session, window):
                    # Loop again: a record may have been enqueued mid-flush.
                    continue
                return 0  # flush failed (tmux) → leave queued
            time.sleep(max(0.1, poll_interval))

        return 0  # timed out waiting for idle → leave queued
    finally:
        _release_lock(lock_fp)


def _refill_pr_urls(records: list[dict]) -> None:
    """Top up missing ``pr_url`` fields by re-scanning each task's outbox.

    Best-effort, in place: a record enqueued at ``done`` time can carry a null
    ``pr_url`` because the driver called ``done`` just before its PR landed in
    ``outbox.md``. By inject time the PR has usually been written, so re-scan
    any record still missing a URL. Records that already carry one are left
    untouched (not re-scanned). Each record carries its own project ``state_dir``
    (the queue is cross-project), so the re-scan targets the right outbox.
    ``scan_pr_url`` never raises, but guard anyway so a re-scan hiccup can never
    block the injection.
    """
    for rec in records:
        if rec.get("pr_url"):
            continue
        task_id = rec.get("task_id")
        state_dir = rec.get("state_dir")
        if not task_id or not state_dir:
            continue
        try:
            url = scan_pr_url(Path(state_dir), task_id)
        except Exception:  # noqa: BLE001 - best-effort; never block the flush
            url = None
        if url:
            rec["pr_url"] = url


def _flush_once(session_dir: Path, session: str, window: str) -> bool:
    """Inject the current queue once and clear exactly what was flushed.

    Returns True on a successful injection (or empty queue), False if tmux
    failed before submit — in which case records are left untouched/queued.
    """
    records = read_queue(session_dir)
    if not records:
        return True
    _refill_pr_urls(records)
    text = render_block(records)
    try:
        time.sleep(INJECT_SETTLE_SECONDS)
        tmux.send_keys(session, window, text, enter=True)
    except tmux.TmuxError:
        return False
    nonces = {r.get("nonce") for r in records if r.get("nonce")}
    clear_records(session_dir, nonces)
    append_event(
        Path(session_dir) / "events.jsonl",
        "leader_notified",
        window=window,
        count=len(records),
        task_ids=[r.get("task_id") for r in records],
    )
    return True


def _acquire_lock(session_dir: Path):
    """Non-blocking exclusive lock. Returns the fp on success, None if held.

    One notifier per session: the lock lives under ``global/sessions/<label>/``.
    """
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    lock_path = session_dir / LOCK_NAME
    fp = open(lock_path, "a+")  # noqa: SIM115 - released in _release_lock
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        return None
    return fp


def _release_lock(fp) -> None:
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
    finally:
        fp.close()


# ---------------------------------------------------------------------------
# CLI entry (detached subprocess target)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--session-dir", required=True, type=Path)
    p.add_argument("--session", required=True)
    p.add_argument("--window", required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    p.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return notify(
        session_dir=args.session_dir,
        session=args.session,
        window=args.window,
        agent_spec=args.agent,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    raise SystemExit(main())
