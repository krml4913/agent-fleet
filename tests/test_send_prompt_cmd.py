"""Tests for ``fleet send-prompt`` — error paths only (success requires a live agent CLI)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state, tmux  # noqa: E402


def run_fleet(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("FLEET_TASK_ID", None)
    return subprocess.run(
        [sys.executable, str(FLEET), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
    )


class SendPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.project_name = "fleet-test-" + os.urandom(3).hex()
        state.init_state(self.project / ".fleet-state", name=self.project_name)
        self.session = f"fleet-{self.project_name}"

    def tearDown(self) -> None:
        if shutil.which("tmux") and tmux.session_exists(self.session):
            tmux.kill_session(self.session)
        self._tmp.cleanup()

    def test_no_state_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            r = run_fleet("send-prompt", "1", "--project", tmp)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no .fleet-state", r.stderr)

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_no_prompt_file(self) -> None:
        r = run_fleet("send-prompt", "999", "--project", str(self.project))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no driver-prompt.md", r.stderr)

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_no_session(self) -> None:
        # Stage a prompt file under a task dir so we get past the file check.
        td = state.task_dir(self.project / ".fleet-state", "1")
        td.mkdir(parents=True)
        (td / "driver-prompt.md").write_text("hello\n")
        r = run_fleet("send-prompt", "1", "--project", str(self.project))
        self.assertEqual(r.returncode, 1)
        self.assertIn("session not running", r.stderr)


if __name__ == "__main__":
    unittest.main()
