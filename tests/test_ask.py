"""Tests for ``fleet ask``."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
FLEET = ROOT / "fleet"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402


def run_fleet(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(FLEET), *args],
        capture_output=True,
        text=True,
        env=env,
    )


class AskTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        state.init_state(self.project / ".fleet-state", name="demo")
        # Disable notification side-effects in tests.
        (self.project / ".fleet-state" / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n"
        )
        # Spawn one task to ask about.
        result = run_fleet(
            "spawn",
            "--project", str(self.project),
            "--dry-run",
            "1", "Implement foo",
        )
        assert result.returncode == 0, result.stderr

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_records_needs_input(self) -> None:
        result = run_fleet(
            "ask",
            "--task-id", "1",
            "Should I use approach A or B?",
            env_extra={"FLEET_TASK_ID": ""},  # explicit --task-id wins anyway
        )
        # Ensure cwd discovery has somewhere to look — pass cwd=project via cwd kw.
        # subprocess respects cwd separately; here we set --task-id so resolve()
        # only needs state_dir lookup. State lookup walks up from cwd which is
        # claude-forge (parent of agent-fleet) — neither has .fleet-state.
        # Use --task-id + explicit project. There is no --project on `ask`,
        # but task_context.resolve walks from cwd. We need cwd to be inside
        # the project. Easier: re-run with cwd argument.
        # Fall back: invoke from inside project root via cwd.
        env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, str(FLEET), "ask", "--task-id", "1",
             "Should I use approach A or B?"],
            capture_output=True, text=True,
            cwd=str(self.project),
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        sd = self.project / ".fleet-state"
        task = state.load_task(sd, "1")
        self.assertEqual(task["status"], "needs_input")
        questions = (sd / "tasks" / "task-1" / "questions.md").read_text()
        self.assertIn("approach A or B", questions)
        events = [
            json.loads(l) for l in (sd / "events.jsonl").read_text().splitlines() if l
        ]
        needs_input_events = [e for e in events if e["type"] == "needs_input"]
        self.assertEqual(len(needs_input_events), 1)
        self.assertEqual(needs_input_events[0]["task_id"], "1")

    def test_resolves_from_cwd(self) -> None:
        task_cwd = self.project / ".fleet-state" / "tasks" / "task-1"
        env = os.environ.copy()
        env.pop("FLEET_TASK_ID", None)
        result = subprocess.run(
            [sys.executable, str(FLEET), "ask", "from cwd"],
            capture_output=True, text=True,
            cwd=str(task_cwd),
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_task(self) -> None:
        env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, str(FLEET), "ask", "--task-id", "999", "?"],
            capture_output=True, text=True,
            cwd=str(self.project),
            env=env,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("task.yaml missing", result.stderr)


if __name__ == "__main__":
    unittest.main()
