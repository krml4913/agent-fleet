"""Tests for the workspace worktree implementation (requires a working ``git`` binary)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import tests._fleet_test_helpers  # noqa: E402,F401  (hermetic env: FLEET_NO_NOTIFY / FLEET_NO_MUX)
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import workspace  # noqa: E402


_GIT_ENV_EXTRA = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}
_template: TemporaryDirectory | None = None


def _template_repo() -> Path:
    """A one-commit ``main`` repo built once per module and copied per test.

    ``git init`` + ``add`` + ``commit`` is three process spawns (slow on
    Windows); ``shutil.copytree`` of the result gives each test an identical,
    fully independent repository.
    """
    global _template
    if _template is None:
        _template = TemporaryDirectory()
        repo = Path(_template.name) / "proj"
        repo.mkdir()
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        env = {**os.environ, **_GIT_ENV_EXTRA}
        for args in (
            ("init", "-q", "-b", "main"),
            ("add", "README.md"),
            ("commit", "-q", "-m", "initial"),
        ):
            subprocess.run(["git", "-C", str(repo), *args], check=True,
                           capture_output=True, env=env)
    return Path(_template.name) / "proj"


def tearDownModule() -> None:
    global _template
    if _template is not None:
        _template.cleanup()
        _template = None


@unittest.skipIf(shutil.which("git") is None, "git not available")
class WorkspaceWorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        shutil.copytree(_template_repo(), self.project)

        self.git_env = os.environ.copy()
        self.git_env.update(_GIT_ENV_EXTRA)

        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.state_dir = self.fleet_home / "projects" / "testproj"
        self.state_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(
        self,
        *args: str,
        cwd: Path | None = None,
    ) -> "subprocess.CompletedProcess[bytes]":
        return subprocess.run(
            ["git", "-C", str(cwd or self.project), *args],
            check=True,
            capture_output=True,
            env=self.git_env,
        )

    def test_pre_start_creates_worktree(self) -> None:
        ctx: dict = {
            "state_dir": self.state_dir,
            "task_id": "7",
            "project_root": self.project,
        }
        workspace._worktree_add(ctx)
        worktree = self.state_dir / "worktrees" / "task-7"
        self.assertTrue(worktree.is_dir())
        self.assertEqual(ctx["task_extra"]["worktree"], str(worktree))
        self.assertEqual(ctx["task_extra"]["branch"], "testproj/task/7")
        self.assertEqual(ctx["cwd"], worktree)

    def test_pre_start_warns_when_base_branch_is_behind_upstream(self) -> None:
        # Reproduce "local main is 1 commit behind origin/main" purely with
        # local refs (all the check reads — it never fetches): commit, record
        # that commit as origin/main, then rewind local main by one. Same end
        # state as clone/push/fetch against a bare remote, minus 5 spawns.
        (self.project / "README.md").write_text("hello\nremote update\n", encoding="utf-8")
        self.git("commit", "-q", "-am", "remote update")
        self.git("remote", "add", "origin", str(Path(self._tmp.name) / "remote.git"))
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.git("branch", "-q", "--set-upstream-to=origin/main", "main")

        ctx: dict = {
            "state_dir": self.state_dir,
            "task_id": "behind",
            "project_root": self.project,
        }
        stderr = StringIO()
        with redirect_stderr(stderr):
            workspace._worktree_add(ctx)

        self.assertIn(
            "warn: workspace worktree base branch 'main' is 1 commit behind 'origin/main'",
            stderr.getvalue(),
        )
        self.assertIn("git pull --ff-only", stderr.getvalue())
        self.assertTrue((self.state_dir / "worktrees" / "task-behind").is_dir())

    def test_pre_start_does_not_warn_without_upstream(self) -> None:
        ctx: dict = {
            "state_dir": self.state_dir,
            "task_id": "no-upstream",
            "project_root": self.project,
        }
        stderr = StringIO()
        with redirect_stderr(stderr):
            workspace._worktree_add(ctx)

        self.assertNotIn("base branch", stderr.getvalue())
        self.assertTrue((self.state_dir / "worktrees" / "task-no-upstream").is_dir())

    def test_pre_start_rejects_duplicate(self) -> None:
        ctx: dict = {
            "state_dir": self.state_dir,
            "task_id": "8",
            "project_root": self.project,
        }
        workspace._worktree_add(ctx)
        with self.assertRaises(RuntimeError):
            workspace._worktree_add(dict(ctx))

    def test_cleanup_removes_worktree_and_branch(self) -> None:
        ctx: dict = {
            "state_dir": self.state_dir,
            "task_id": "9",
            "project_root": self.project,
        }
        workspace._worktree_add(ctx)
        worktree = self.state_dir / "worktrees" / "task-9"
        self.assertTrue(worktree.is_dir())

        r = subprocess.run(
            ["git", "-C", str(self.project), "show-ref", "--verify", "refs/heads/testproj/task/9"],
            capture_output=True,
        )
        self.assertEqual(r.returncode, 0)

        cleanup_ctx: dict = {
            "state_dir": self.state_dir,
            "task_id": "9",
            "project_root": self.project,
        }
        workspace._worktree_remove(cleanup_ctx)

        self.assertFalse(worktree.exists())
        r = subprocess.run(
            ["git", "-C", str(self.project), "show-ref", "--verify", "refs/heads/testproj/task/9"],
            capture_output=True,
        )
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
