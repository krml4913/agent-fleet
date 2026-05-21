"""Tests for the ``git_worktree`` plugin (requires a working ``git`` binary)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet.plugins import git_worktree  # noqa: E402


@unittest.skipIf(shutil.which("git") is None, "git not available")
class GitWorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()

        env_extra = {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        import os as _os
        env = _os.environ.copy()
        env.update(env_extra)

        def git(*args: str) -> None:
            subprocess.run(
                ["git", "-C", str(self.project), *args],
                check=True,
                capture_output=True,
                env=env,
            )

        git("init", "-q", "-b", "main")
        (self.project / "README.md").write_text("hello\n")
        git("add", "README.md")
        git("commit", "-q", "-m", "initial")

        # state_dir is now stored in fleet-state/projects/<name>/,
        # but the plugin only cares that state_dir.is_dir() and
        # state_dir / "worktrees" is where it creates the worktree.
        # We simulate this by using a tempdir as the state_dir directly.
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.state_dir = self.fleet_home / "projects" / "testproj"
        self.state_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pre_start_creates_worktree(self) -> None:
        ctx: dict = {
            "state_dir": self.state_dir,
            "task_id": "7",
            "project_root": self.project,
        }
        git_worktree.on_pre_start(ctx)
        worktree = self.state_dir / "worktrees" / "task-7"
        self.assertTrue(worktree.is_dir())
        self.assertEqual(ctx["task_extra"]["worktree"], str(worktree))
        self.assertEqual(ctx["task_extra"]["branch"], "task/7")
        self.assertEqual(ctx["cwd"], worktree)

    def test_pre_start_rejects_duplicate(self) -> None:
        ctx: dict = {
            "state_dir": self.state_dir,
            "task_id": "8",
            "project_root": self.project,
        }
        git_worktree.on_pre_start(ctx)
        with self.assertRaises(RuntimeError):
            git_worktree.on_pre_start(dict(ctx))

    def test_cleanup_removes_worktree_and_branch(self) -> None:
        ctx: dict = {
            "state_dir": self.state_dir,
            "task_id": "9",
            "project_root": self.project,
        }
        git_worktree.on_pre_start(ctx)
        worktree = self.state_dir / "worktrees" / "task-9"
        self.assertTrue(worktree.is_dir())

        r = subprocess.run(
            ["git", "-C", str(self.project), "show-ref", "--verify", "refs/heads/task/9"],
            capture_output=True,
        )
        self.assertEqual(r.returncode, 0)

        cleanup_ctx: dict = {
            "state_dir": self.state_dir,
            "task_id": "9",
            "project_root": self.project,
        }
        git_worktree.on_cleanup(cleanup_ctx)

        self.assertFalse(worktree.exists())
        r = subprocess.run(
            ["git", "-C", str(self.project), "show-ref", "--verify", "refs/heads/task/9"],
            capture_output=True,
        )
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
