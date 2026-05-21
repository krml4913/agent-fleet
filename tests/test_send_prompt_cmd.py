"""Tests for ``fleet send-prompt`` — error paths only (success requires a live agent CLI)."""
from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state, tmux  # noqa: E402
from tests._fleet_test_helpers import run_fleet_agent, make_project  # noqa: E402


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

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_no_prompt_file(self) -> None:
        r = run_fleet_agent("send-prompt", "999", "--project", self.project_name,
                            fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no driver-prompt.md", r.stderr)

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_no_session(self) -> None:
        td = state.task_dir(self.state_dir, "1")
        td.mkdir(parents=True)
        (td / "driver-prompt.md").write_text("hello\n")
        r = run_fleet_agent("send-prompt", "1", "--project", self.project_name,
                            fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("session not running", r.stderr)


if __name__ == "__main__":
    unittest.main()
