"""Tests for ``fleet cleanup``."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import state  # noqa: E402
from tests._fleet_test_helpers import run_fleet_agent  # noqa: E402
from fleet.commands.cleanup import run  # noqa: E402
from tests._fake_mux import use_fake_mux  # noqa: E402


class CleanupCmdTests(unittest.TestCase):
    def setUp(self) -> None:
        # The suite may itself run inside a driver pane; ``cleanup`` refuses there.
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("FLEET_TASK_ID", None)
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo", repo=self.project)
        (self.state_dir / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(
        self, *args: str, cwd: Path | None = None, env_extra: dict | None = None
    ) -> subprocess.CompletedProcess[str]:
        return run_fleet_agent(
            *args, cwd=cwd or self.project,
            env_extra={"FLEET_STATE_DIR": str(self.state_dir), **(env_extra or {})},
        )

    def _save(self, task_id: str, status: str) -> None:
        state.save_task(self.state_dir, task_id, {
            "id": task_id, "title": f"t{task_id}", "status": status,
            "agent": "claude:sonnet", "workspace": "none",
            "owner_session": "demo",
        })

    def test_refuses_non_terminal_without_force(self) -> None:
        self._save("1", "spawning")
        r = self._run("cleanup", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("refusing", r.stderr)

    def test_refuses_awaiting_orders_without_force(self) -> None:
        self._save("1", "awaiting_orders")
        r = self._run("cleanup", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("status=awaiting_orders", r.stderr)

    def test_completed_cleans_up(self) -> None:
        self._save("1", "completed")
        r = self._run("cleanup", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        events_path = self.state_dir / "events.jsonl"
        events = [
            json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line
        ]
        self.assertTrue(any(e["type"] == "cleanup" for e in events))

    def test_force_overrides(self) -> None:
        self._save("2", "spawning")
        r = self._run("cleanup", "2", "--force")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_archive_moves_dir(self) -> None:
        self._save("3", "completed")
        r = self._run("cleanup", "3", "--archive")
        self.assertEqual(r.returncode, 0, r.stderr)
        sd = self.state_dir
        self.assertFalse((sd / "tasks" / "task-3").exists())
        self.assertTrue((sd / "tasks" / "_archive" / "task-3").is_dir())
        self.assertEqual(state.list_tasks(sd), [])

    def test_archive_collision_uniquifies_and_preserves_existing(self) -> None:
        # A re-spawned task id collides with an archive left by a prior cleanup.
        # The live dir must still be archived (no stranding) under a unique name,
        # and the existing archive must stay untouched.
        sd = self.state_dir
        archive_root = sd / "tasks" / "_archive"
        archive_root.mkdir(parents=True, exist_ok=True)
        old = archive_root / "task-3"
        old.mkdir()
        (old / "marker.txt").write_text("first run", encoding="utf-8")

        self._save("3", "completed")
        # Mark the live (re-spawned) dir so we can tell the two apart.
        (state.task_dir(sd, "3") / "marker.txt").write_text("second run", encoding="utf-8")

        r = self._run("cleanup", "3", "--archive")
        self.assertEqual(r.returncode, 0, r.stderr)

        self.assertFalse((sd / "tasks" / "task-3").exists())  # no stranding
        self.assertEqual((old / "marker.txt").read_text(encoding="utf-8"), "first run")  # untouched
        self.assertTrue((archive_root / "task-3-2").is_dir())
        self.assertEqual(
            (archive_root / "task-3-2" / "marker.txt").read_text(encoding="utf-8"), "second run"
        )
        self.assertEqual(state.list_tasks(sd), [])

    def test_archive_collision_chains_to_next_free_suffix(self) -> None:
        # task-3 and task-3-2 both already archived → the next archive goes to -3.
        sd = self.state_dir
        archive_root = sd / "tasks" / "_archive"
        archive_root.mkdir(parents=True, exist_ok=True)
        (archive_root / "task-3").mkdir()
        (archive_root / "task-3-2").mkdir()

        self._save("3", "completed")
        r = self._run("cleanup", "3", "--archive")
        self.assertEqual(r.returncode, 0, r.stderr)

        self.assertFalse((sd / "tasks" / "task-3").exists())
        self.assertTrue((archive_root / "task-3-3").is_dir())

    def test_unknown_task(self) -> None:
        r = self._run("cleanup", "999")
        self.assertEqual(r.returncode, 1)
        self.assertIn("task.yaml missing", r.stderr)

    # -- leader-only guard (driver pane detected via FLEET_TASK_ID) ----------

    def test_refuses_from_driver_pane(self) -> None:
        self._save("1", "completed")
        r = self._run("cleanup", "1", env_extra={"FLEET_TASK_ID": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("leader-only", r.stderr)
        self.assertIn("--allow-from-driver", r.stderr)
        self.assertTrue(state.task_dir(self.state_dir, "1").exists())
        events_path = self.state_dir / "events.jsonl"
        self.assertFalse(events_path.exists() and "cleanup" in events_path.read_text(encoding="utf-8"))

    def test_force_does_not_override_driver_guard(self) -> None:
        # --force means "skip the terminal-status guard", not "I am the leader".
        self._save("2", "spawning")
        r = self._run("cleanup", "2", "--force", env_extra={"FLEET_TASK_ID": "2"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("leader-only", r.stderr)

    def test_allow_from_driver_overrides_guard(self) -> None:
        self._save("1", "completed")
        r = self._run("cleanup", "1", "--allow-from-driver", env_extra={"FLEET_TASK_ID": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cleanup_kills_task_windows_by_task_id(self) -> None:
        self._save("stage-transition", "completed")
        args = MagicMock()
        args.task_id = "stage-transition"
        # "." → resolve via FLEET_STATE_DIR (patched below), not registry name.
        args.project = "."
        args.archive = False
        args.force = False
        args.allow_from_driver = False

        windows = ["leader", "stage-transition·designer", "stage-transition·implementer", "other·driver"]
        with (
            patch.dict(os.environ, {"FLEET_STATE_DIR": str(self.state_dir)}, clear=False),
            use_fake_mux(sessions={"fleet-demo": windows}) as fake,
        ):
            result = run(args)

        self.assertEqual(result, 0)
        self.assertEqual(
            [a for a, _k in fake.calls_named("kill_window")],
            [
                ("fleet-demo", "stage-transition·designer"),
                ("fleet-demo", "stage-transition·implementer"),
            ],
        )
        self.assertEqual(fake.sessions["fleet-demo"], ["leader", "other·driver"])
        # The manual-paste pointer staged at launch is dropped too.
        self.assertEqual(
            fake.calls_named("drop_paste"), [(("fleet-task-stage-transition",), {})]
        )


if __name__ == "__main__":
    unittest.main()
