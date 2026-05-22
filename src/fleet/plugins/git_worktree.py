"""``git_worktree`` workflow.

On start: create a git worktree at ``<state_dir>/worktrees/task-<id>``
on branch ``<project-name>/task/<id>`` from the project root. The start
window's cwd is overridden to that worktree.

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
    "Per-task git worktree on branch <project-name>/task/<id>, rooted at the project dir."
)
DRIVER_PROMPT_FRAGMENT = """\
Git workflow (作業の git は driver が担う):
  - 作業完了後は必ず以下の手順を実行してから `fleet-agent done` を呼ぶ:
      1. git add / git commit   — 変更を commit する
      2. git push -u origin <branch>  — remote に push する
      3. gh pr create           — PR を作成する (タイトル・本文を適切に記述)
      4. fleet-agent done       — 最後に done を呼んで orchestrator に通知
  - PR のマージは行わない。マージは leader / user の判断に委ねる。
  - conflict が発生した場合は driver (AI) が自力で解決する:
      git fetch origin main → git rebase origin/main (または git merge) →
      conflict を手動編集して解消 → git rebase --continue → git push --force-with-lease
  - push reject された場合も driver が原因を調べて対処する (force-with-lease / rebase 等)。
  - 作業の git (commit / push / PR) は fleet core ではなく driver (AI) の責務。
    fleet core が git commit / push / PR を自動実行することはない。
  - 各 role 固有の追加規律は role 断片に記載する。
"""


def _git(
    target: Path,
    *args: str,
    timeout: float = 5,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(target), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _warn_if_base_branch_behind_upstream(target: Path) -> None:
    """Warn when the branch used for the new worktree is behind its upstream.

    This intentionally does not fetch. ``fleet-agent start`` should remain fast
    and should continue to work offline; the comparison uses refs git already
    knows locally.
    """
    branch_r = _git(target, "rev-parse", "--abbrev-ref", "HEAD")
    if branch_r is None or branch_r.returncode != 0:
        return
    branch = branch_r.stdout.strip()
    if not branch or branch == "HEAD":
        return

    upstream_r = _git(
        target,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
    )
    if upstream_r is None or upstream_r.returncode != 0:
        return
    upstream = upstream_r.stdout.strip()
    if not upstream:
        return

    counts_r = _git(
        target,
        "rev-list",
        "--left-right",
        "--count",
        f"HEAD...{upstream}",
    )
    if counts_r is None or counts_r.returncode != 0:
        return
    parts = counts_r.stdout.strip().split()
    if len(parts) != 2:
        return
    try:
        _ahead = int(parts[0])
        behind = int(parts[1])
    except ValueError:
        return
    if behind <= 0:
        return

    plural = "" if behind == 1 else "s"
    print(
        "warn: git_worktree base branch "
        f"{branch!r} is {behind} commit{plural} behind {upstream!r}; "
        "run `git pull --ff-only` before `fleet-agent start` to spawn "
        "from the latest code. Continuing anyway.",
        file=sys.stderr,
    )


def on_pre_start(ctx: dict[str, Any]) -> None:
    state_dir: Path = ctx["state_dir"]
    task_id: str = ctx["task_id"]
    project_name: str = state_dir.name
    target = Path(ctx.get("project_root") or state_dir.parent)
    worktree = state_dir / "worktrees" / f"task-{task_id}"
    branch = f"{project_name}/task/{task_id}"

    worktree.parent.mkdir(parents=True, exist_ok=True)

    if worktree.exists():
        raise RuntimeError(
            f"git_worktree: worktree already exists: {worktree}"
        )

    _warn_if_base_branch_behind_upstream(target)

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
    project_name: str = state_dir.name
    project_root = Path(ctx.get("project_root") or state_dir.parent)
    worktree = state_dir / "worktrees" / f"task-{task_id}"
    branch = f"{project_name}/task/{task_id}"

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
