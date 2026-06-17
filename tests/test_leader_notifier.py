from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import leader_notifier, state  # noqa: E402
from fleet.commands import done as done_cmd  # noqa: E402

READY_PANE = 'status\n❯ Try "help"\n'      # claude idle prompt
BUSY_PANE = "✻ Thinking… (esc to interrupt)\n"  # mid-turn, no ❯ prompt


class LeaderNotifierTests(unittest.TestCase):
    """The notifier queue is keyed by SESSION (global/sessions/<label>/), not by
    project (Issue #166 §10.3). ``state_dir`` here is the project the task lives in
    (for outbox scanning); ``session_dir`` is where the queue / lock / events live."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name) / "state"      # project state dir
        state.init_state(self.state_dir, name="demo")
        self.session_dir = Path(self._tmp.name) / "session"  # owner session dir
        self.session_dir.mkdir()
        self._old_no_notify = os.environ.get("FLEET_NO_NOTIFY")
        os.environ["FLEET_NO_NOTIFY"] = "1"
        self._sleep_patch = patch("fleet.leader_notifier.time.sleep", return_value=None)
        self._sleep_patch.start()

    def tearDown(self) -> None:
        self._sleep_patch.stop()
        if self._old_no_notify is None:
            os.environ.pop("FLEET_NO_NOTIFY", None)
        else:
            os.environ["FLEET_NO_NOTIFY"] = self._old_no_notify
        self._tmp.cleanup()

    def _seed_task(self, task_id: str, *, pr_url: str | None = None) -> None:
        tdir = state.task_dir(self.state_dir, task_id)
        tdir.mkdir(parents=True, exist_ok=True)
        if pr_url is not None:
            (tdir / "outbox.md").write_text(
                f"## report\nWork done.\nPR: {pr_url}\n", encoding="utf-8"
            )

    def _record(self, task_id: str, **over) -> dict:
        defaults = dict(
            state_dir=self.state_dir,
            task_id=task_id,
            status="completed",
            branch=f"fleet/task/{task_id}",
            worktree=f"/wt/{task_id}",
            summary=f"task-{task_id} done",
        )
        defaults.update(over)
        return leader_notifier.build_record(**defaults)

    def _events(self) -> list[dict]:
        path = self.session_dir / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    # -- record shape / payload -------------------------------------------

    def test_build_record_idempotent_payload_shape(self) -> None:
        self._seed_task("7", pr_url="https://github.com/o/r/pull/42")
        rec = self._record("7", result="approved")
        for key in ("nonce", "ts", "task_id", "status", "branch", "worktree",
                    "state_dir", "pr_url", "summary"):
            self.assertIn(key, rec)
        self.assertEqual(rec["task_id"], "7")
        self.assertEqual(rec["status"], "completed")
        self.assertEqual(rec["branch"], "fleet/task/7")
        self.assertEqual(rec["worktree"], "/wt/7")
        # The record carries its PROJECT state dir for flush-time outbox re-scan.
        self.assertEqual(rec["state_dir"], str(self.state_dir))
        self.assertEqual(rec["pr_url"], "https://github.com/o/r/pull/42")
        self.assertEqual(rec["summary"], "task-7 done")
        self.assertEqual(rec["result"], "approved")

    def test_scan_pr_url_uses_last_match_and_handles_missing(self) -> None:
        self._seed_task("8", pr_url="https://github.com/o/r/pull/1")
        outbox = state.task_dir(self.state_dir, "8") / "outbox.md"
        outbox.write_text(
            outbox.read_text() + "\nhttps://github.com/o/r/pull/99\n", encoding="utf-8"
        )
        self.assertEqual(
            leader_notifier.scan_pr_url(self.state_dir, "8"),
            "https://github.com/o/r/pull/99",
        )
        self._seed_task("9")  # no outbox
        self.assertIsNone(leader_notifier.scan_pr_url(self.state_dir, "9"))

    # -- queue append + persistence (under the SESSION dir) ----------------

    def test_enqueue_persists_under_session_dir_and_survives_reload(self) -> None:
        self._seed_task("1")
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        qpath = leader_notifier.queue_path(self.session_dir)
        self.assertTrue(qpath.exists())
        self.assertEqual(qpath.parent, self.session_dir)  # lives under the session
        recs = leader_notifier.read_queue(self.session_dir)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["task_id"], "1")

    def test_clear_records_removes_only_flushed_nonces(self) -> None:
        r1, r2 = self._record("1"), self._record("2")
        leader_notifier.enqueue(self.session_dir, r1)
        leader_notifier.enqueue(self.session_dir, r2)
        leader_notifier.clear_records(self.session_dir, {r1["nonce"]})
        remaining = leader_notifier.read_queue(self.session_dir)
        self.assertEqual([r["task_id"] for r in remaining], ["2"])

    # -- render / coalesce -------------------------------------------------

    def test_render_block_coalesces_n_into_one_single_line(self) -> None:
        block = leader_notifier.render_block([self._record("1"), self._record("2")])
        self.assertNotIn("\n", block)  # single line — newline would submit early
        self.assertIn("2 driver notification", block)
        self.assertIn("task-1", block)
        self.assertIn("task-2", block)
        self.assertIn("pull the diff", block)
        self.assertIn("completed+merged", block)

    # -- inject-time PR-URL re-scan (per-record project) ------------------

    def test_refill_fills_missing_pr_url_found_at_inject_time(self) -> None:
        self._seed_task("1")
        rec = self._record("1")
        self.assertIsNone(rec["pr_url"])
        self._seed_task("1", pr_url="https://github.com/o/r/pull/55")
        leader_notifier._refill_pr_urls([rec])
        self.assertEqual(rec["pr_url"], "https://github.com/o/r/pull/55")

    def test_refill_does_not_overwrite_present_pr_url(self) -> None:
        self._seed_task("1", pr_url="https://github.com/o/r/pull/1")
        rec = self._record("1")
        self.assertEqual(rec["pr_url"], "https://github.com/o/r/pull/1")
        self._seed_task("1", pr_url="https://github.com/o/r/pull/999")
        with patch.object(leader_notifier, "scan_pr_url") as scan:
            leader_notifier._refill_pr_urls([rec])
            scan.assert_not_called()
        self.assertEqual(rec["pr_url"], "https://github.com/o/r/pull/1")

    def test_refill_leaves_still_missing_pr_url_null(self) -> None:
        self._seed_task("1")  # no outbox, PR never appeared
        rec = self._record("1")
        leader_notifier._refill_pr_urls([rec])
        self.assertIsNone(rec["pr_url"])

    def test_refill_swallows_scan_errors(self) -> None:
        self._seed_task("1")
        rec = self._record("1")
        with patch.object(leader_notifier, "scan_pr_url", side_effect=OSError("boom")):
            leader_notifier._refill_pr_urls([rec])  # must not raise
        self.assertIsNone(rec["pr_url"])

    def test_flush_injects_pr_url_topped_up_at_inject_time(self) -> None:
        self._seed_task("1")
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        self._seed_task("1", pr_url="https://github.com/o/r/pull/77")
        with (
            patch("fleet.leader_notifier.tmux.session_exists", return_value=True),
            patch("fleet.leader_notifier.tmux.capture_pane", return_value=READY_PANE),
            patch("fleet.leader_notifier.tmux.send_keys") as send_keys,
        ):
            rc = leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.5,
                poll_interval=0.001,
            )
        self.assertEqual(rc, 0)
        send_keys.assert_called_once()
        injected = send_keys.call_args[0][2]
        self.assertIn("https://github.com/o/r/pull/77", injected)
        self.assertNotIn("(none yet)", injected)

    def test_flush_still_renders_none_yet_when_pr_absent(self) -> None:
        self._seed_task("1")  # PR never appears
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            patch("fleet.leader_notifier.tmux.session_exists", return_value=True),
            patch("fleet.leader_notifier.tmux.capture_pane", return_value=READY_PANE),
            patch("fleet.leader_notifier.tmux.send_keys") as send_keys,
        ):
            rc = leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.5,
                poll_interval=0.001,
            )
        self.assertEqual(rc, 0)
        send_keys.assert_called_once()
        self.assertIn("PR=(none yet)", send_keys.call_args[0][2])

    # -- inject-only-on-ready ---------------------------------------------

    def test_busy_leader_never_injects_keeps_queued(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            patch("fleet.leader_notifier.tmux.session_exists", return_value=True),
            patch("fleet.leader_notifier.tmux.capture_pane", return_value=BUSY_PANE),
            patch("fleet.leader_notifier.tmux.send_keys") as send_keys,
        ):
            rc = leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.05,
                poll_interval=0.001,
            )
        self.assertEqual(rc, 0)
        send_keys.assert_not_called()  # never mid-turn
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)  # still queued

    def test_idle_leader_injects_once_coalesced_and_clears(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        leader_notifier.enqueue(self.session_dir, self._record("2"))
        with (
            patch("fleet.leader_notifier.tmux.session_exists", return_value=True),
            patch("fleet.leader_notifier.tmux.capture_pane", return_value=READY_PANE),
            patch("fleet.leader_notifier.tmux.send_keys") as send_keys,
        ):
            rc = leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.5,
                poll_interval=0.001,
            )
        self.assertEqual(rc, 0)
        send_keys.assert_called_once()
        args, kwargs = send_keys.call_args
        self.assertEqual(args[0], "fleet-main")
        self.assertEqual(args[1], "leader")
        self.assertIn("task-1", args[2])
        self.assertIn("task-2", args[2])
        self.assertTrue(kwargs.get("enter"))
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])
        # leader_notified event lands in the SESSION's events.jsonl.
        last = self._events()[-1]
        self.assertEqual(last["type"], "leader_notified")
        self.assertEqual(last["count"], 2)
        self.assertEqual(sorted(last["task_ids"]), ["1", "2"])

    def test_leader_detached_leaves_records_queued(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            patch("fleet.leader_notifier.tmux.session_exists", return_value=False),
            patch("fleet.leader_notifier.tmux.send_keys") as send_keys,
        ):
            rc = leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.05,
                poll_interval=0.001,
            )
        self.assertEqual(rc, 0)
        send_keys.assert_not_called()
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)

    def test_empty_queue_is_noop(self) -> None:
        with patch("fleet.leader_notifier.tmux.session_exists") as exists:
            rc = leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.05,
            )
        self.assertEqual(rc, 0)
        exists.assert_not_called()  # bailed before touching tmux

    def test_second_notifier_noops_while_lock_held(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        fp = leader_notifier._acquire_lock(self.session_dir)
        self.assertIsNotNone(fp)
        try:
            with (
                patch("fleet.leader_notifier.tmux.session_exists") as exists,
                patch("fleet.leader_notifier.tmux.send_keys") as send_keys,
            ):
                rc = leader_notifier.notify(
                    session_dir=self.session_dir,
                    session="fleet-main",
                    window="leader",
                    agent_spec="claude:opus",
                    timeout=0.05,
                )
            self.assertEqual(rc, 0)
            exists.assert_not_called()  # lock held → immediate no-op
            send_keys.assert_not_called()
        finally:
            leader_notifier._release_lock(fp)
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)


class DoneHookTests(unittest.TestCase):
    """done.py wiring, now routed by ``owner_session`` (Issue #166 §10.3).

    Default OFF means zero behaviour change. The queue + leader record live under
    the owner session's dir (``global/sessions/<label>/``); a missing
    ``owner_session`` is treated as ``main``."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo")
        self.task_id = "1"
        tdir = state.task_dir(self.state_dir, self.task_id)
        tdir.mkdir(parents=True, exist_ok=True)
        # No owner_session on the task → defaults to "main".
        self.task = {
            "id": self.task_id,
            "status": "completed",
            "branch": "fleet/task/1",
            "worktree": "/wt/1",
        }
        self.session_dir = state.session_dir("main")

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def test_truthy_parsing(self) -> None:
        for v in ("true", "True", "1", "yes", "on", "  TRUE "):
            self.assertTrue(done_cmd._truthy(v))
        for v in ("false", "0", "no", "", None, "off"):
            self.assertFalse(done_cmd._truthy(v))

    def test_default_off_spawns_nothing(self) -> None:
        project = {"name": "demo"}  # no notify_leader_on_driver_done key
        with patch("fleet.leader_notifier.start_detached") as spawn:
            done_cmd._maybe_notify_leader(
                self.state_dir, self.task_id, self.task, project, "demo",
                status="completed", result="approved", summary="done",
            )
        spawn.assert_not_called()
        self.assertFalse(leader_notifier.queue_path(self.session_dir).exists())

    def test_on_enqueues_to_session_dir_then_skips_spawn_when_no_record(self) -> None:
        project = {"name": "demo", "notify_leader_on_driver_done": "true"}
        with (
            patch("fleet.commands.done.tmux.available", return_value=True),
            patch("fleet.commands.done.tmux.session_exists", return_value=True),
            patch("fleet.leader_notifier.start_detached") as spawn,
        ):
            done_cmd._maybe_notify_leader(
                self.state_dir, self.task_id, self.task, project, "demo",
                status="completed", result="approved", summary="done",
            )
        # No session.json record for "main" → no spawn, but record durably queued
        # under the SESSION dir (not the project state dir).
        spawn.assert_not_called()
        recs = leader_notifier.read_queue(self.session_dir)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["task_id"], "1")
        self.assertEqual(recs[0]["state_dir"], str(self.state_dir))

    def test_on_with_session_record_enqueues_and_spawns(self) -> None:
        project = {"name": "demo", "notify_leader_on_driver_done": "true"}
        rec_path = state.session_record_path("main")
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(
            json.dumps({"label": "main", "agent": "claude:opus"}), encoding="utf-8"
        )
        with (
            patch("fleet.commands.done.tmux.available", return_value=True),
            patch("fleet.commands.done.tmux.session_exists", return_value=True),
            patch("fleet.leader_notifier.start_detached") as spawn,
        ):
            done_cmd._maybe_notify_leader(
                self.state_dir, self.task_id, self.task, project, "demo",
                status="completed", result="approved", summary="done",
            )
        spawn.assert_called_once()
        kwargs = spawn.call_args.kwargs
        self.assertEqual(kwargs["session_dir"], self.session_dir)
        self.assertEqual(kwargs["session"], "fleet-main")
        self.assertEqual(kwargs["window"], "leader")
        self.assertEqual(kwargs["agent_spec"], "claude:opus")
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)

    def test_owner_session_routes_to_named_session(self) -> None:
        # A task spawned by a non-default session routes to fleet-<label>.
        project = {"name": "demo", "notify_leader_on_driver_done": "true"}
        task = dict(self.task, owner_session="migration")
        rec_path = state.session_record_path("migration")
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(
            json.dumps({"label": "migration", "agent": "codex:gpt-5.5"}), encoding="utf-8"
        )
        with (
            patch("fleet.commands.done.tmux.available", return_value=True),
            patch("fleet.commands.done.tmux.session_exists", return_value=True),
            patch("fleet.leader_notifier.start_detached") as spawn,
        ):
            done_cmd._maybe_notify_leader(
                self.state_dir, self.task_id, task, project, "demo",
                status="completed", result="approved", summary="done",
            )
        spawn.assert_called_once()
        kwargs = spawn.call_args.kwargs
        self.assertEqual(kwargs["session"], "fleet-migration")
        self.assertEqual(kwargs["session_dir"], state.session_dir("migration"))
        self.assertEqual(kwargs["agent_spec"], "codex:gpt-5.5")
        # Record queued under the migration session, not main.
        self.assertEqual(
            len(leader_notifier.read_queue(state.session_dir("migration"))), 1
        )
        self.assertFalse(leader_notifier.queue_path(state.session_dir("main")).exists())


if __name__ == "__main__":
    unittest.main()
