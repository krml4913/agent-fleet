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

A leader that stays busy past one poller's lifetime must not strand the queue.
When the poll loop hits its deadline with records still pending and the leader
session still alive, it re-arms: it hands off to a fresh detached notifier that
keeps watching for the next idle boundary. Each poller stays short-lived (daemon-
less), but the chain guarantees a busy leader is eventually caught — the queue is
only dropped from a *retirement* path (``merge`` / ``cleanup`` call
:func:`clear_task_records`), never silently on timeout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import agents, mux, state as state_mod
from .adapters import REGISTRY
from .events import append_event, utcnow_iso
from .locking import atomic_update, lock_file, unlock_file
from .proc import spawn_detached

DEFAULT_TIMEOUT_SECONDS = 10 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
# Let the composer settle after the leader goes idle before submitting, so a
# just-finished turn's trailing render does not eat the injected keystrokes.
INJECT_SETTLE_SECONDS = 0.5

QUEUE_NAME = "leader-pending.jsonl"
LOCK_NAME = "leader-notifier.lock"

# Best-effort PR-URL scrape from a task's outbox.md.
PR_URL_RE = re.compile(r"https://github\.com/[^\s)\]]+/pull/\d+")
# Drivers often write just "PR #280" (or "pull request #280") instead of a URL.
PR_NUM_RE = re.compile(r"\b(?:PR|pull\s+request)\s*#(\d+)", re.IGNORECASE)
# owner/repo out of a github.com origin remote: https, ssh:// and scp-style forms.
_GITHUB_REMOTE_RE = re.compile(
    r"github\.com(?::\d+/|[:/]+)(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?\s*$", re.IGNORECASE
)
# git / gh lookups are best-effort: keep them short so they can never stall a caller.
LOOKUP_TIMEOUT_SECONDS = 5.0
_ORIGIN_CACHE: dict[str, str] = {}


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


