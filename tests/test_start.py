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

from fleet import state  # noqa: E402
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
        self.assertIn("topology: solo", text)
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
            (self.state_dir / "tasks" / "task-codex-untrusted").exists()
        )
        run_hook.assert_not_called()

    def test_topology_pair_review_starts_first_stage(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project="demo",
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
            "--topology", "pair_review", "--role", "reviewer",
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

    def test_auto_paste_sends_enter_after_paste(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project="demo",
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
        enter_calls = [
            c for c in mock_tmux.send_keys.call_args_list
            if c.kwargs.get("enter", True)
        ]
        self.assertTrue(enter_calls, "send_keys with enter=True not called after paste")

    def test_no_auto_paste_skips_paste_and_enter(self) -> None:
        from fleet.commands import start

        args = argparse.Namespace(
            project="demo",
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


if __name__ == "__main__":
    unittest.main()
