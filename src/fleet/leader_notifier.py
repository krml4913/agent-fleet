"""Detached leader-pane notifier: push driver done/gate/ask into the leader.

Sibling of :mod:`fleet.prompt_deliverer`. Where the prompt deliverer waits
for a *driver* pane to reach its CLI prompt and pastes the task prompt, this
module waits for the *leader* pane to go idle (vendor ``ready`` regex) and
injects a coalesced, idempotent summary of finished/gated tasks and of driver
questions (``fleet-agent ask``) so the leader can review / answer without polling.

"Idle" means a real turn boundary — :meth:`VendorAdapter.is_idle` (input prompt
visible, no dialog, no running-turn indicator, AND no unsent human draft in the
composer), seen on two captures
:data:`INJECT_SETTLE_SECONDS` apart with the second taken right before the
keystrokes. The prompt visible alone is not enough: claude keeps its ``❯``
composer on screen while working, so typing then either surfaces mid-turn or
sits unsubmitted in the composer (Issue #288). After the submit Enter the pane is
re-captured and, if the text is still in the composer, Enter is pressed again
(bounded by :data:`SUBMIT_ENTER_RETRIES`).

A draft counts as not idle (Issue #329): the user types in the leader pane too,
and injecting on top of their half-written message merges the two texts (the
leader once received ``1. teams.[fleet] … PR=…/pull/3`` for PR #328: the draft
prefixed, the URL's last digits gone). The records simply stay queued: a draft
that outlives the poller is handled like a busy leader — the deadline re-arms a
successor, nothing is dropped.

The prompt deliverer and the inbox wake-up deliberately keep their own bars. The
deliverer pastes into a freshly booted driver pane that is never mid-turn, so
``is_ready`` is the right test and a busy false-positive there would fail the
task. The inbox nudge (``fleet send`` / handoff) is a one-shot, non-polling write
whose whole purpose is to reach a driver that may be working; the message itself
is durable in ``inbox.md``.

fleet is daemon-less, so there is nothing watching for the leader to become
idle. ``done`` / ``ask`` resolve the task's ``owner_session`` (see
:func:`push_to_leader`) and enqueue a persisted
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

A single multiplexer hiccup must not strand the queue either (Issue #292): a
``MuxError`` from ``capture`` and one ``session_exists() == False`` are transient
(zellij briefly lists panes inconsistently while another tab is closed, see
docs/windows-support.md §4.8). The poller keeps going until the deadline and
re-arms like the busy case; it gives up only once the session is *confirmed*
gone by :data:`SESSION_RECHECKS` checks in a row. Its decisions (spawn, lock
contention, non-idle reasons, flush result, deadline / re-arm, exit reason) are
appended at low volume to ``leader-notifier.log`` next to the queue so a delayed
notification can be explained after the fact.

The leader pane is addressed as ``fleet-<label>:leader``, and a window name is
easy to lose: closing the leader tab and starting the leader again leaves a new
tab with the multiplexer's default name (Issue #302), so every capture failed
for a day. When ``capture`` fails and the ``leader`` window is missing,
:func:`heal_leader_window` looks for the pane titled with the leader agent's
session name (``<label>-leader``, which survives a rename), renames its window
back to ``leader`` and carries on. :func:`pending_summary` exposes what is still
queued so ``fleet status`` / ``fleet sessions`` can show a stranded queue.
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
from datetime import datetime, timezone
from pathlib import Path

from . import agents, formation, heartbeat, mux, state as state_mod
from .adapters import REGISTRY, VendorAdapter
from .events import append_event, utcnow_iso
from .locking import atomic_update, lock_file, unlock_file
from .proc import spawn_detached

DEFAULT_TIMEOUT_SECONDS = 10 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
# Gap between the first idle capture and the confirming capture taken right
# before the keystrokes: a leader must look idle on both, so a turn boundary
# that is only a momentary gap between tool calls, or a just-finished turn's
# trailing render, does not get the injected keystrokes.
INJECT_SETTLE_SECONDS = 0.5
# After the submit Enter, wait this long, re-capture, and if the text is still
# in the composer press Enter again (at most this many times), mirroring the
# prompt deliverer's ``submit_retries``.
SUBMIT_VERIFY_SECONDS = 0.5
SUBMIT_ENTER_RETRIES = 1
# A missing session is only believed after this many ``session_exists`` checks in
# a row (``SESSION_RECHECK_SECONDS`` apart) all say so: one False is transient.
SESSION_RECHECKS = 3
SESSION_RECHECK_SECONDS = 0.5

QUEUE_NAME = "leader-pending.jsonl"
LOCK_NAME = "leader-notifier.lock"
LOG_NAME = "leader-notifier.log"

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
# Decision log (leader-notifier.log)
# ---------------------------------------------------------------------------


def log_path(session_dir: Path) -> Path:
    """The session's notifier log; also the detached notifier's stdout/stderr."""
    return Path(session_dir) / LOG_NAME


def _log(session_dir: Path, message: str) -> None:
    """Append one timestamped line to ``leader-notifier.log``. Never raises."""
    line = f"{utcnow_iso()} [pid {os.getpid()}] {message}\n"
    try:
        with log_path(session_dir).open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


class _ReasonLog:
    """Logs a poll-loop reason only when it changes, so a long wait stays quiet."""

    def __init__(self, session_dir: Path) -> None:
        self._session_dir = session_dir
        self._last: str | None = None

    def note(self, reason: str) -> None:
        if reason != self._last:
            self._last = reason
            _log(self._session_dir, reason)


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


KIND_DONE = "done"
KIND_ASK = "ask"
# The prompt deliverer gave up (Issue #289): the driver never got its prompt.
KIND_DELIVERY_FAILED = "delivery_failed"
# Kinds that carry no PR: never pay for a PR lookup.
_NO_PR_KINDS = (KIND_ASK, KIND_DELIVERY_FAILED)


def build_record(
    *,
    state_dir: Path,
    task_id: str,
    status: str,
    branch: str | None,
    worktree: str | None,
    summary: str,
    result: str | None = None,
    kind: str = KIND_DONE,
    question: str | None = None,
    project: str | None = None,
) -> dict:
    """Build an idempotent pending-notification record for ``task_id``.

    Carries everything the leader needs to no-op an already-handled task and,
    otherwise, to make its first move: "pull the diff and run the gate" for a
    ``done`` record, "answer the question via ``fleet-agent inbox``" for an
    ``ask`` record (``kind="ask"``, carrying the driver's ``question``), "resend
    the prompt with ``fleet-agent send-prompt``" for a ``delivery_failed`` record.

    Records are independent: the only identity is the per-record ``nonce``, so an
    ask and a later done (or gate) for the same task are both delivered.

    ``state_dir`` here is the **project** state dir the task lives in. It is
    recorded on the record so the (session-keyed, cross-project) notifier can
    re-scan that task's outbox at flush time — the queue itself lives under the
    owner session's dir, away from any one project (Issue #166 §10.3).
    ``project`` is the project's name, needed to render the ``--project`` flag of
    the answer command for an ask.
    """
    record: dict = {
        "nonce": uuid.uuid4().hex,
        "ts": utcnow_iso(),
        "task_id": task_id,
        "kind": kind,
        "status": status,
        "branch": branch,
        "worktree": worktree,
        "state_dir": str(state_dir),
        # An ask / delivery failure needs no diff, so it never pays for the PR lookup.
        "pr_url": None if kind in _NO_PR_KINDS else scan_pr_url(state_dir, task_id),
        "summary": summary,
    }
    if project:
        record["project"] = project
    if question is not None:
        record["question"] = question
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
# Producer side (shared by ``done`` and ``ask``)
# ---------------------------------------------------------------------------


def truthy(value: object) -> bool:
    """Parse a ``project.yaml`` flag (``true`` / ``1`` / ``yes`` / ``on``)."""
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def push_to_leader(
    state_dir: Path,
    task_id: str,
    task: dict,
    project: dict,
    project_name: str,
    *,
    status: str,
    summary: str,
    result: str | None = None,
    kind: str = KIND_DONE,
    question: str | None = None,
) -> None:
    """Opt-in leader-pane push, routed by the task's ``owner_session``.

    Shared by ``fleet-agent done`` (``kind="done"``), ``fleet-agent ask``
    (``kind="ask"``) and the prompt deliverer's failure report
    (``kind="delivery_failed"``). Default OFF (``notify_leader_on_driver_done``) → zero
    behaviour change. Always enqueues a persisted record (never dropped) into the
    owner session's queue when the feature is on, then best-effort spawns the
    detached notifier against the ``fleet-<label>`` pane. multiplexer/leader
    absence only leaves the record queued — it never errors the caller.

    Routing is keyed by ``owner_session`` (Issue #166 §10.3): the queue lives under
    that session's dir and the agent ``ready`` regex is read from its record. A
    missing ``owner_session`` is treated as ``main`` (:func:`state.task_owner_session`).
    """
    if not truthy(project.get("notify_leader_on_driver_done")):
        return

    label = state_mod.task_owner_session(task)
    session_dir = state_mod.session_dir(label)

    try:
        record = build_record(
            state_dir=state_dir,
            task_id=task_id,
            status=status,
            branch=task.get("branch"),
            worktree=task.get("worktree"),
            summary=summary,
            result=result,
            kind=kind,
            question=question,
            project=project_name,
        )
        enqueue(session_dir, record)
    except Exception:
        # The queue is the durable path; if even that fails, do not break the caller.
        return

    # Spawn the detached notifier only when the leader pane is resolvable.
    # Otherwise the record stays queued for the next done / ask / re-attach.
    try:
        m = mux.get()
        if not m.available():
            return
        session = f"fleet-{label}"
        if not m.session_exists(session):
            return
        leader_session = formation.read_leader_session(label)
        if not leader_session or not leader_session.get("agent"):
            return
        start_detached(
            session_dir=session_dir,
            session=session,
            window="leader",
            agent_spec=leader_session["agent"],
            reason="enqueue",
        )
    except Exception:
        return


# ---------------------------------------------------------------------------
# Coalesced injection text
# ---------------------------------------------------------------------------


def _is_ask(record: dict) -> bool:
    return record.get("kind") == KIND_ASK


def _render_ask(record: dict) -> str:
    """One ask record: the question plus the exact command that answers it."""
    task_id = record.get("task_id", "?")
    question = " ".join(str(record.get("question") or "").split())  # keep it single-line
    project = record.get("project")
    project_flag = f" --project {project}" if project else ""
    return (
        f"task-{task_id} [ask] question: {question or '(empty)'}"
        f' | answer with: fleet-agent inbox {task_id} "<answer>"{project_flag}'
    )


def _is_delivery_failed(record: dict) -> bool:
    return record.get("kind") == KIND_DELIVERY_FAILED


def _render_delivery_failed(record: dict) -> str:
    """One delivery-failed record: what happened plus the command that retries it."""
    task_id = record.get("task_id", "?")
    summary = " ".join(str(record.get("summary") or "").split())  # keep it single-line
    project = record.get("project")
    project_flag = f" --project {project}" if project else ""
    return (
        f"task-{task_id} [delivery failed] {summary or 'the driver prompt was not delivered'}"
        f" | retry with: fleet-agent send-prompt {task_id}{project_flag}"
    )


def _render_done(record: dict) -> str:
    parts = [f"task-{record.get('task_id', '?')} [{record.get('status', '?')}]"]
    summary = (record.get("summary") or "").strip()
    if summary:
        parts.append(summary)
    # result= is the driver's self-reported flag, not a gate decision — omit to
    # avoid confusion with a user_approval outcome.
    if record.get("branch"):
        parts.append(f"branch={record['branch']}")
    if record.get("worktree"):
        parts.append(f"worktree={record['worktree']}")
    parts.append(f"PR={record.get('pr_url') or '(none yet)'}")
    return " ".join(parts)


_GATE_INSTRUCTION = "pull the diff and run the gate. Skip any task already completed+merged."
_ASK_INSTRUCTION = (
    "answer the driver's question with the fleet-agent inbox command shown, or relay it "
    "to the user if it is not yours to decide. Skip any task no longer awaiting_orders."
)
_DELIVERY_FAILED_INSTRUCTION = (
    "the driver never got its prompt; check its pane and retry with the fleet-agent "
    "send-prompt command shown, or relay to the user. Skip any task no longer failed."
)


def render_block(records: list[dict]) -> str:
    """Coalesce N records into ONE single-line, auto-submittable block.

    Single-line on purpose: typing text into a pane (tmux ``send-keys``) turns an
    embedded newline into Enter, which would submit prematurely. So fields are joined inline and the
    whole block is submitted with one trailing Enter.

    The lead-in instruction follows the record kinds: a done / gate entry says to
    pull the diff and run the gate; an ``[ask]`` entry says to answer it (never
    "run the gate", which makes no sense for a question); a ``[delivery failed]``
    entry says to retry with ``fleet-agent send-prompt``.
    """
    n = len(records)
    asks = sum(1 for r in records if _is_ask(r))
    fails = sum(1 for r in records if _is_delivery_failed(r))
    if fails == 0:
        if asks == 0:
            instruction = f"for each: {_GATE_INSTRUCTION}"
        elif asks == n:
            instruction = f"for each: {_ASK_INSTRUCTION}"
        else:
            instruction = (
                f"for each entry NOT marked [ask]: {_GATE_INSTRUCTION} "
                f"For each [ask] entry: {_ASK_INSTRUCTION}"
            )
    elif fails == n:
        instruction = f"for each: {_DELIVERY_FAILED_INSTRUCTION}"
    else:
        parts = []
        if n - asks - fails:
            parts.append(
                f"for each entry NOT marked [ask] or [delivery failed]: {_GATE_INSTRUCTION}"
            )
        if asks:
            parts.append(f"For each [ask] entry: {_ASK_INSTRUCTION}")
        parts.append(f"For each [delivery failed] entry: {_DELIVERY_FAILED_INSTRUCTION}")
        instruction = " ".join(parts)
    head = f"[fleet] {n} driver notification(s) — {instruction}"
    segs = [_render_record(r) for r in records]
    return head + " :: " + " || ".join(segs)


def _render_record(record: dict) -> str:
    if _is_ask(record):
        return _render_ask(record)
    if _is_delivery_failed(record):
        return _render_delivery_failed(record)
    return _render_done(record)


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
    reason: str = "enqueue",
) -> Path:
    """Spawn a detached notifier that flushes the session's queue into its pane.

    Defensive: callers should already have enqueued the record. If a notifier
    is already running it will no-op (lock), so spawning is always safe.
    ``reason`` (``enqueue`` / ``re-arm``) only labels the spawn in the log.
    """
    log_file = log_path(session_dir)
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
    proc = spawn_detached(args, cwd=repo_root, env=env, log_path=log_file)
    _log(
        session_dir,
        f"spawn ({reason}): notifier pid={getattr(proc, 'pid', '?')} "
        f"session={session} timeout={timeout:g}s",
    )
    return log_file


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
        _log(session_dir, "lock held by another notifier; exiting (it will flush the queue)")
        return 0

    _log(
        session_dir,
        f"started: session={session} window={window} agent={agent_spec} "
        f"timeout={timeout:g}s poll={poll_interval:g}s",
    )
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
                reason="re-arm",
            )
        except Exception as e:  # noqa: BLE001 - re-arm is best-effort; queue persists
            _log(session_dir, f"re-arm failed ({e}); queue stays pending for the next spawn")
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
    leader session not confirmed gone — i.e. the leader was busy (or the mux was
    flaky) the whole time and the caller should re-arm a successor. Returns
    ``False`` on every other terminal: queue drained, session confirmed gone, or a
    send that failed on the mux (left queued for the next ``done`` to re-spawn).

    A ``MuxError`` from ``capture`` and a single ``session_exists() == False`` are
    transient (Issue #292): they are retried on the next poll, never read as "the
    leader went away". Holds no lock itself; the caller owns the session lock for
    our lifetime.
    """
    vendor, _model = agents.parse_spec(agent_spec)
    adapter = REGISTRY[vendor]
    deadline = time.monotonic() + max(0.0, timeout)
    reasons = _ReasonLog(session_dir)

    while time.monotonic() <= deadline:
        if not read_queue(session_dir):
            _log(session_dir, "exit: queue empty")
            return False  # nothing pending → done
        backend = mux.get()
        if not backend.session_exists(session):
            if session_confirmed_gone(backend, session):
                _log(session_dir, f"exit: session {session} confirmed gone; records stay queued")
                return False  # leader detached → leave queued, re-spawn later
            reasons.note(f"session_exists({session}) was False once but is back; keep polling")
        try:
            pane = backend.capture(session, window)
        except mux.MuxError as e:
            healed, note = heal_leader_window(backend, session, window, session_dir)
            if healed:
                continue  # the window is back under its name: capture again at once
            reason = f"capture failed (transient, retrying until the deadline): {e}"
            reasons.note(f"{reason}; {note}" if note else reason)
            time.sleep(max(0.1, poll_interval))
            continue

        if adapter.is_idle(pane):
            flushed = _flush_once(session_dir, session, window, adapter, reasons)
            if flushed is None:
                pass  # not stably idle: keep polling
            elif flushed:
                # Loop again: a record may have been enqueued mid-flush.
                continue
            else:
                _log(session_dir, "exit: send failed on the mux; records stay queued")
                return False  # send failed (mux) → leave queued
        else:
            reasons.note(_not_idle_reason(adapter, pane))
        time.sleep(max(0.1, poll_interval))

    # Deadline hit while still busy. Re-arm only if there is pending work AND the
    # leader session is not confirmed gone: a dead session needs no successor (the
    # next done / re-attach re-spawns) and an empty queue is already delivered.
    pending = len(read_queue(session_dir))
    rearm = bool(pending) and not session_confirmed_gone(mux.get(), session)
    _log(
        session_dir,
        f"deadline {timeout:g}s reached: {pending} record(s) pending; "
        + ("re-arming a successor" if rearm else "not re-arming"),
    )
    return rearm


def _not_idle_reason(adapter: type[VendorAdapter], pane: str) -> str:
    """Why ``pane`` is not idle, for the log: a human's unsent draft or busy / dialog."""
    if adapter.is_ready(pane) and not adapter.is_busy(pane) and adapter.has_draft(pane):
        return "leader has an unsent draft in its composer; waiting (Issue #329)"
    return "leader not idle (busy or dialog); waiting"


def session_confirmed_gone(backend, session: str) -> bool:
    """True only if ``session_exists`` says no :data:`SESSION_RECHECKS` times in a row.

    One False is not enough: a multiplexer that is briefly inconsistent (zellij
    while another tab closes; its ``session_exists`` folds errors into False) must
    not make the poller give up. A ``MuxError`` from the check itself means
    "unknown", not "gone".
    """
    for attempt in range(SESSION_RECHECKS):
        try:
            if backend.session_exists(session):
                return False
        except mux.MuxError:
            return False
        if attempt < SESSION_RECHECKS - 1:
            time.sleep(SESSION_RECHECK_SECONDS)
    return True


def _session_label(session: str) -> str | None:
    """The label of a ``fleet-<label>`` session; ``None`` for any other session."""
    prefix = "fleet-"
    return session[len(prefix):] if session.startswith(prefix) and len(session) > len(prefix) else None


def title_names_agent(title: str, name: str) -> bool:
    """True if ``title`` carries ``name`` as a whole token (case-insensitive).

    A terminal title is usually decorated (``✳ main-leader``, ``main-leader -
    Claude``), so this is a token match rather than equality. ``-`` and word
    characters are not boundaries, which keeps ``main-leader`` from matching a
    driver's ``<project>-<task>-<role>`` name such as ``main-leader-implementer``.
    """
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", title, re.IGNORECASE) is not None


def heal_leader_window(
    backend, session: str, window: str, session_dir: Path
) -> tuple[bool, str | None]:
    """Find a renamed / recreated leader window by its pane title and rename it back.

    Called when the leader pane cannot be reached by ``session:window`` (Issue
    #302: a closed and recreated tab keeps the multiplexer's default name, and
    the name was the only thing the notifier looked the leader up by). The
    leader agent is launched with the session name ``<label>-leader``
    (:func:`state.leader_agent_name`), which shows as the pane title and survives
    a window rename, so the pane whose title carries that name is the leader.

    Returns ``(healed, note)``: ``healed`` is True once the window has been
    renamed to ``window`` (logged); otherwise ``note`` says why not (``None``
    when the window is present — the failure was something else — or when the
    session is not a ``fleet-<label>`` one, which is never touched). Deliberately
    conservative: it renames only when exactly one window holds a matching pane,
    so a driver pane is never adopted and an ambiguous match is left alone.
    Never raises.
    """
    label = _session_label(session)
    if label is None:
        return False, None
    name = state_mod.leader_agent_name(label)
    try:
        if window in backend.list_windows(session):
            return False, None
        matches = [p for p in backend.list_panes(session) if title_names_agent(p.title, name)]
        if not matches:
            return False, f"window {window!r} is missing and no pane is titled {name!r}"
        found = {p.window_id: p.window for p in matches}
        if len(found) > 1:
            return False, (
                f"window {window!r} is missing and {len(found)} windows hold a pane "
                f"titled {name!r} ({', '.join(sorted(found.values()))}); not renaming any"
            )
        (window_id, old_name), = found.items()
        backend.rename_window(session, window_id, window)
    except mux.MuxError as e:
        return False, f"could not recover the leader window {window!r}: {e}"
    _log(
        session_dir,
        f"leader window {window!r} was missing; the pane titled {name!r} is in window "
        f"{old_name!r} (id {window_id}): renamed it back to {window!r}",
    )
    return True, None


def pending_summary(session_dir: Path) -> dict | None:
    """Count and age of a session's pending notifications, or ``None`` when none.

    ``{"count": n, "oldest_ts": <iso or None>, "oldest_age_seconds": <float or None>}``.
    The age is of the oldest record with a parseable ``ts`` — how long the leader
    has been unaware of it; surfaced by ``fleet status`` / ``fleet sessions`` so a
    stranded queue is visible without reading the notifier log.
    """
    records = read_queue(session_dir)
    if not records:
        return None
    now = datetime.now(timezone.utc)
    oldest: tuple[datetime, str] | None = None
    for rec in records:
        ts = rec.get("ts")
        parsed = heartbeat.parse_ts(ts) if isinstance(ts, str) else None
        if parsed is not None and (oldest is None or parsed < oldest[0]):
            oldest = (parsed, ts)
    return {
        "count": len(records),
        "oldest_ts": oldest[1] if oldest else None,
        "oldest_age_seconds": max(0.0, (now - oldest[0]).total_seconds()) if oldest else None,
    }


def describe_pending(summary: dict) -> str:
    """``2 leader notifications pending (oldest 3h ago)`` for a :func:`pending_summary`."""
    n = summary["count"]
    text = f"{n} leader notification{'' if n == 1 else 's'} pending"
    age = summary.get("oldest_age_seconds")
    if age is not None:
        text += f" (oldest {heartbeat.humanize_age(age)})"
    return text


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
        if rec.get("pr_url") or rec.get("kind") in _NO_PR_KINDS:
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


def _flush_once(
    session_dir: Path,
    session: str,
    window: str,
    adapter: type[VendorAdapter],
    reasons: _ReasonLog | None = None,
) -> bool | None:
    """Inject the current queue once and clear exactly what was flushed.

    The caller saw the pane idle; after the (slow) PR re-scan and the settle
    delay the pane is captured again and must still be idle, right before the
    keystrokes. After the submit Enter, :func:`_ensure_submitted` checks the
    text left the composer.

    Returns True on a successful injection (or empty queue), False if the mux
    failed while sending — in which case records are left untouched/queued (the
    text may be half typed, so no retry that could double-inject) — and None if
    nothing was typed and the caller should keep polling: the leader was no longer
    idle on the confirming capture, or that capture failed on the mux (transient,
    Issue #292). Records stay queued for the next idle boundary.
    """
    records = read_queue(session_dir)
    if not records:
        return True
    reasons = reasons or _ReasonLog(session_dir)
    _refill_pr_urls(records)
    text = render_block(records)
    backend = mux.get()
    time.sleep(INJECT_SETTLE_SECONDS)
    try:
        still_idle = adapter.is_idle(backend.capture(session, window))
    except mux.MuxError as e:
        reasons.note(f"confirming capture failed (transient, nothing typed): {e}")
        return None
    if not still_idle:
        reasons.note("leader idle on the first capture only; nothing typed, waiting")
        return None
    try:
        backend.send_text(session, window, text, enter=True)
    except mux.MuxError as e:
        _log(session_dir, f"send_text failed on the mux ({e}); {len(records)} record(s) left queued")
        return False
    enter_retries, submitted = _ensure_submitted(backend, session, window, adapter, text)
    nonces = {r.get("nonce") for r in records if r.get("nonce")}
    clear_records(session_dir, nonces)
    append_event(
        Path(session_dir) / "events.jsonl",
        "leader_notified",
        window=window,
        count=len(records),
        task_ids=[r.get("task_id") for r in records],
        submit_confirmed=submitted,
        enter_retries=enter_retries,
    )
    _log(
        session_dir,
        f"flushed {len(records)} record(s) {[r.get('task_id') for r in records]}: "
        f"submit_confirmed={submitted} enter_retries={enter_retries}",
    )
    return True


def _ensure_submitted(
    backend, session: str, window: str, adapter: type[VendorAdapter], text: str
) -> tuple[int, bool]:
    """After the submit Enter, re-press it (bounded) while ``text`` is still in the composer.

    Returns ``(enter_retries_used, submitted)``. A busy claude can swallow the
    Enter, leaving the injected text typed but unsent (Issue #288); a human then
    submits it by hand and the leader sees it twice. A mux failure while
    verifying is not fatal — the text is already typed and submitted once — so
    it reports what is known and stops.
    """
    retries = 0
    try:
        while True:
            time.sleep(SUBMIT_VERIFY_SECONDS)
            if not adapter.composer_holds(backend.capture(session, window), text):
                return retries, True
            if retries >= SUBMIT_ENTER_RETRIES:
                return retries, False
            backend.send_key(session, window, "Enter")
            retries += 1
    except mux.MuxError:
        return retries, False


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
