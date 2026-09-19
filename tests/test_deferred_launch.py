"""Tests for :mod:`fleet.deferred_launch` and the orchestrator's use of it.

Regression for the Windows/zellij two-stage E2E: a driver's ``fleet-agent
done`` advanced to the next stage from *inside* the stage-1 tab; killing that
tab (to replace the task's windows) ended the ``done`` process before the
stage-2 tab was opened, stranding the task.
"""
from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import deferred_launch, orchestrator, state  # noqa: E402
from tests._fake_mux import use_fake_mux  # noqa: E402


def _two_stage_task(state_dir: Path, task_id: str, *, current: int = 0) -> dict:
    stages = [
        {"role": "designer", "agent": "claude:haiku", "status": "running" if current == 0 else "done"},
        {"role": "implementer", "agent": "claude:haiku", "status": "pending" if current == 0 else "running"},
    ]
    task = {
        "id": task_id,
        "title": "t",
        "description": "d",
        "status": "running",
        "formation": "two_stage",
        "workspace": "none",
        "owner_session": "main",
        "current_stage": current,
        "stages": stages,
    }
    state.save_task(state_dir, task_id, task)
    td = state.task_dir(state_dir, task_id)
    for name in ("driver-prompt.md", "inbox.md", "outbox.md"):
        (td / name).write_text("", encoding="utf-8")
    return task


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        project = Path(self._tmp.name) / "proj"
        project.mkdir()
        self.sd = project / ".fleet-state"
        state.init_state(self.sd, name="demo")
        (self.sd / "notify.yaml").write_text(
            "macos:\n  enabled: false\nslack:\n  enabled: false\n", encoding="utf-8"
        )
        # Project-level roles so prompt rendering never depends on global state.
        for role in ("designer", "implementer"):
            (self.sd / "roles" / f"{role}.md").write_text(f"{role} role\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()


class OrchestratorDeferralTests(_Base):
    def _advance(self, *, kills_caller: bool, env_task_id: str | None, task_id: str = "s1"):
        task = _two_stage_task(self.sd, task_id)
        env = {k: v for k, v in os.environ.items() if k != "FLEET_TASK_ID"}
        if env_task_id is not None:
            env["FLEET_TASK_ID"] = env_task_id
        with (
            use_fake_mux(sessions={"fleet-main": ["leader", f"{task_id}·designer"]}) as fake,
            unittest.mock.patch.dict(os.environ, env, clear=True),
            unittest.mock.patch("fleet.commands.start.launch_stage_driver") as launch,
            unittest.mock.patch.object(deferred_launch, "start_detached") as deferred,
        ):
            fake.window_close_kills_caller = kills_caller
            orchestrator.advance(self.sd, task_id, task, result="approved")
        return launch, deferred, state.load_task(self.sd, task_id)

    def test_done_from_own_pane_defers_launch_when_close_kills_caller(self) -> None:
        launch, deferred, task = self._advance(kills_caller=True, env_task_id="s1")
        launch.assert_not_called()
        deferred.assert_called_once()
        kw = deferred.call_args.kwargs
        self.assertEqual(kw["task_id"], "s1")
        self.assertEqual(kw["stage_idx"], 1)
        self.assertEqual(kw["wait_pid"], os.getpid())
        # State is advanced synchronously; only the window work is deferred.
        self.assertEqual(task["current_stage"], 1)
        self.assertEqual(task["stages"][0]["status"], "done")
        self.assertEqual(task["stages"][1]["status"], "running")

    def test_tmux_like_backend_launches_synchronously(self) -> None:
        launch, deferred, _task = self._advance(kills_caller=False, env_task_id="s1")
        deferred.assert_not_called()
        launch.assert_called_once()
        self.assertTrue(launch.call_args.kwargs["replace_task_windows"])

    def test_caller_outside_the_task_pane_launches_synchronously(self) -> None:
        # e.g. the leader's ``fleet-agent approve`` — its window is not killed.
        for env_task_id in (None, "other-task"):
            with self.subTest(env_task_id=env_task_id):
                launch, deferred, _task = self._advance(
                    kills_caller=True, env_task_id=env_task_id, task_id="s2"
                )
                deferred.assert_not_called()
                launch.assert_called_once()

    def test_first_reviewer_launch_is_not_deferred(self) -> None:
        # replace_task_windows=False kills nothing, so it is safe in-pane.
        task = _two_stage_task(self.sd, "r1")
        stage = task["stages"][0]
        with (
            use_fake_mux(sessions={"fleet-main": ["leader", "r1·designer"]}) as fake,
            unittest.mock.patch.dict(os.environ, {"FLEET_TASK_ID": "r1"}),
            unittest.mock.patch("fleet.commands.start.launch_stage_driver") as launch,
            unittest.mock.patch.object(deferred_launch, "start_detached") as deferred,
        ):
            fake.window_close_kills_caller = True
            orchestrator._launch_driver_for_stage(
                self.sd, "r1", task, 0, stage, replace_task_windows=False
            )
        deferred.assert_not_called()
        launch.assert_called_once()


class DeferredLaunchRunTests(_Base):
    def test_waits_for_caller_then_launches_current_stage(self) -> None:
        _two_stage_task(self.sd, "s1", current=1)
        with (
            unittest.mock.patch.object(deferred_launch, "_wait_for_exit", return_value=True) as wait,
            unittest.mock.patch.object(orchestrator, "_launch_driver_for_stage") as launch,
        ):
            rc = deferred_launch.run(state_dir=self.sd, task_id="s1", stage_idx=1, wait_pid=4242)
        self.assertEqual(rc, 0)
        wait.assert_called_once_with(4242, deferred_launch.DEFAULT_WAIT_SECONDS)
        launch.assert_called_once()
        args, kwargs = launch.call_args
        self.assertEqual(args[1], "s1")
        self.assertEqual(args[3], 1)
        self.assertEqual(args[4]["role"], "implementer")
        self.assertFalse(kwargs["defer_if_in_pane"])

    def test_launches_even_if_caller_outlives_the_wait(self) -> None:
        _two_stage_task(self.sd, "s1", current=1)
        with (
            unittest.mock.patch.object(deferred_launch, "_wait_for_exit", return_value=False),
            unittest.mock.patch.object(orchestrator, "_launch_driver_for_stage") as launch,
        ):
            deferred_launch.run(state_dir=self.sd, task_id="s1", stage_idx=1, wait_pid=4242)
        launch.assert_called_once()

    def test_skips_when_task_moved_on(self) -> None:
        task = _two_stage_task(self.sd, "s1", current=1)
        task["stages"][1]["status"] = "done"
        task["status"] = "completed"
        state.save_task(self.sd, "s1", task)
        with (
            unittest.mock.patch.object(deferred_launch, "_wait_for_exit", return_value=True),
            unittest.mock.patch.object(orchestrator, "_launch_driver_for_stage") as launch,
        ):
            deferred_launch.run(state_dir=self.sd, task_id="s1", stage_idx=1, wait_pid=1)
            deferred_launch.run(state_dir=self.sd, task_id="s1", stage_idx=0, wait_pid=1)
        launch.assert_not_called()

    def test_wait_for_exit_polls_pid(self) -> None:
        alive = iter([True, True, False])
        with (
            unittest.mock.patch("fleet.pane_launch.pid_alive", side_effect=lambda _p: next(alive)),
            unittest.mock.patch.object(deferred_launch.time, "sleep"),
        ):
            self.assertTrue(deferred_launch._wait_for_exit(99, 5.0))

    def test_start_detached_spawns_module_and_logs_event(self) -> None:
        _two_stage_task(self.sd, "s1", current=1)
        with unittest.mock.patch.object(deferred_launch, "spawn_detached") as spawn:
            log = deferred_launch.start_detached(
                state_dir=self.sd, task_id="s1", stage_idx=1, wait_pid=777
            )
        argv = spawn.call_args.args[0]
        self.assertEqual(argv[1:3], ["-m", "fleet.deferred_launch"])
        self.assertIn("--wait-pid", argv)
        self.assertEqual(argv[argv.index("--wait-pid") + 1], "777")
        self.assertEqual(argv[argv.index("--stage-idx") + 1], "1")
        self.assertEqual(log.name, deferred_launch.LOG_NAME)
        events = (self.sd / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"stage_launch_deferred"', events)

    def test_main_parses_args(self) -> None:
        with unittest.mock.patch.object(deferred_launch, "run", return_value=0) as run:
            rc = deferred_launch.main(
                ["--state-dir", str(self.sd), "--task-id", "s1", "--stage-idx", "1", "--wait-pid", "5"]
            )
        self.assertEqual(rc, 0)
        kw = run.call_args.kwargs
        self.assertEqual((kw["task_id"], kw["stage_idx"], kw["wait_pid"]), ("s1", 1, 5))


class RunningInTaskPaneTests(unittest.TestCase):
    def test_matches_only_this_task(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"FLEET_TASK_ID": "a1"}):
            self.assertTrue(deferred_launch.running_in_task_pane("a1"))
            self.assertFalse(deferred_launch.running_in_task_pane("b2"))
        env = {k: v for k, v in os.environ.items() if k != "FLEET_TASK_ID"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(deferred_launch.running_in_task_pane("a1"))


if __name__ == "__main__":
    unittest.main()
