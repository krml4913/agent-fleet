"""Tests for ``fleet-agent start`` (dry-run path — tmux not exercised)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet-agent"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402


def run_fleet(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FLEET), *args],
        capture_output=True,
        text=True,
    )


class StartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        state.init_state(self.project / ".fleet-state", name="demo")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dry_run_creates_task_artifacts(self) -> None:
        result = run_fleet(
            "start",
            "--project",
            str(self.project),
            "--dry-run",
            "7",
            "Add a new endpoint",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        sd = self.project / ".fleet-state"
        tdir = sd / "tasks" / "task-7"
        self.assertTrue(tdir.is_dir())
        self.assertTrue((tdir / "task.yaml").is_file())
        self.assertTrue((tdir / "inbox.md").is_file())
        self.assertTrue((tdir / "outbox.md").is_file())
        self.assertTrue((tdir / "driver-prompt.md").is_file())
        # task.yaml has the resolved agent + topology (new schema with stages)
        text = (tdir / "task.yaml").read_text()
        task_data = state.load_task(sd, "7")
        self.assertEqual(str(task_data["id"]), "7")
        self.assertIn("agent: claude:sonnet", text)
        self.assertIn("status: spawning", text)
        self.assertIn("topology: solo", text)
        # New schema fields
        self.assertIn("stages:", text)
        self.assertIn("current_stage:", text)
        self.assertIsInstance(task_data.get("stages"), list)
        self.assertEqual(len(task_data["stages"]), 1)
        self.assertEqual(task_data["stages"][0]["status"], "running")
        # event recorded with type "start"
        events_path = sd / "events.jsonl"
        lines = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        starts = [e for e in lines if e.get("type") == "start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["task_id"], "7")
        self.assertTrue(starts[0].get("dry_run"))

    def test_rejects_duplicate_task_id(self) -> None:
        run_fleet(
            "start", "--project", str(self.project), "--dry-run",
            "1", "first",
        )
        result = run_fleet(
            "start", "--project", str(self.project), "--dry-run",
            "1", "second",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists", result.stderr)

    def test_agent_override(self) -> None:
        result = run_fleet(
            "start",
            "--project", str(self.project),
            "--dry-run",
            "--agent", "codex:o4-mini",
            "2", "Do the codex thing",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (
            self.project / ".fleet-state" / "tasks" / "task-2" / "task.yaml"
        ).read_text()
        self.assertIn("agent: codex:o4-mini", text)

    def test_codex_untrusted_repo_stops_before_task_creation(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project=str(self.project),
            task_id="codex-untrusted",
            description="Do the codex thing",
            topology="solo",
            agent="codex:o4-mini",
            title=None,
            dry_run=True,
            auto_paste=True,
            prompt_delay=0.0,
        )

        with (
            unittest.mock.patch("fleet.commands.start._git_toplevel", return_value=self.project),
            unittest.mock.patch(
                "fleet.commands.start.agents_mod.codex_repo_trusted",
                return_value=False,
            ),
            unittest.mock.patch("fleet.commands.start.plugins_mod.run_hook") as run_hook,
        ):
            result = start.run(args)

        self.assertEqual(result, 1)
        self.assertFalse(
            (self.project / ".fleet-state" / "tasks" / "task-codex-untrusted").exists()
        )
        run_hook.assert_not_called()

    def test_topology_pair_review_starts_first_stage(self) -> None:
        # pair_review's first stage is a codex implementer; run in-process and
        # stub the codex trust check so the test doesn't depend on ~/.codex.
        from fleet.commands import start

        args = argparse.Namespace(
            project=str(self.project),
            task_id="3",
            description="Pair flow",
            topology="pair_review",
            agent=None,
            title=None,
            dry_run=True,
            auto_paste=True,
            prompt_delay=0.0,
        )
        with unittest.mock.patch(
            "fleet.commands.start.agents_mod.codex_repo_trusted",
            return_value=True,
        ):
            result = start.run(args)
        self.assertEqual(result, 0)
        sd = self.project / ".fleet-state"
        text = (sd / "tasks" / "task-3" / "task.yaml").read_text()
        # First stage of pair_review is the implementer (codex by preset default).
        self.assertIn("role: implementer", text)
        self.assertIn("agent: codex:gpt-5.5", text)
        # pair_review now has 1 stage (implementer with inline peer_review)
        task_data = state.load_task(sd, "3")
        self.assertEqual(len(task_data["stages"]), 1)
        self.assertEqual(task_data["stages"][0]["role"], "implementer")
        self.assertIn("peer_review", task_data["stages"][0])
        self.assertEqual(task_data["stages"][0]["peer_review"]["role"], "code-reviewer")
        self.assertEqual(task_data["current_stage"], 0)
        self.assertEqual(task_data["stages"][0]["status"], "running")

    def test_role_flag_is_not_accepted(self) -> None:
        result = run_fleet(
            "start",
            "--project", str(self.project),
            "--dry-run",
            "--topology", "pair_review",
            "--role", "reviewer",
            "4", "Should fail",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized", result.stderr.lower())

    def test_no_state_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_fleet(
                "start",
                "--project", tmp,
                "--dry-run",
                "9", "no state",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("no .fleet-state", result.stderr)

    def test_no_auto_paste_flag_is_accepted(self) -> None:
        result = run_fleet(
            "start",
            "--project", str(self.project),
            "--dry-run",
            "--no-auto-paste",
            "10", "Manual paste task",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        tdir = self.project / ".fleet-state" / "tasks" / "task-10"
        self.assertTrue(tdir.is_dir())

    def test_spawn_command_is_gone(self) -> None:
        result = run_fleet(
            "spawn",
            "--project", str(self.project),
            "--dry-run",
            "99", "old command",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_title_with_colon_yaml_safe(self) -> None:
        result = run_fleet(
            "start",
            "--project", str(self.project),
            "--dry-run",
            "--title", "fix: handle edge case in parser",
            "11", "Fix the parser",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        sd = self.project / ".fleet-state"
        task_data = state.load_task(sd, "11")
        self.assertEqual(task_data["title"], "fix: handle edge case in parser")

    def test_title_with_hash_yaml_safe(self) -> None:
        result = run_fleet(
            "start",
            "--project", str(self.project),
            "--dry-run",
            "--title", "issue #42: fix the bug",
            "12", "Fix it",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        sd = self.project / ".fleet-state"
        task_data = state.load_task(sd, "12")
        self.assertEqual(task_data["title"], "issue #42: fix the bug")

    def test_description_with_colon_in_title_yaml_safe(self) -> None:
        result = run_fleet(
            "start",
            "--project", str(self.project),
            "--dry-run",
            "13", "refactor: split into modules",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        sd = self.project / ".fleet-state"
        task_data = state.load_task(sd, "13")
        self.assertEqual(task_data["title"], "refactor: split into modules")


class StartAutopasteEnterTests(unittest.TestCase):
    """Verify that auto-paste sends Enter after pasting the driver-prompt."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        state.init_state(self.project / ".fleet-state", name="demo")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_auto_paste_sends_enter_after_paste(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project=str(self.project),
            task_id="200",
            description="auto-paste enter integration test",
            topology="solo",
            agent=None,
            title=None,
            dry_run=False,
            auto_paste=True,
            prompt_delay=0.0,
        )

        with unittest.mock.patch("fleet.commands.start.tmux_mod") as mock_tmux:
            mock_tmux.available.return_value = True
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception
            result = start.run(args)

        self.assertEqual(result, 0)
        mock_tmux.paste_buffer.assert_called_once()
        # At least one send_keys call must carry enter=True (the post-paste Enter)
        enter_calls = [
            c for c in mock_tmux.send_keys.call_args_list
            if c.kwargs.get("enter", True)
        ]
        self.assertTrue(enter_calls, "send_keys with enter=True not called after paste")

    def test_no_auto_paste_skips_paste_and_enter(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project=str(self.project),
            task_id="201",
            description="no auto-paste test",
            topology="solo",
            agent=None,
            title=None,
            dry_run=False,
            auto_paste=False,
            prompt_delay=0.0,
        )

        with unittest.mock.patch("fleet.commands.start.tmux_mod") as mock_tmux:
            mock_tmux.available.return_value = True
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception
            result = start.run(args)

        self.assertEqual(result, 0)
        mock_tmux.paste_buffer.assert_not_called()


