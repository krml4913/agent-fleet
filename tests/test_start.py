"""Tests for ``fleet-agent start`` (dry-run path — tmux not exercised)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import prompt_pointer, state  # noqa: E402
from tests._fleet_test_helpers import run_fleet_agent, make_project  # noqa: E402


class StartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_dry_run_creates_task_artifacts(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "7", "Add a new endpoint",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        tdir = self.state_dir / "tasks" / "task-7"
        self.assertTrue(tdir.is_dir())
        self.assertTrue((tdir / "task.yaml").is_file())
        self.assertTrue((tdir / "inbox.md").is_file())
        self.assertTrue((tdir / "outbox.md").is_file())
        self.assertTrue((tdir / "driver-prompt.md").is_file())
        text = (tdir / "task.yaml").read_text()
        task_data = state.load_task(self.state_dir, "7")
        self.assertEqual(str(task_data["id"]), "7")
        self.assertIn("agent: claude:sonnet", text)
        self.assertIn("status: spawning", text)
        self.assertIn("formation: solo", text)
        self.assertIn("stages:", text)
        self.assertIn("current_stage:", text)
        self.assertIsInstance(task_data.get("stages"), list)
        self.assertEqual(len(task_data["stages"]), 1)
        self.assertEqual(task_data["stages"][0]["status"], "running")
        events_path = self.state_dir / "events.jsonl"
        lines = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        starts = [e for e in lines if e.get("type") == "start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["task_id"], "7")
        self.assertTrue(starts[0].get("dry_run"))

    def test_prompt_file_reads_description(self) -> None:
        prompt_file = self.project / "task-prompt.md"
        prompt_text = "File prompt first line\nSecond line from file\n"
        prompt_file.write_text(prompt_text)

        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--prompt-file", str(prompt_file), "from-file",
            fleet_home=self.fleet_home,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        tdir = self.state_dir / "tasks" / "task-from-file"
        task_data = state.load_task(self.state_dir, "from-file")
        self.assertEqual(task_data["description"], prompt_text)
        self.assertEqual(task_data["title"], "File prompt first line")
        prompt = (tdir / "driver-prompt.md").read_text()
        self.assertIn(prompt_text, prompt)
        lines = [
            json.loads(l)
            for l in (self.state_dir / "events.jsonl").read_text().splitlines()
            if l
        ]
        starts = [e for e in lines if e.get("type") == "start"]
        self.assertEqual(starts[-1]["description"], prompt_text)

    def test_prompt_file_and_description_is_error(self) -> None:
        prompt_file = self.project / "task-prompt.md"
        prompt_file.write_text("File prompt")

        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--prompt-file", str(prompt_file), "both", "Inline prompt",
            fleet_home=self.fleet_home,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("either description or --prompt-file", result.stderr)
        self.assertFalse((self.state_dir / "tasks" / "task-both").exists())

    def test_missing_description_and_prompt_file_is_error(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run", "missing-prompt",
            fleet_home=self.fleet_home,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("either description or --prompt-file", result.stderr)
        self.assertFalse((self.state_dir / "tasks" / "task-missing-prompt").exists())

    def test_missing_prompt_file_is_error(self) -> None:
        missing = self.project / "does-not-exist.md"

        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--prompt-file", str(missing), "missing-file",
            fleet_home=self.fleet_home,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read --prompt-file", result.stderr)
        self.assertIn(str(missing), result.stderr)
        self.assertFalse((self.state_dir / "tasks" / "task-missing-file").exists())

    def test_prompt_omits_git_workflow(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "no-git", "No git workflow here",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        prompt = (
            self.state_dir / "tasks" / "task-no-git" / "driver-prompt.md"
        ).read_text()
        self.assertNotIn("Git workflow", prompt)
        self.assertNotIn("gh pr create", prompt)

    def test_rejects_duplicate_task_id(self) -> None:
        run_fleet_agent("start", "--project", "demo", "--dry-run",
                        "1", "first", fleet_home=self.fleet_home)
        result = run_fleet_agent("start", "--project", "demo", "--dry-run",
                                 "1", "second", fleet_home=self.fleet_home)
        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists", result.stderr)

    def test_agent_override(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--agent", "codex:o4-mini", "2", "Do the codex thing",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (self.state_dir / "tasks" / "task-2" / "task.yaml").read_text()
        self.assertIn("agent: codex:o4-mini", text)

    def test_codex_untrusted_repo_stops_before_task_creation(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project="demo",
            task_id="codex-untrusted",
            description="Do the codex thing",
            formation="solo",
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
            unittest.mock.patch("fleet.commands.start.workspace_mod.on_pre_start") as on_pre_start,
        ):
            result = start.run(args)

        self.assertEqual(result, 1)
        self.assertFalse(
            (self.state_dir / "tasks" / "task-codex-untrusted").exists()
        )
        on_pre_start.assert_not_called()

    def test_formation_pair_review_starts_first_stage(self) -> None:
        import shutil
        from fleet import formation as formation_mod
        from fleet.commands import start

        src = formation_mod.TEMPLATES_DIR / "pair_review.yaml"
        shutil.copyfile(src, self.state_dir / "formations" / "pair_review.yaml")

        args = argparse.Namespace(
            project="demo",
            task_id="3",
            description="Pair flow",
            formation="pair_review",
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
        text = (self.state_dir / "tasks" / "task-3" / "task.yaml").read_text()
        self.assertIn("role: implementer", text)
        self.assertIn("agent: codex:gpt-5.5", text)
        task_data = state.load_task(self.state_dir, "3")
        self.assertEqual(len(task_data["stages"]), 1)
        self.assertEqual(task_data["stages"][0]["role"], "implementer")
        self.assertIn("peer_review", task_data["stages"][0])
        self.assertEqual(task_data["stages"][0]["peer_review"]["role"], "code-reviewer")
        self.assertEqual(task_data["current_stage"], 0)
        self.assertEqual(task_data["stages"][0]["status"], "running")

    def test_role_flag_is_not_accepted(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--formation", "pair_review", "--role", "reviewer",
            "4", "Should fail",
            fleet_home=self.fleet_home,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized", result.stderr.lower())

    def test_no_project_found(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "nonexistent", "--dry-run",
            "9", "no state",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no registered project", result.stderr)

    def test_no_auto_paste_flag_is_accepted(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--no-auto-paste", "10", "Manual paste task",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        tdir = self.state_dir / "tasks" / "task-10"
        self.assertTrue(tdir.is_dir())

    def test_spawn_command_is_gone(self) -> None:
        result = run_fleet_agent(
            "spawn", "--project", "demo", "--dry-run",
            "99", "old command",
            fleet_home=self.fleet_home,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_title_with_colon_yaml_safe(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--title", "fix: handle edge case in parser",
            "11", "Fix the parser",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        task_data = state.load_task(self.state_dir, "11")
        self.assertEqual(task_data["title"], "fix: handle edge case in parser")

    def test_title_with_hash_yaml_safe(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "--title", "issue #42: fix the bug",
            "12", "Fix it",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        task_data = state.load_task(self.state_dir, "12")
        self.assertEqual(task_data["title"], "issue #42: fix the bug")

    def test_description_with_colon_in_title_yaml_safe(self) -> None:
        result = run_fleet_agent(
            "start", "--project", "demo", "--dry-run",
            "13", "refactor: split into modules",
            fleet_home=self.fleet_home,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        task_data = state.load_task(self.state_dir, "13")
        self.assertEqual(task_data["title"], "refactor: split into modules")


class StartAutopasteEnterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = make_project(self.fleet_home, "demo", self.project)

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_auto_paste_starts_detached_deliverer(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project="demo",
            task_id="200",
            description="auto-paste enter integration test",
            formation="solo",
            agent=None,
            title=None,
            dry_run=False,
            auto_paste=True,
            prompt_delay=0.0,
        )

        with (
            unittest.mock.patch("fleet.commands.start.tmux_mod") as mock_tmux,
            unittest.mock.patch("fleet.commands.start.workspace_mod.on_pre_start"),
            unittest.mock.patch(
                "fleet.commands.start.prompt_deliverer.start_detached",
                return_value=self.state_dir / "tasks" / "task-200" / "prompt-deliverer.log",
            ) as mock_deliverer,
        ):
            mock_tmux.available.return_value = True
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception
            result = start.run(args)

        self.assertEqual(result, 0)
        # start itself does not paste; the detached deliverer does, once the
        # pane is ready. start only pre-loads the pointer (not the prompt
        # body) into the tmux buffer for the no-auto-paste manual-paste path.
        mock_tmux.paste_buffer.assert_not_called()
        loaded_path = Path(mock_tmux.load_buffer.call_args.args[1])
        self.assertEqual(loaded_path.name, ".driver-prompt.md.paste-pointer")
        pointer = loaded_path.read_text(encoding="utf-8")
        prompt_path = self.state_dir / "tasks" / "task-200" / "driver-prompt.md"
        self.assertIn("Read the prompt file at this path", pointer)
        self.assertTrue(pointer.endswith(str(prompt_path.resolve())))
        self.assertEqual(pointer.count("\n"), 0)
        self.assertNotIn("auto-paste enter integration test", pointer)
        mock_deliverer.assert_called_once()
        self.assertEqual(mock_deliverer.call_args.kwargs["task_id"], "200")
        self.assertEqual(mock_deliverer.call_args.kwargs["agent_spec"], "claude:sonnet")

    def test_no_auto_paste_skips_paste_and_enter(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project="demo",
            task_id="201",
            description="no auto-paste test",
            formation="solo",
            agent=None,
            title=None,
            dry_run=False,
            auto_paste=False,
            prompt_delay=0.0,
        )

        with (
            unittest.mock.patch("fleet.commands.start.tmux_mod") as mock_tmux,
            unittest.mock.patch("fleet.commands.start.workspace_mod.on_pre_start"),
        ):
            mock_tmux.available.return_value = True
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception
            result = start.run(args)

        self.assertEqual(result, 0)
        mock_tmux.paste_buffer.assert_not_called()
        mock_tmux.load_buffer.assert_called_once()
        prompt_path = self.state_dir / "tasks" / "task-201" / "driver-prompt.md"
        loaded_path = Path(mock_tmux.load_buffer.call_args.args[1])
        self.assertEqual(loaded_path, prompt_pointer.pointer_path(prompt_path))
        self.assertIn(str(prompt_path.resolve()), loaded_path.read_text(encoding="utf-8"))


class LaunchStageDriverWindowCollisionTests(unittest.TestCase):
    """Verify launch_stage_driver cleans old task windows before creating a new one."""

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

    def test_kill_task_windows_called_before_new_window(self) -> None:
        """Existing windows for the task must be killed before new_window."""
        from fleet.commands import start

        task_id = "multiproject"
        task_dir = self._make_task_dir(task_id)
        call_order: list[str] = []

        with unittest.mock.patch("fleet.commands.start.tmux_mod") as mock_tmux:
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception
            mock_tmux.kill_task_windows.side_effect = lambda *a, **kw: call_order.append("kill")
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
        self.assertIn("kill", call_order, "kill_task_windows was not called")
        self.assertIn("new", call_order, "new_window was not called")
        # kill must precede new_window
        self.assertLess(
            call_order.index("kill"),
            call_order.index("new"),
            "kill_task_windows must be called before new_window",
        )
        loaded_path = Path(mock_tmux.load_buffer.call_args.args[1])
        self.assertEqual(loaded_path, prompt_pointer.pointer_path(task_dir / "driver-prompt.md"))
        pointer = loaded_path.read_text(encoding="utf-8")
        self.assertIn(str((task_dir / "driver-prompt.md").resolve()), pointer)
        self.assertNotIn("prompt content", pointer)

    def test_launch_uses_task_id_and_role_window_name(self) -> None:
        """new_window uses <task-id>·<role> and cleanup targets the task id."""
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
                stage={"agent": "claude:sonnet", "role": "implementer"},
                project_name="demo",
                auto_paste=False,
                prompt_delay=0.0,
            )

        mock_tmux.kill_task_windows.assert_called_once_with("fleet-demo", task_id)
        new_args = mock_tmux.new_window.call_args[0]
        self.assertEqual(new_args[1], f"{task_id}·implementer")

    def test_codex_launch_disables_update_prompt(self) -> None:
        from fleet.commands import start

        task_id = "codex-update"
        task_dir = self._make_task_dir(task_id)

        with unittest.mock.patch("fleet.commands.start.tmux_mod") as mock_tmux:
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception

            start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=0,
                stage={"agent": "codex:gpt-5.5", "role": "implementer"},
                project_name="demo",
                auto_paste=False,
                prompt_delay=0.0,
            )

        sent = mock_tmux.send_keys.call_args.args[2]
        self.assertIn("check_for_update_on_startup=false", sent)

    def test_launch_succeeds_even_when_window_already_exists(self) -> None:
        """Simulates stage transition: new_window does not fail even if a stale window exists."""
        from fleet.commands import start

        task_id = "stage-transition"
        task_dir = self._make_task_dir(task_id)

        with unittest.mock.patch("fleet.commands.start.tmux_mod") as mock_tmux:
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception
            # kill_task_windows removes stale windows from previous stages.
            mock_tmux.kill_task_windows.return_value = None
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
        mock_tmux.kill_task_windows.assert_called_once_with("fleet-demo", task_id)
        mock_tmux.new_window.assert_called_once()

    def test_launch_can_preserve_existing_task_windows(self) -> None:
        from fleet.commands import start

        task_id = "keep-live"
        task_dir = self._make_task_dir(task_id)

        with unittest.mock.patch("fleet.commands.start.tmux_mod") as mock_tmux:
            mock_tmux.session_exists.return_value = True
            mock_tmux.TmuxError = Exception

            result = start.launch_stage_driver(
                state_dir=self.state_dir,
                task_id=task_id,
                task_dir=task_dir,
                stage_idx=0,
                stage={"agent": "claude:sonnet", "role": "code-reviewer"},
                project_name="demo",
                auto_paste=False,
                prompt_delay=0.0,
                replace_task_windows=False,
            )

        self.assertEqual(result, 0)
        mock_tmux.kill_task_windows.assert_not_called()
        mock_tmux.new_window.assert_called_once()


if __name__ == "__main__":
    unittest.main()
