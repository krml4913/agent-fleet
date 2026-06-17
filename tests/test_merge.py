"""Tests for ``fleet-agent merge``."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from fleet.commands import cleanup as cleanup_mod  # noqa: E402
from fleet.commands import merge as merge_mod  # noqa: E402


def _ok(*_a, **_k) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


class MergeCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo", repo=self.project)
        (self.state_dir / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _save(self, task_id: str, status: str, *, branch: str | None = None) -> None:
        data = {
            "id": task_id, "title": f"t{task_id}", "status": status,
            "agent": "claude:sonnet", "workspace": "none",
        }
        if branch is not None:
            data["branch"] = branch
        state.save_task(self.state_dir, task_id, data)

    def _args(self, task_id: str, **over) -> MagicMock:
        args = MagicMock()
        args.task_id = task_id
        args.project = str(self.project)
        args.squash = False
        args.keep = False
        args.force = False
        for k, v in over.items():
            setattr(args, k, v)
        return args

    def _events(self) -> list[dict]:
        path = self.state_dir / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().splitlines() if l]

    # -- happy path ---------------------------------------------------------

    def test_happy_path_merges_tears_down_and_archives(self) -> None:
        self._save("1", "completed", branch="demo/task/1")
        calls: list[list[str]] = []

        def record(cmd, **_k):
            calls.append(cmd)
            return _ok()

        with patch.dict(os.environ, {"FLEET_STATE_DIR": str(self.state_dir)}, clear=False), \
                patch("fleet.commands.merge.subprocess.run", side_effect=record), \
                patch("fleet.commands.cleanup.tmux_mod") as mock_tmux:
            mock_tmux.available.return_value = False
            rc = merge_mod.run(self._args("1"))

        self.assertEqual(rc, 0)
        # gh pr merge ran with a merge-commit strategy (no --delete-branch).
        gh = next(c for c in calls if c[:3] == ["gh", "pr", "merge"])
        self.assertIn("demo/task/1", gh)
        self.assertIn("--merge", gh)
        self.assertNotIn("--delete-branch", gh)
        # remote branch delete ran after teardown.
        self.assertTrue(any(
            c[:1] == ["git"] and "push" in c and "--delete" in c for c in calls
        ))
        # archived by default + merge event recorded.
        self.assertFalse((self.state_dir / "tasks" / "task-1").exists())
        self.assertTrue((self.state_dir / "tasks" / "_archive" / "task-1").is_dir())
        merge_events = [e for e in self._events() if e["type"] == "merge"]
        self.assertEqual(len(merge_events), 1)
        self.assertEqual(merge_events[0]["branch"], "demo/task/1")
        self.assertFalse(merge_events[0]["squash"])
        self.assertTrue(merge_events[0]["archived"])

    def test_squash_uses_squash_strategy(self) -> None:
        self._save("1", "completed", branch="demo/task/1")
        calls: list[list[str]] = []

        def record(cmd, **_k):
            calls.append(cmd)
            return _ok()

        with patch.dict(os.environ, {"FLEET_STATE_DIR": str(self.state_dir)}, clear=False), \
                patch("fleet.commands.merge.subprocess.run", side_effect=record), \
                patch("fleet.commands.cleanup.tmux_mod") as mock_tmux:
            mock_tmux.available.return_value = False
            rc = merge_mod.run(self._args("1", squash=True))

        self.assertEqual(rc, 0)
        gh = next(c for c in calls if c[:3] == ["gh", "pr", "merge"])
        self.assertIn("--squash", gh)

    def test_keep_does_not_archive(self) -> None:
        self._save("1", "completed", branch="demo/task/1")
        with patch.dict(os.environ, {"FLEET_STATE_DIR": str(self.state_dir)}, clear=False), \
                patch("fleet.commands.merge.subprocess.run", side_effect=_ok), \
                patch("fleet.commands.cleanup.tmux_mod") as mock_tmux:
            mock_tmux.available.return_value = False
            rc = merge_mod.run(self._args("1", keep=True))

        self.assertEqual(rc, 0)
        self.assertTrue((self.state_dir / "tasks" / "task-1").exists())

    def test_branch_falls_back_to_convention(self) -> None:
        self._save("1", "completed")  # no branch field
        calls: list[list[str]] = []

        def record(cmd, **_k):
            calls.append(cmd)
            return _ok()

        with patch.dict(os.environ, {"FLEET_STATE_DIR": str(self.state_dir)}, clear=False), \
                patch("fleet.commands.merge.subprocess.run", side_effect=record), \
                patch("fleet.commands.cleanup.tmux_mod") as mock_tmux:
            mock_tmux.available.return_value = False
            rc = merge_mod.run(self._args("1"))

        self.assertEqual(rc, 0)
        gh = next(c for c in calls if c[:3] == ["gh", "pr", "merge"])
        self.assertIn("demo/task/1", gh)

    # -- merge failure aborts before teardown -------------------------------

    def test_merge_failure_tears_nothing_down(self) -> None:
        self._save("1", "completed", branch="demo/task/1")

        def fake(cmd, **_k):
            if cmd[:3] == ["gh", "pr", "merge"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="",
                    stderr="not mergeable: the merge commit cannot be cleanly created",
                )
            return _ok()

        with patch.dict(os.environ, {"FLEET_STATE_DIR": str(self.state_dir)}, clear=False), \
                patch("fleet.commands.merge.subprocess.run", side_effect=fake), \
                patch("fleet.commands.cleanup.teardown") as mock_teardown:
            rc = merge_mod.run(self._args("1"))

        self.assertEqual(rc, 1)
        mock_teardown.assert_not_called()
        # task dir untouched, no merge event.
        self.assertTrue((self.state_dir / "tasks" / "task-1").exists())
        self.assertEqual([e for e in self._events() if e["type"] == "merge"], [])

    # -- guards -------------------------------------------------------------

    def test_refuses_non_terminal_without_force(self) -> None:
        self._save("1", "running", branch="demo/task/1")
        called: list = []

        with patch.dict(os.environ, {"FLEET_STATE_DIR": str(self.state_dir)}, clear=False), \
                patch("fleet.commands.merge.subprocess.run",
                      side_effect=lambda *a, **k: called.append(a) or _ok()):
            rc = merge_mod.run(self._args("1"))

        self.assertEqual(rc, 1)
        self.assertEqual(called, [])  # never reached gh

    def test_force_overrides_guard(self) -> None:
        self._save("1", "running", branch="demo/task/1")
        with patch.dict(os.environ, {"FLEET_STATE_DIR": str(self.state_dir)}, clear=False), \
                patch("fleet.commands.merge.subprocess.run", side_effect=_ok), \
                patch("fleet.commands.cleanup.tmux_mod") as mock_tmux:
            mock_tmux.available.return_value = False
            rc = merge_mod.run(self._args("1", force=True))
        self.assertEqual(rc, 0)

    def test_unknown_task(self) -> None:
        with patch.dict(os.environ, {"FLEET_STATE_DIR": str(self.state_dir)}, clear=False):
            rc = merge_mod.run(self._args("999"))
        self.assertEqual(rc, 1)


class TeardownHelperTests(unittest.TestCase):
    """The shared teardown helper used by both cleanup and merge."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo", repo=self.project)
        (self.state_dir / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n"
        )
        state.save_task(self.state_dir, "1", {
            "id": "1", "title": "t1", "status": "completed",
            "agent": "claude:sonnet", "workspace": "none",
            "owner_session": "demo",
        })

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_teardown_archives_and_kills_window(self) -> None:
        task = state.load_task(self.state_dir, "1")
        with patch("fleet.commands.cleanup.tmux_mod") as mock_tmux:
            mock_tmux.available.return_value = True
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception
            archived = cleanup_mod.teardown(
                self.state_dir, "1", task, archive=True,
            )

        self.assertTrue(archived)
        self.assertFalse((self.state_dir / "tasks" / "task-1").exists())
        self.assertTrue((self.state_dir / "tasks" / "_archive" / "task-1").is_dir())
        mock_tmux.kill_task_windows.assert_called_once_with("fleet-demo", "1")
        mock_tmux.delete_buffer.assert_called_once_with("fleet-task-1")

    def test_teardown_uses_project_repo_as_project_root(self) -> None:
        # repo (self.project) differs from state_dir.parent; teardown must hand
        # on_cleanup the real repo so a non-fleet project's worktree (which lives
        # in its own repo, not under fleet-state) can actually be removed.
        self.assertNotEqual(self.project, self.state_dir.parent)
        task = state.load_task(self.state_dir, "1")
        with patch("fleet.commands.cleanup.workspace_mod.on_cleanup") as on_cleanup, \
                patch("fleet.commands.cleanup.tmux_mod") as mock_tmux:
            mock_tmux.available.return_value = False
            cleanup_mod.teardown(self.state_dir, "1", task, archive=False)

        ctx = on_cleanup.call_args.args[0]
        self.assertEqual(Path(ctx["project_root"]).resolve(), self.project.resolve())

    def test_teardown_explicit_project_root_overrides_repo(self) -> None:
        other = Path(self._tmp.name) / "elsewhere"
        task = state.load_task(self.state_dir, "1")
        with patch("fleet.commands.cleanup.workspace_mod.on_cleanup") as on_cleanup, \
                patch("fleet.commands.cleanup.tmux_mod") as mock_tmux:
            mock_tmux.available.return_value = False
            cleanup_mod.teardown(
                self.state_dir, "1", task, archive=False, project_root=other,
            )

        ctx = on_cleanup.call_args.args[0]
        self.assertEqual(ctx["project_root"], other)

    def test_teardown_without_archive_keeps_dir(self) -> None:
        task = state.load_task(self.state_dir, "1")
        with patch("fleet.commands.cleanup.tmux_mod") as mock_tmux:
            mock_tmux.available.return_value = False
            archived = cleanup_mod.teardown(
                self.state_dir, "1", task, archive=False,
            )

        self.assertFalse(archived)
        self.assertTrue((self.state_dir / "tasks" / "task-1").exists())