class LaunchStageDriverWindowCollisionTests(unittest.TestCase):
    """Verify that launch_stage_driver kills a pre-existing same-named window before creating a new one."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = self.project / ".fleet-state"
        state.init_state(self.state_dir, name="demo")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_task_dir(self, task_id: str) -> Path:
        task_dir = self.state_dir / "tasks" / f"task-{task_id}"
        task_dir.mkdir(parents=True)
        (task_dir / "driver-prompt.md").write_text("prompt content")
        return task_dir

    def test_kill_window_if_exists_called_before_new_window(self) -> None:
        """kill_window_if_exists must be called before new_window for the same window name."""
        from fleet.commands import start

        task_id = "multiproject"
        task_dir = self._make_task_dir(task_id)
        call_order: list[str] = []

        with unittest.mock.patch("fleet.commands.start.tmux_mod") as mock_tmux:
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception
            mock_tmux.kill_window_if_exists.side_effect = lambda *a, **kw: call_order.append("kill")
            mock_tmux.new_window.side_effect = lambda *a, **kw: call_order.append("new")

            result = start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=0,
                stage={"agent": "claude:sonnet", "role": "driver"},
                project_name="demo",
                auto_paste=False,
                prompt_delay=0.0,
            )

        self.assertEqual(result, 0)
        self.assertIn("kill", call_order, "kill_window_if_exists was not called")
        self.assertIn("new", call_order, "new_window was not called")
        # kill must precede new_window
        self.assertLess(
            call_order.index("kill"),
            call_order.index("new"),
            "kill_window_if_exists must be called before new_window",
        )

    def test_kill_targets_only_this_task_window(self) -> None:
        """kill_window_if_exists must be called with exactly the window for this task."""
        from fleet.commands import start

        task_id = "mytask"
        task_dir = self._make_task_dir(task_id)

        with unittest.mock.patch("fleet.commands.start.tmux_mod") as mock_tmux:
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception

            start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=0,
                stage={"agent": "claude:sonnet", "role": "driver"},
                project_name="demo",
                auto_paste=False,
                prompt_delay=0.0,
            )

        kill_calls = mock_tmux.kill_window_if_exists.call_args_list
        self.assertEqual(len(kill_calls), 1, "kill_window_if_exists should be called exactly once")
        _session, window_arg = kill_calls[0][0]
        self.assertEqual(window_arg, f"task-{task_id}")

    def test_launch_succeeds_even_when_window_already_exists(self) -> None:
        """Simulates stage transition: new_window does not fail even if a stale window exists."""
        from fleet.commands import start

        task_id = "stage-transition"
        task_dir = self._make_task_dir(task_id)

        with unittest.mock.patch("fleet.commands.start.tmux_mod") as mock_tmux:
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception
            # kill_window_if_exists silently succeeds (window existed — now gone)
            mock_tmux.kill_window_if_exists.return_value = None
            # new_window succeeds cleanly
            mock_tmux.new_window.return_value = None

            result = start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=1,
                stage={"agent": "claude:sonnet", "role": "implementer"},
                project_name="demo",
                auto_paste=False,
                prompt_delay=0.0,
            )

        self.assertEqual(result, 0)
        mock_tmux.kill_window_if_exists.assert_called_once()
        mock_tmux.new_window.assert_called_once()


if __name__ == "__main__":
    unittest.main()
