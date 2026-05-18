"""Tests for ``fleet leader``."""
from __future__ import annotations

import json
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


class LeaderCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        # Use a unique project name so tmux sessions don't collide.
        self.project_name = "fleet-test-" + os.urandom(3).hex()
        state.init_state(self.project / ".fleet-state", name=self.project_name)
        self.session = f"fleet-{self.project_name}"

    def tearDown(self) -> None:
        if shutil.which("tmux") and tmux.session_exists(self.session):
            tmux.kill_session(self.session)
        self._tmp.cleanup()

    def test_no_state_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            r = run_fleet("leader", "--project", tmp)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no .fleet-state", r.stderr)

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_launch_creates_session(self) -> None:
        r = run_fleet("leader", "--project", str(self.project))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(tmux.session_exists(self.session))
        # leader_start event recorded
        events_path = self.project / ".fleet-state" / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        self.assertTrue(any(e["type"] == "leader_start" for e in events))

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_existing_session_is_idempotent(self) -> None:
        r1 = run_fleet("leader", "--project", str(self.project))
        self.assertEqual(r1.returncode, 0, r1.stderr)
        r2 = run_fleet("leader", "--project", str(self.project))
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("already exists", r2.stdout)


if __name__ == "__main__":
    unittest.main()