class DeleteRemoteBranchTests(unittest.TestCase):
    """``_delete_remote_branch`` classifies push --delete failures."""

    def _run_with_stderr(self, returncode: int, stderr: str) -> str:
        result = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout="", stderr=stderr,
        )
        buf = io.StringIO()
        with patch("fleet.commands.merge.subprocess.run", return_value=result), \
                redirect_stderr(buf):
            merge_mod._delete_remote_branch(Path("/repo"), "demo/task/1")
        return buf.getvalue()

    def test_success_prints_nothing(self) -> None:
        self.assertEqual(self._run_with_stderr(0, ""), "")

    def test_already_deleted_phrasings_are_notes_not_warns(self) -> None:
        # Each of these means "branch already absent = desired end state".
        already_gone = [
            # git's own phrasings (delete of a missing ref).
            "error: unable to delete 'demo/task/1': remote ref does not exist",
            "error: branch 'demo/task/1' does not exist",
            # GitHub auto-delete-head-branches raced the delete.
            (
                " ! [remote rejected] demo/task/1 (cannot lock ref "
                "'refs/heads/demo/task/1': unable to resolve reference "
                "'refs/heads/demo/task/1')\n"
                "error: failed to push some refs to 'github.com:org/repo'"
            ),
        ]
        for stderr in already_gone:
            with self.subTest(stderr=stderr):
                out = self._run_with_stderr(1, stderr)
                self.assertIn("already deleted", out)
                self.assertNotIn("warn:", out)

    def test_cannot_lock_ref_alone_is_a_note(self) -> None:
        out = self._run_with_stderr(
            1, "error: cannot lock ref 'refs/heads/demo/task/1'"
        )
        self.assertIn("already deleted", out)
        self.assertNotIn("warn:", out)

    def test_unable_to_resolve_reference_alone_is_a_note(self) -> None:
        out = self._run_with_stderr(
            1, "fatal: unable to resolve reference 'refs/heads/demo/task/1'"
        )
        self.assertIn("already deleted", out)
        self.assertNotIn("warn:", out)

    def test_genuine_failure_still_warns(self) -> None:
        out = self._run_with_stderr(
            1,
            "remote: Permission to org/repo.git denied.\n"
            "fatal: Authentication failed",
        )
        self.assertIn("warn:", out)
        self.assertNotIn("already deleted", out)

    def test_bare_remote_rejected_still_warns(self) -> None:
        # A protected branch (or other reason the ref is still there) must not be
        # mistaken for "already gone".
        out = self._run_with_stderr(
            1,
            " ! [remote rejected] demo/task/1 (protected branch hook declined)\n"
            "error: failed to push some refs",
        )
        self.assertIn("warn:", out)
        self.assertNotIn("already deleted", out)


if __name__ == "__main__":
    unittest.main()
