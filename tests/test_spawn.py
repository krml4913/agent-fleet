"""Tests for ``fleet spawn`` (dry-run path — tmux not exercised)."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
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


class SpawnTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        state.init_state(self.project / ".fleet-state", name="demo")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dry_run_creates_task_artifacts(self) -> None:
        result = run_fleet(
            "spawn",
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
        # task.yaml has the resolved agent + topology
        text = (tdir / "task.yaml").read_text()
        self.assertIn("id: 7", text)
        self.assertIn("agent: claude:sonnet", text)
        self.assertIn("status: spawning", text)
        self.assertIn("topology: solo", text)
        # event recorded
        events_path = sd / "events.jsonl"
        lines = [json.loads(l) for l in events_path.read_text().splitlines() if l]
        spawns = [e for e in lines if e.get("type") == "spawn"]
        self.assertEqual(len(spawns), 1)
        self.assertEqual(spawns[0]["task_id"], "7")
        self.assertTrue(spawns[0].get("dry_run"))

    def test_rejects_duplicate_task_id(self) -> None:
        run_fleet(
            "spawn", "--project", str(self.project), "--dry-run",
            "1", "first",
        )
        result = run_fleet(
            "spawn", "--project", str(self.project), "--dry-run",
            "1", "second",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists", result.stderr)

    def test_agent_override(self) -> None:
        result = run_fleet(
            "spawn",
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

    def test_topology_pair_review(self) -> None:
        result = run_fleet(
            "spawn",
            "--project", str(self.project),
            "--dry-run",
            "--topology", "pair_review",
            "3", "Pair flow",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (
            self.project / ".fleet-state" / "tasks" / "task-3" / "task.yaml"
        ).read_text()
        # First role of pair_review is implementer with claude:sonnet
        self.assertIn("role: implementer", text)
        self.assertIn("agent: claude:sonnet", text)

    def test_role_override(self) -> None:
        result = run_fleet(
            "spawn",
            "--project", str(self.project),
            "--dry-run",
            "--topology", "pair_review",
            "--role", "reviewer",
            "4", "Review this",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (
            self.project / ".fleet-state" / "tasks" / "task-4" / "task.yaml"
        ).read_text()
        self.assertIn("role: reviewer", text)
        self.assertIn("agent: claude:opus", text)

    def test_unknown_role(self) -> None:
        result = run_fleet(
            "spawn",
            "--project", str(self.project),
            "--dry-run",
            "--topology", "pair_review",
            "--role", "nobody",
            "5", "Bad",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not in topology", result.stderr)

    def test_no_state_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_fleet(
                "spawn",
                "--project", tmp,
                "--dry-run",
                "9", "no state",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("no .fleet-state", result.stderr)

    def test_no_auto_paste_flag_is_accepted(self) -> None:
        result = run_fleet(
            "spawn",
            "--project", str(self.project),
            "--dry-run",
            "--no-auto-paste",
            "10", "Manual paste task",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        tdir = self.project / ".fleet-state" / "tasks" / "task-10"
        self.assertTrue(tdir.is_dir())

    def test_auto_prompt_flag_removed(self) -> None:
        result = run_fleet(
            "spawn",
            "--project", str(self.project),
            "--dry-run",
            "--auto-prompt",
            "11", "Old flag test",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
