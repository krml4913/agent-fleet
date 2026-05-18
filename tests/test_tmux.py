"""Tests for ``fleet.tmux`` — real tmux subprocesses (skipped if missing).

The test uses an isolated tmux session name so it can run alongside an
existing user session without collisions.
"""
from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet import tmux  # noqa: E402


SESSION = "fleet-test-" + os.urandom(3).hex()


@unittest.skipIf(not tmux.available(), "tmux not available")
class TmuxTests(unittest.TestCase):
    def setUp(self) -> None:
        if tmux.session_exists(SESSION):
            tmux.kill_session(SESSION)
        tmux.new_session(SESSION)
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        if tmux.session_exists(SESSION):
            tmux.kill_session(SESSION)
        self._tmp.cleanup()

    def test_session_lifecycle(self) -> None:
        self.assertTrue(tmux.session_exists(SESSION))
        self.assertEqual(tmux.list_windows(SESSION), ["leader"])

    def test_new_window_with_env_and_cwd(self) -> None:
        tmux.new_window(
            SESSION,
            "task-1",
            cwd=str(self.tmp),
            env={"FLEET_TASK_ID": "1", "FLEET_STATE_DIR": str(self.tmp)},
        )
        self.assertIn("task-1", tmux.list_windows(SESSION))

    def test_load_and_paste_buffer(self) -> None:
        prompt_file = self.tmp / "prompt.txt"
        prompt_file.write_text("hello buffer\n")
        tmux.load_buffer("fleet-test-buf", str(prompt_file))
        # Open a window so we can paste somewhere.
        tmux.new_window(SESSION, "scratch", cwd=str(self.tmp))
        # paste_buffer must not raise.
        tmux.paste_buffer(SESSION, "scratch", "fleet-test-buf")
        tmux.delete_buffer("fleet-test-buf")  # cleanup
        # Re-deleting must be safe (best-effort).
        tmux.delete_buffer("fleet-test-buf")

    def test_kill_window(self) -> None:
        tmux.new_window(SESSION, "doomed")
        self.assertIn("doomed", tmux.list_windows(SESSION))
        tmux.kill_window(SESSION, "doomed")
        self.assertNotIn("doomed", tmux.list_windows(SESSION))


if __name__ == "__main__":
    unittest.main()
