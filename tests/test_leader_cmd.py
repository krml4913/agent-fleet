"""Tests for ``fleet leader``."""
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import tmux  # noqa: E402
from tests._fleet_test_helpers import run_fleet, make_project  # noqa: E402


class LeaderCmdTests(unittest.TestCase):
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

    def test_no_project_found(self) -> None:
        r = run_fleet("leader", "--project", "nonexistent",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no registered project", r.stderr)

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_launch_creates_session(self) -> None:
        r = run_fleet("leader", "--project", self.project_name,
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(tmux.session_exists(self.session))
        events_path = self.state_dir / "events.jsonl"
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        self.assertTrue(any(e["type"] == "leader_start" for e in events))

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_existing_session_is_idempotent(self) -> None:
        r1 = run_fleet("leader", "--project", self.project_name,
                       fleet_home=self.fleet_home)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        r2 = run_fleet("leader", "--project", self.project_name,
                       fleet_home=self.fleet_home)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("already exists", r2.stdout)

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_launch_writes_leader_prompt(self) -> None:
        r = run_fleet("leader", "--project", self.project_name,
                      "--prompt-delay", "0",
                      fleet_home=self.fleet_home)
        self.assertEqual(r.returncode, 0, r.stderr)
        prompt_path = self.state_dir / "leader-prompt.md"
        self.assertTrue(prompt_path.exists(), "leader-prompt.md was not written")
        content = prompt_path.read_text()
        self.assertIn("You are the leader of a fleet project", content)
        self.assertIn(self.project_name, content)

    def test_no_auto_paste_skips_prompt_file(self) -> None:
        run_fleet("leader", "--project", self.project_name,
                  "--no-auto-paste",
                  fleet_home=self.fleet_home)
        prompt_path = self.state_dir / "leader-prompt.md"
        self.assertFalse(prompt_path.exists(), "leader-prompt.md should not be written with --no-auto-paste")

    @unittest.skipIf(shutil.which("tmux") is None, "tmux not available")
    def test_prompt_file_writes_leader_prompt(self) -> None:
        prompt_file = self.project / "leader-prompt-source.md"
        prompt_text = "Custom leader prompt\nUse this exact text.\n"
        prompt_file.write_text(prompt_text)

        r = run_fleet("leader", "--project", self.project_name,
                      "--prompt-delay", "0",
                      "--prompt-file", str(prompt_file),
                      fleet_home=self.fleet_home)

        self.assertEqual(r.returncode, 0, r.stderr)
        prompt_path = self.state_dir / "leader-prompt.md"
        self.assertEqual(prompt_path.read_text(), prompt_text)

    def test_missing_prompt_file_is_error(self) -> None:
        missing = self.project / "does-not-exist.md"

        r = run_fleet("leader", "--project", self.project_name,
                      "--prompt-file", str(missing),
                      fleet_home=self.fleet_home)

        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot read --prompt-file", r.stderr)
        self.assertIn(str(missing), r.stderr)
        self.assertFalse((self.state_dir / "leader-prompt.md").exists())

    def test_prompt_file_with_no_auto_paste_is_error(self) -> None:
        prompt_file = self.project / "leader-prompt-source.md"
        prompt_file.write_text("Custom leader prompt\n")

        r = run_fleet("leader", "--project", self.project_name,
                      "--prompt-file", str(prompt_file),
                      "--no-auto-paste",
                      fleet_home=self.fleet_home)

        self.assertEqual(r.returncode, 1)
        self.assertIn("--prompt-file requires auto-paste", r.stderr)
        self.assertFalse((self.state_dir / "leader-prompt.md").exists())


if __name__ == "__main__":
    unittest.main()
