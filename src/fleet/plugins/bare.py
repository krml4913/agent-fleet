"""``bare`` workflow — no-op hooks.

Use this for projects that aren't git-managed, or where you want fleet
to stay out of your VCS entirely. It is the default workflow when
``project.yaml`` does not specify one.
"""
from __future__ import annotations

from typing import Any

WORKFLOW_NAME = "bare"
DESCRIPTION = "No-op workflow. No worktree, no branch, no PR — fleet stays out of git."


def on_pre_start(ctx: dict[str, Any]) -> None:
    return None


def on_post_done(ctx: dict[str, Any]) -> None:
    return None
