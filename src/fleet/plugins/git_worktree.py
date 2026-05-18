"""``git_worktree`` workflow.

On spawn: create a git worktree at ``<state_dir>/worktrees/task-<id>``
on branch ``task/<id>`` from the project root. The spawn window's cwd
is overridden to that worktree.

On done: no-op for MVP. Worktree teardown will land in a follow-up
phase (likely a separate ``fleet cleanup`` CLI rather than a hook —
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


def on_pre_spawn(ctx: dict[str, Any]) -> None:
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
