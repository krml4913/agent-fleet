"""workspace 機構 — fleet が持つ唯一の開発フロー機構。

``project.yaml`` の ``workspace:`` フィールド (``worktree`` / ``none``) を
読み、ライフサイクル境界の git (worktree 作成 / 削除) を実行する。
作業の git (commit / push / PR) には触らない — それは PJ の責務。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

VALUES = ("worktree", "none")
DEFAULT = "worktree"


def load(state_dir: Path) -> str:
    """Return the active workspace value for state_dir."""
    from . import state as state_mod  # break cycle

    project = state_mod.load_project(state_dir)
    value = project.get("workspace") or DEFAULT
    if value not in VALUES:
        raise ValueError(
            f"unknown workspace value: {value!r}; expected one of {VALUES}"
        )
    return value


def on_pre_start(ctx: dict[str, Any]) -> None:
    """Run before driver pane spawn. Create worktree if workspace=worktree."""
    try:
        value = load(ctx["state_dir"])
    except FileNotFoundError:
        value = DEFAULT
    if value != "worktree":
        return
    _worktree_add(ctx)


def on_cleanup(ctx: dict[str, Any]) -> None:
    """Run on fleet-agent cleanup. Remove worktree if workspace=worktree."""
    try:
        value = load(ctx["state_dir"])
    except FileNotFoundError:
        value = DEFAULT
    if value != "worktree":
        return
    _worktree_remove(ctx)


# ---------------------------------------------------------------------------
# worktree implementation (旧 git_worktree.py からそのまま移植)
# ---------------------------------------------------------------------------


def _git(
    target: Path,
    *args: str,
    timeout: float = 5,
) -> "subprocess.CompletedProcess[str] | None":
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

    Intentionally does not fetch — fleet-agent start should remain fast and
    work offline; the comparison uses refs git already knows locally.
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
        "warn: workspace worktree base branch "
        f"{branch!r} is {behind} commit{plural} behind {upstream!r}; "
        "run `git pull --ff-only` before `fleet-agent start` to spawn "
        "from the latest code. Continuing anyway.",
        file=sys.stderr,
    )


def _worktree_add(ctx: dict[str, Any]) -> None:
    state_dir: Path = ctx["state_dir"]
    task_id: str = ctx["task_id"]
    project_name: str = state_dir.name
    target = Path(ctx.get("project_root") or state_dir.parent)
    worktree = state_dir / "worktrees" / f"task-{task_id}"
    branch = f"{project_name}/task/{task_id}"

    worktree.parent.mkdir(parents=True, exist_ok=True)

    if worktree.exists():
        raise RuntimeError(
            f"workspace: worktree already exists: {worktree}"
        )

    _warn_if_base_branch_behind_upstream(target)

    r = subprocess.run(
        ["git", "-C", str(target), "worktree", "add", str(worktree), "-b", branch],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"workspace: `git worktree add` failed:\n{r.stderr.strip()}"
        )

    extra = ctx.setdefault("task_extra", {})
    extra["worktree"] = str(worktree)
    extra["branch"] = branch
    ctx["cwd"] = worktree

    print(
        f"workspace: created {worktree} on branch {branch}",
        file=sys.stderr,
    )


def _worktree_remove(ctx: dict[str, Any]) -> None:
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

    r = subprocess.run(
        ["git", "-C", str(project_root), "branch", "-D", branch],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        msg = r.stderr.strip()
        if "not found" not in msg and "no such branch" not in msg:
            print(f"warn: git branch -D failed: {msg}", file=sys.stderr)
