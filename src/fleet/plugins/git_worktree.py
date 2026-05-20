"""``git_worktree`` workflow.

On start: create a git worktree at ``<state_dir>/worktrees/task-<id>``
on branch ``task/<id>`` from the project root. The start window's cwd
is overridden to that worktree.

On done: no-op for MVP. Worktree teardown will land in a follow-up
phase (likely a separate ``fleet-agent cleanup`` CLI rather than a hook —
keeps the post_done path safe-by-default).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

WORKFLOW_NAME = "git_worktree"
DESCRIPTION = (
    "Per-task git worktree on branch task/<id>, rooted at the project dir."
)


def on_pre_start(ctx: dict[str, Any]) -> None:
    state_dir: Path = ctx["state_dir"]
    task_id: str = ctx["task_id"]
    target = Path(ctx.get("project_root") or state_dir.parent)
    worktree = state_dir / "worktrees" / f"task-{task_id}"
    branch = f"task/{task_id}"

    worktree.parent.mkdir(parents=True, exist_ok=True)

    if worktree.exists():
        raise RuntimeError(
            f"git_worktree: worktree already exists: {worktree}"
        )

    r = subprocess.run(
        ["git", "-C", str(target), "worktree", "add", str(worktree), "-b", branch],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"git_worktree: `git worktree add` failed:\n{r.stderr.strip()}"
        )

    extra = ctx.setdefault("task_extra", {})
    extra["worktree"] = str(worktree)
    extra["branch"] = branch
    # Drivers should work inside the worktree.
    ctx["cwd"] = worktree

    print(
        f"git_worktree: created {worktree} on branch {branch}",
        file=sys.stderr,
    )


def on_post_done(ctx: dict[str, Any]) -> None:
    return None


def on_cleanup(ctx: dict[str, Any]) -> None:
    """Remove the per-task worktree + branch. Errors warn but don't raise."""
    state_dir: Path = ctx["state_dir"]
    task_id: str = ctx["task_id"]
    project_root = Path(ctx.get("project_root") or state_dir.parent)
    worktree = state_dir / "worktrees" / f"task-{task_id}"
    branch = f"task/{task_id}"

    if worktree.exists():
        r = subprocess.run(
            [
                "git", "-C", str(project_root),
                "worktree", "remove", "--force", str(worktree),
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            print(
                f"warn: git worktree remove failed: {r.stderr.strip()}",
                file=sys.stderr,
            )

    # Delete the branch; tolerate "not found" / "unborn".
    r = subprocess.run(
        ["git", "-C", str(project_root), "branch", "-D", branch],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        # Branch may never have been committed onto; this is non-fatal.
        msg = r.stderr.strip()
        if "not found" not in msg and "no such branch" not in msg:
            print(f"warn: git branch -D failed: {msg}", file=sys.stderr)