def _run_capture(argv: list[str], cwd: Path | str | None) -> str | None:
    """Run ``argv`` and return its stdout, or ``None`` on any failure / timeout.

    Best-effort helper for the PR lookups: never raises, never prompts (stdin is
    closed), and gives up after :data:`LOOKUP_TIMEOUT_SECONDS`.
    """
    try:
        proc = subprocess.run(  # noqa: S603 - argv is constructed, no shell.
            argv,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LOOKUP_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 - OSError, TimeoutExpired, ... all best-effort
        return None
    return proc.stdout if proc.returncode == 0 else None


def _lookup_dirs(state_dir: Path, task: dict | None) -> list[Path]:
    """Directories a git/gh lookup may run in: the project repo, then the task worktree."""
    dirs: list[Path] = []
    repo = state_mod.project_repo_dir(state_dir)
    if repo is not None:
        dirs.append(repo)
    worktree = (task or {}).get("worktree")
    if worktree and Path(str(worktree)).is_dir():
        dirs.append(Path(str(worktree)))
    return dirs


def _github_repo_slug(remote_url: str) -> str | None:
    """``owner/repo`` for a github.com remote URL (https / ssh forms), else ``None``."""
    m = _GITHUB_REMOTE_RE.search(remote_url.strip())
    return f"{m.group('owner')}/{m.group('repo')}" if m else None


def _origin_repo_slug(dirs: list[Path]) -> str | None:
    """``owner/repo`` of the ``origin`` remote of the first of ``dirs`` that has one."""
    for d in dirs:
        key = str(d)
        slug = _ORIGIN_CACHE.get(key)
        if slug is None:
            out = _run_capture(["git", "remote", "get-url", "origin"], d)
            slug = _github_repo_slug(out) if out else None
            if slug:
                _ORIGIN_CACHE[key] = slug
        if slug:
            return slug
    return None


def _gh_pr_url_for_branch(branch: str, dirs: list[Path]) -> str | None:
    """Look the PR up by head branch with ``gh`` (newest first); ``None`` if unavailable."""
    gh = shutil.which("gh")
    if not gh or not branch:
        return None
    argv = [gh, "pr", "list", "--head", branch, "--state", "all", "--json", "url", "--limit", "1"]
    for d in dirs:
        out = _run_capture(argv, d)
        if out is None:
            continue
        try:
            rows = json.loads(out)
            url = rows[0]["url"]
        except (ValueError, LookupError, TypeError):
            continue
        if isinstance(url, str) and url:
            return url
    return None


def scan_pr_url(
    state_dir: Path,
    task_id: str,
    *,
    branch: str | None = None,
    use_gh: bool = False,
) -> str | None:
    """Best-effort: return the PR URL of ``task_id``. Never raises.

    1. the last full ``https://github.com/<owner>/<repo>/pull/<n>`` URL in the
       task's ``outbox.md``;
    2. else the last ``PR #<n>`` mention, expanded with the ``origin`` remote
       (github https / ssh forms);
    3. else, only when ``use_gh`` is set, ``gh pr list --head <branch>`` (``gh``
       on PATH, short timeout). It is a network call, so callers on a hot path
       (``done``, status) leave it off; the detached notifier turns it on.

    ``branch`` defaults to the task's recorded branch.
    """
    try:
        return _scan_pr_url(state_dir, task_id, branch, use_gh)
    except Exception:  # noqa: BLE001 - a PR lookup must never break its caller
        return None


def _scan_pr_url(state_dir: Path, task_id: str, branch: str | None, use_gh: bool) -> str | None:
    outbox = state_mod.task_dir(state_dir, task_id) / "outbox.md"
    try:
        text = outbox.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        text = ""

    urls = list(PR_URL_RE.finditer(text))
    if urls:
        return urls[-1].group(0)

    task: dict | None = None
    try:
        task = state_mod.load_task(state_dir, task_id)
    except Exception:  # noqa: BLE001 - no task.yaml: fall back to what we were given
        pass

    nums = PR_NUM_RE.findall(text)
    if nums:
        slug = _origin_repo_slug(_lookup_dirs(state_dir, task))
        if slug:
            return f"https://github.com/{slug}/pull/{nums[-1]}"

    if use_gh:
        branch = branch or (task or {}).get("branch")
        if branch:
            return _gh_pr_url_for_branch(str(branch), _lookup_dirs(state_dir, task))
    return None


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


def clear_task_records(session_dir: Path, task_id: str) -> int:
    """Drop every queued record for ``task_id``; return how many were removed.

    Called from the retirement path (``merge`` / ``cleanup`` teardown) so a
    retired task can never leave a stale "awaiting approval" record that a later
    notifier would inject. Unlike :func:`clear_records` (matched by ``nonce`` to
    flush exactly what was injected), this matches by ``task_id`` to evict *all*
    of a task's records at once — a multi_stage task may have several queued.

    No-op when the queue is absent (returns 0, creates nothing) so teardown of a
    task that never enqueued anything leaves no empty queue file behind.
    """
    path = queue_path(session_dir)
    if not path.exists():
        return 0

    removed = 0

    def _mutate(old: str) -> str:
        nonlocal removed
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
            if rec.get("task_id") == task_id:
                removed += 1
                continue
            kept.append(stripped)
        return ("\n".join(kept) + "\n") if kept else ""

    atomic_update(path, _mutate)
    return removed


# ---------------------------------------------------------------------------
# Coalesced injection text
# ---------------------------------------------------------------------------


def render_block(records: list[dict]) -> str:
    """Coalesce N records into ONE single-line, auto-submittable block.

    Single-line on purpose: typing text into a pane (tmux ``send-keys``) turns an
    embedded newline into Enter, which would submit prematurely. So fields are joined inline and the
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
        # result= is the driver's self-reported flag, not a gate decision — omit to
        # avoid confusion with a user_approval outcome.
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
    spawn_detached(args, cwd=repo_root, env=env, log_path=log_path)
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

    Returns 0 on a clean exit (flushed, queue empty, or leader absent). Never
    raises on multiplexer trouble; a missing pane just leaves records queued. If the
    leader stays busy through our whole lifetime but the session is still alive
    and records remain, we *re-arm* a successor notifier (after releasing the
    lock) so a busy leader can never strand the queue.
    """
    session_dir = Path(session_dir)
    lock_fp = _acquire_lock(session_dir)
    if lock_fp is None:
        # Another notifier holds the queue; it will flush what we enqueued.
        return 0

    try:
        rearm = _poll_until_idle(
            session_dir=session_dir,
            session=session,
            window=window,
            agent_spec=agent_spec,
            timeout=timeout,
            poll_interval=poll_interval,
        )
    finally:
        _release_lock(lock_fp)

    if rearm:
        # Hand off to a fresh notifier that keeps watching for the next idle
        # boundary. Spawn only AFTER releasing the lock, or the successor would
        # fail to acquire it and no-op immediately — leaving nothing watching.
        try:
            start_detached(
                session_dir=session_dir,
                session=session,
                window=window,
                agent_spec=agent_spec,
                timeout=timeout,
                poll_interval=poll_interval,
            )
        except Exception:  # noqa: BLE001 - re-arm is best-effort; queue persists
            pass
    return 0


def _poll_until_idle(
    *,
    session_dir: Path,
    session: str,
    window: str,
    agent_spec: str,
    timeout: float,
    poll_interval: float,
) -> bool:
    """Poll until the queue drains, the leader goes away, or the deadline expires.

    Returns ``True`` iff the deadline expired with records still pending and the
    leader session still alive — i.e. the leader was busy the whole time and the
    caller should re-arm a successor. Returns ``False`` on every other terminal:
    queue drained, leader detached, window gone, or a flush that failed on the mux
    (left queued for the next ``done`` to re-spawn). Holds no lock itself; the
    caller owns the session lock for our lifetime.
    """
    vendor, _model = agents.parse_spec(agent_spec)
    adapter = REGISTRY[vendor]
    deadline = time.monotonic() + max(0.0, timeout)

    while time.monotonic() <= deadline:
        if not read_queue(session_dir):
            return False  # nothing pending → done
        if not mux.get().session_exists(session):
            return False  # leader detached → leave queued, re-spawn later
        try:
            pane = mux.get().capture(session, window)
        except mux.MuxError:
            return False  # window gone → leave queued

        if adapter.is_ready(pane):
            if _flush_once(session_dir, session, window):
                # Loop again: a record may have been enqueued mid-flush.
                continue
            return False  # flush failed (mux) → leave queued
        time.sleep(max(0.1, poll_interval))

    # Deadline hit while still busy. Re-arm only if there is pending work AND the
    # leader session is still alive: a dead session needs no successor (the next
    # done / re-attach re-spawns) and an empty queue is already delivered.
    return bool(read_queue(session_dir)) and mux.get().session_exists(session)


def _refill_pr_urls(records: list[dict]) -> None:
    """Top up missing ``pr_url`` fields by re-scanning each task's outbox.

    Best-effort, in place: a record enqueued at ``done`` time can carry a null
    ``pr_url`` because the driver called ``done`` just before its PR landed in
    ``outbox.md``. By inject time the PR has usually been written, so re-scan
    any record still missing a URL (also trying ``gh pr list`` by branch, which
    is affordable here: the notifier is a detached poller, not ``done``).
    Records that already carry one are left untouched (not re-scanned). Each record carries its own project ``state_dir``
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
            url = scan_pr_url(
                Path(state_dir), task_id, branch=rec.get("branch"), use_gh=True
            )
        except Exception:  # noqa: BLE001 - best-effort; never block the flush
            url = None
        if url:
            rec["pr_url"] = url


def _flush_once(session_dir: Path, session: str, window: str) -> bool:
    """Inject the current queue once and clear exactly what was flushed.

    Returns True on a successful injection (or empty queue), False if the mux
    failed before submit — in which case records are left untouched/queued.
    """
    records = read_queue(session_dir)
    if not records:
        return True
    _refill_pr_urls(records)
    text = render_block(records)
    try:
        time.sleep(INJECT_SETTLE_SECONDS)
        mux.get().send_text(session, window, text, enter=True)
    except mux.MuxError:
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
    fp = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115 - released in _release_lock
    try:
        acquired = lock_file(fp, blocking=False)
    except OSError:
        acquired = False
    if not acquired:
        fp.close()
        return None
    return fp


def _release_lock(fp) -> None:
    try:
        unlock_file(fp)
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
