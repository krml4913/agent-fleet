"""Tests for ``fleet send-prompt`` — error paths only (success requires a live agent CLI)."""
from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state, tmux  # noqa: E402
from tests._fleet_test_helpers import run_fleet_agent, make_project, requires_live_tmux  # noqa: E402


class SendPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.project_name = "fleet-test-" + os.urandom(3).hex()
        self.state_dir = make_project(self.fleet_home, self.project_name, self.project)
        self.session = f"fleet-{self.project_name}"

    def tearDown(self) -> None:
        if shutil.which("tmux") and tmux.session_exists(self.session):
            tmux.kill_session(self.session)
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_no_state_dir(self) -> None:
        r = run_fleet_agent("send-prompt", "1", "--project", "nonexistent",
                            fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no registered project", r.stderr)

    @requires_live_tmux
    def test_no_prompt_file(self) -> None:
        r = run_fleet_agent("send-prompt", "999", "--project", self.project_name,
                            fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no driver-prompt.md", r.stderr)

    @requires_live_tmux
    def test_no_session(self) -> None:
        td = state.task_dir(self.state_dir, "1")
        td.mkdir(parents=True)
        (td / "driver-prompt.md").write_text("hello\n")
        # owner_session resolves the target tmux session (Issue #166); the label
        # here matches the project name, so the (absent) session is fleet-<name>.
        state.save_task(
            self.state_dir, "1",
            {"id": "1", "status": "spawning", "owner_session": self.project_name},
        )
        r = run_fleet_agent("send-prompt", "1", "--project", self.project_name,
                            fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("session not running", r.stderr)

    @patch("fleet.commands.send_prompt.tmux_mod")
    @patch("fleet.commands.send_prompt.state_mod.resolve_state_dir")
    def test_uses_task_window_lookup(self, mock_resolve, mock_tmux: MagicMock) -> None:
        from fleet.commands.send_prompt import run

        td = state.task_dir(self.state_dir, "1")
        td.mkdir(parents=True)
        (td / "driver-prompt.md").write_text("hello\n")
        state.save_task(
            self.state_dir,
            "1",
            {
                "id": "1",
                "status": "spawning",
                "owner_session": self.project_name,
                "current_stage": 0,
                "stages": [{"role": "implementer", "agent": "claude:sonnet", "status": "running"}],
            },
        )
        mock_resolve.return_value = self.state_dir
        mock_tmux.available.return_value = True
        mock_tmux.session_exists.return_value = True
        mock_tmux.task_window_names.return_value = ["1·implementer"]
        mock_tmux.TmuxError = Exception
        args = MagicMock()
        args.task_id = "1"
        args.project = self.project_name
        args.prompt_timeout = 600.0

        with patch(
            "fleet.commands.send_prompt.prompt_deliverer.start_detached",
            return_value=td / "prompt-deliverer.log",
        ) as mock_deliverer:
            result = run(args)

        self.assertEqual(result, 0)
        mock_tmux.task_window_names.assert_called_once_with(
            f"fleet-{self.project_name}",
            "1",
        )
        mock_deliverer.assert_called_once()
        self.assertEqual(mock_deliverer.call_args.kwargs["window"], "1·implementer")
        self.assertEqual(mock_deliverer.call_args.kwargs["agent_spec"], "claude:sonnet")

    @patch("fleet.commands.send_prompt.tmux_mod")
    @patch("fleet.commands.send_prompt.state_mod.resolve_state_dir")
    def test_missing_task_yaml_errors_after_window_lookup(
        self,
        mock_resolve,
        mock_tmux: MagicMock,
    ) -> None:
        from fleet.commands.send_prompt import run

        td = state.task_dir(self.state_dir, "missing-yaml")
        td.mkdir(parents=True)
        (td / "driver-prompt.md").write_text("hello\n")
        mock_resolve.return_value = self.state_dir
        mock_tmux.available.return_value = True
        mock_tmux.session_exists.return_value = True
        mock_tmux.task_window_names.return_value = ["missing-yaml·driver"]
        mock_tmux.TmuxError = Exception
        args = MagicMock()
        args.task_id = "missing-yaml"
        args.project = self.project_name
        args.prompt_timeout = 600.0

        result = run(args)

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
