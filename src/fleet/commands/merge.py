"""``fleet-agent merge <id>`` — atomic PR merge + teardown + archive.

Retires a finished task in ONE correct-ordered pass, fixing the dogfooded
race where ``gh pr merge --delete-branch`` then ``fleet-agent cleanup``
collide (the worktree still holds the local branch, so ``--delete-branch``
fails and a later cleanup finds a half-removed worktree, leaving the remote
branch for a manual ``git push --delete``).

Order of operations:
  1. Guard — refuse unless the task status is terminal (reuses
     :data:`cleanup.TERMINAL_STATUSES`).
  2. Merge the PR — ``gh pr merge <branch>`` (merge commit by default,
     ``--squash`` to squash) run in the project repo. **No** ``--delete-branch``:
     fleet drops the branch itself after the worktree is gone. If the merge
     fails, stop and report — nothing is torn down, so the worktree survives
     for conflict resolution.
  3. Teardown — the same path ``cleanup`` runs (:func:`cleanup.teardown`):
     workspace ``on_cleanup`` (worktree remove + local ``branch -D``), kill the
     tmux window, drop the prompt buffer.
  4. Delete the remote branch — ``git push origin --delete <branch>`` once the
     worktree is gone. "Already deleted" is treated as success.
  5. Archive by default (merge implies full retire) unless ``--keep``.
  6. Emit a ``merge`` event and rebuild the dashboard.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import cleanup as cleanup_mod
from .. import dashboard as dashboard_mod
from .. import state as state_mod
from .. import task_context
from ..events import append_event


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "merge",
        help="Merge a finished task's PR, then tear it down and archive it",
        description=(
            "Atomically retire a finished task: merge its PR (merge commit by "
            "default, --squash to squash), remove the worktree + local & remote "
            "branch, kill the tmux window, and archive the task dir (unless "
            "--keep). If the merge fails nothing is torn down, so the worktree "
            "survives for conflict resolution. Refuses to run on a non-terminal "
            "task unless --force is passed."
        ),
    )
    p.add_argument(
        "task_id",
        nargs="?",
        default=None,
        help="Task id (default: derived from cwd or FLEET_TASK_ID)",
    )
    p.add_argument("--project", default=".", help="Project path (default: cwd)")
    p.add_argument(
        "--squash",
        action="store_true",
        help="Squash-merge the PR (default: merge commit)",
    )
    p.add_argument(
        "--keep",
        action="store_true",
        help="Keep the task dir instead of archiving it",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Run even if the task isn't in a terminal status",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        state_dir, task_id = task_context.resolve(
            explicit_id=args.task_id,
            cwd=Path(args.project),
        )
    except task_context.TaskNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        task = state_mod.load_task(state_dir, task_id)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    status = task.get("status", "?")
    if status not in cleanup_mod.TERMINAL_STATUSES and not args.force:
        print(
            f"error: refusing to merge task-{task_id} (status={status}); "
            f"pass --force to override.",
            file=sys.stderr,
        )
        return 1

    project = state_mod.load_project(state_dir)
    repo = project.get("repo")
    project_root = Path(repo) if repo else state_dir.parent
    project_name = project.get("name") or state_dir.name
    branch = task.get("branch") or f"{project_name}/task/{task_id}"

    # 1. Merge the PR. No --delete-branch: fleet drops the branch itself after
    #    the worktree is gone. On failure, stop — tear nothing down.
    strategy = "--squash" if args.squash else "--merge"
    merge = subprocess.run(
        ["gh", "pr", "merge", branch, strategy],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )
    if merge.returncode != 0:
        detail = (merge.stderr or merge.stdout).strip()
        print(
            f"error: gh pr merge failed for branch {branch!r} "
            f"(nothing torn down — resolve and retry):\n{detail}",
            file=sys.stderr,
        )
        return 1

    # 2. Teardown (shared with cleanup): worktree + local branch, tmux, archive.
    archived = cleanup_mod.teardown(
        state_dir,
        task_id,
        task,
        archive=not args.keep,
        project_root=project_root,
    )

    # 3. Delete the remote branch now the worktree no longer holds it.
    _delete_remote_branch(project_root, branch)

    append_event(
        state_dir / "events.jsonl",
        "merge",
        task_id=task_id,
        branch=branch,
        squash=bool(args.squash),
        archived=archived,
    )
    dashboard_mod.rebuild(state_dir)

    print(
        f"task-{task_id} merged ({branch})"
        + (" and archived" if archived else "")
    )
    return 0


def _delete_remote_branch(project_root: Path, branch: str) -> None:
    """``git push origin --delete <branch>``; "already gone" is success."""
    r = subprocess.run(
        ["git", "-C", str(project_root), "push", "origin", "--delete", branch],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        return
    msg = (r.stderr or r.stdout).strip()
    if _branch_already_gone(msg):
        print(
            f"note: remote branch {branch!r} already deleted",
            file=sys.stderr,
        )
        return
    print(f"warn: git push --delete {branch} failed: {msg}", file=sys.stderr)


def _branch_already_gone(msg: str) -> bool:
    """Does this ``push --delete`` stderr mean the branch is already absent?

    Two shapes both mean "the desired end state (no remote branch) is already
    true", so the merge fully succeeded and we should not warn:

    - git's own "ref does not exist" phrasings (delete of a missing ref);
    - GitHub auto-delete-head-branches racing us: the head branch is gone before
      we push, so the delete fails to *resolve/lock* the ref. That surfaces as
      ``remote rejected ... cannot lock ref ... unable to resolve reference``.

    A bare ``remote rejected`` (without the can't-resolve/lock signal) is left as
    a genuine failure — it could mean a protected branch or some other reason the
    ref is still there.
    """
    lowered = msg.lower()
    return (
        "remote ref does not exist" in lowered
        or "does not exist" in lowered
        or "unable to resolve reference" in lowered
        or "cannot lock ref" in lowered
    )
