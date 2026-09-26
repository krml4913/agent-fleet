from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import tests._fleet_test_helpers  # noqa: E402,F401  (hermetic env: FLEET_NO_NOTIFY / FLEET_NO_MUX)
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import leader_notifier, mux, state  # noqa: E402
from fleet.commands import ask as ask_cmd  # noqa: E402
from fleet.commands import done as done_cmd  # noqa: E402
from tests import _pane_fixtures as pane_fx  # noqa: E402
from tests._fake_mux import use_fake_mux  # noqa: E402

READY_PANE = 'status\n❯ Try "help"\n'      # claude idle prompt
BUSY_PANE = "✻ Thinking… (esc to interrupt)\n"  # mid-turn, no ❯ prompt

_REAL_RUN_CAPTURE = leader_notifier._run_capture
_lookup_patch = None


def setUpModule() -> None:
    """Hermetic: no test may run a real ``git`` / ``gh`` for the PR lookups."""
    global _lookup_patch
    _lookup_patch = patch.object(leader_notifier, "_run_capture", return_value=None)
    _lookup_patch.start()


def tearDownModule() -> None:
    if _lookup_patch is not None:
        _lookup_patch.stop()


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
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

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
            outbox.read_text(encoding="utf-8") + "\nhttps://github.com/o/r/pull/99\n", encoding="utf-8"
        )
        self.assertEqual(
            leader_notifier.scan_pr_url(self.state_dir, "8"),
            "https://github.com/o/r/pull/99",
        )
        self._seed_task("9")  # no outbox
        self.assertIsNone(leader_notifier.scan_pr_url(self.state_dir, "9"))

    def test_incident_outbox_yields_the_right_pr_url_in_the_rendered_block(self) -> None:
        """Issue #329: the record was right (``pull/328``); the leader saw ``pull/3``.

        The outbox is the one the fix-trust-gate driver wrote right before ``done``
        (URL first, then prose mentioning other issues); the scan, the record and
        the rendered block must all carry the full URL, ending the block.
        """
        self._seed_task("trust")
        outbox = state.task_dir(self.state_dir, "trust") / "outbox.md"
        outbox.write_text(
            "\n## 2026-09-26 — #327 fix ready for review\n\n"
            "PR: https://github.com/krml4913/agent-fleet/pull/328 (CI green: Linux 3.11/3.12/3.13, "
            "Windows, zellij-linux). Not merged.\n\n"
            "- corrects the #259 note in docs/windows-support.md.\n",
            encoding="utf-8",
        )
        url = "https://github.com/krml4913/agent-fleet/pull/328"
        self.assertEqual(leader_notifier.scan_pr_url(self.state_dir, "trust"), url)
        rec = self._record("trust", status="awaiting_orders")
        self.assertEqual(rec["pr_url"], url)
        self.assertTrue(leader_notifier.render_block([rec]).endswith(f"PR={url}"))

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

    def test_render_block_does_not_include_driver_result(self) -> None:
        """render_block must not expose the driver's self-reported result= flag.

        Showing ``result=approved`` alongside a gate event misleads the leader into
        thinking the user_approval gate has already been decided.
        """
        block = leader_notifier.render_block([self._record("1", result="approved")])
        self.assertNotIn("result=", block)

    # -- ask records (issue #287) ------------------------------------------

    def _ask_record(self, task_id: str, question: str = "A or B?", **over) -> dict:
        defaults = dict(
            status="awaiting_orders",
            summary=f"task-{task_id} awaiting orders",
            kind="ask",
            question=question,
            project="demo",
        )
        defaults.update(over)
        return self._record(task_id, **defaults)

    def test_ask_record_carries_question_and_skips_pr_lookup(self) -> None:
        self._seed_task("3", pr_url="https://github.com/o/r/pull/42")
        with patch.object(leader_notifier, "scan_pr_url") as scan:
            rec = self._ask_record("3", "Use approach A or B?")
            scan.assert_not_called()  # an ask needs no diff
        self.assertEqual(rec["kind"], "ask")
        self.assertEqual(rec["status"], "awaiting_orders")
        self.assertEqual(rec["question"], "Use approach A or B?")
        self.assertEqual(rec["project"], "demo")
        self.assertIsNone(rec["pr_url"])

    def test_done_record_defaults_to_kind_done(self) -> None:
        rec = self._record("1")
        self.assertEqual(rec["kind"], "done")
        self.assertNotIn("question", rec)

    def test_render_ask_tells_leader_to_answer_via_inbox_not_run_the_gate(self) -> None:
        block = leader_notifier.render_block([self._ask_record("3", "A or B?")])
        self.assertNotIn("\n", block)
        self.assertIn("task-3 [ask]", block)
        self.assertIn("question: A or B?", block)
        self.assertIn('fleet-agent inbox 3 "<answer>" --project demo', block)
        self.assertNotIn("pull the diff", block)
        self.assertNotIn("run the gate", block)
        self.assertNotIn("completed+merged", block)
        self.assertIn("awaiting_orders", block)

    def test_render_ask_flattens_multiline_question_to_one_line(self) -> None:
        block = leader_notifier.render_block(
            [self._ask_record("3", "line one\n\n  line   two\r\nline three")]
        )
        self.assertNotIn("\n", block)
        self.assertNotIn("\r", block)
        self.assertIn("question: line one line two line three", block)

    def test_render_ask_without_project_omits_project_flag(self) -> None:
        rec = self._ask_record("3")
        rec.pop("project")
        block = leader_notifier.render_block([rec])
        self.assertIn('fleet-agent inbox 3 "<answer>"', block)
        self.assertNotIn("--project", block)

    def test_render_mixed_keeps_gate_instruction_for_done_and_ask_instruction_for_ask(self) -> None:
        block = leader_notifier.render_block([self._record("1"), self._ask_record("2")])
        self.assertIn("2 driver notification", block)
        self.assertIn("pull the diff and run the gate", block)
        self.assertIn("NOT marked [ask]", block)
        self.assertIn('fleet-agent inbox 2 "<answer>" --project demo', block)
        self.assertIn("task-1 [completed]", block)
        self.assertIn("task-2 [ask]", block)

    def test_ask_and_done_for_same_task_do_not_suppress_each_other(self) -> None:
        """The only record identity is the per-record nonce: an ask must not swallow
        a later done/gate for the same task (nor the reverse)."""
        self._seed_task("5")
        ask = self._ask_record("5")
        gate = self._record("5", status="awaiting_orders")
        done = self._record("5")
        self.assertEqual(len({ask["nonce"], gate["nonce"], done["nonce"]}), 3)
        for rec in (ask, gate, done):
            leader_notifier.enqueue(self.session_dir, rec)
        self.assertEqual(
            [r["kind"] for r in leader_notifier.read_queue(self.session_dir)],
            ["ask", "done", "done"],
        )
        # Flushing one record by nonce leaves the other kinds queued.
        leader_notifier.clear_records(self.session_dir, {ask["nonce"]})
        self.assertEqual(
            [r["kind"] for r in leader_notifier.read_queue(self.session_dir)], ["done", "done"]
        )

    def test_flush_delivers_ask_then_done_of_same_task_in_one_block(self) -> None:
        self._seed_task("5")
        leader_notifier.enqueue(self.session_dir, self._ask_record("5", "ship it?"))
        leader_notifier.enqueue(self.session_dir, self._record("5"))
        with use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=READY_PANE) as fake:
            rc = leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.5,
                poll_interval=0.001,
            )
        self.assertEqual(rc, 0)
        (args, _kwargs), = fake.calls_named("send_text")
        self.assertIn("task-5 [ask] question: ship it?", args[2])
        self.assertIn("task-5 [completed]", args[2])
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])

    def test_refill_never_scans_or_calls_gh_for_ask_records(self) -> None:
        rec = self._ask_record("6")
        with patch.object(leader_notifier, "scan_pr_url") as scan:
            leader_notifier._refill_pr_urls([rec])
            scan.assert_not_called()
        self.assertIsNone(rec["pr_url"])

    def test_clear_task_records_evicts_ask_records_too(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._ask_record("1"))
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        leader_notifier.enqueue(self.session_dir, self._ask_record("2"))
        self.assertEqual(leader_notifier.clear_task_records(self.session_dir, "1"), 2)
        self.assertEqual(
            [r["task_id"] for r in leader_notifier.read_queue(self.session_dir)], ["2"]
        )

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
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=READY_PANE) as fake,
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
        self.assertEqual(len(fake.calls_named("send_text")), 1)
        injected = fake.calls_named("send_text")[-1][0][2]
        self.assertIn("https://github.com/o/r/pull/77", injected)
        self.assertNotIn("(none yet)", injected)

    def test_flush_still_renders_none_yet_when_pr_absent(self) -> None:
        self._seed_task("1")  # PR never appears
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=READY_PANE) as fake,
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
        self.assertEqual(len(fake.calls_named("send_text")), 1)
        self.assertIn("PR=(none yet)", fake.calls_named("send_text")[-1][0][2])

    # -- queue eviction on retirement (stale-pending guard) ----------------

    def test_clear_task_records_removes_all_for_one_task(self) -> None:
        # A multi_stage task can leave several records; retirement evicts them all
        # at once (matched by task_id), leaving unrelated tasks untouched.
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        leader_notifier.enqueue(self.session_dir, self._record("2"))
        removed = leader_notifier.clear_task_records(self.session_dir, "1")
        self.assertEqual(removed, 2)
        remaining = leader_notifier.read_queue(self.session_dir)
        self.assertEqual([r["task_id"] for r in remaining], ["2"])

    def test_clear_task_records_noop_when_queue_absent(self) -> None:
        # No queue yet → no-op, returns 0, creates no empty queue file.
        removed = leader_notifier.clear_task_records(self.session_dir, "1")
        self.assertEqual(removed, 0)
        self.assertFalse(leader_notifier.queue_path(self.session_dir).exists())

    # -- inject-only-on-ready ---------------------------------------------

    def test_busy_leader_rearms_and_keeps_queued(self) -> None:
        # A leader busy for the notifier's whole lifetime must not strand the
        # queue: we never inject mid-turn, but we hand off to a successor so the
        # next idle boundary is still caught.
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=BUSY_PANE) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
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
        self.assertEqual(fake.calls_named("send_text"), [])  # never mid-turn
        rearm.assert_called_once()  # handed off to a successor
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)  # still queued

    def test_busy_claude_with_visible_composer_is_never_injected_into(self) -> None:
        # Issue #288: claude keeps ``❯`` on screen mid-turn, so ``is_ready`` alone
        # let the notifier type into a working leader. A real busy capture must hold
        # it back until the spinner is gone.
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=pane_fx.CLAUDE_BUSY) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.05,
                poll_interval=0.001,
            )
        self.assertEqual(fake.sent(), [])
        rearm.assert_called_once()
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)

    def test_real_busy_then_idle_injects_once_at_the_boundary(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        panes = [pane_fx.CLAUDE_BUSY, pane_fx.CLAUDE_BUSY_ESC_HINT, pane_fx.CLAUDE_IDLE]
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=panes) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=5.0,
                poll_interval=0.001,
            )
        self.assertEqual([kind for kind, _w, _p in fake.sent()].count("text"), 1)
        # Both busy screens were captured (and skipped) before the idle one + its
        # confirming capture, i.e. nothing was typed until the boundary.
        names = fake.method_names()
        self.assertGreaterEqual(names[:names.index("send_text")].count("capture"), 4)
        rearm.assert_not_called()
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])

    # -- Issue #329: never type on top of a human's unsent draft -------------

    def _notify_with_pane(self, *, capture, timeout: float = 5.0):
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=capture) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=timeout,
                poll_interval=0.001,
            )
        return fake, rearm

    def test_leader_with_an_unsent_draft_is_never_injected_into(self) -> None:
        # The leader's composer held "1. teams." (the user mid-reply); the notifier
        # typed its block after it and the leader got a merged, corrupted message.
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        fake, rearm = self._notify_with_pane(capture=pane_fx.claude_draft("1. teams."), timeout=0.05)
        self.assertEqual(fake.sent(), [])  # nothing typed, no Enter either
        rearm.assert_called_once()  # a draft that outlives the poller is handled like busy
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)  # kept, not dropped
        log = leader_notifier.log_path(self.session_dir).read_text(encoding="utf-8")
        self.assertIn("unsent draft", log)

    def test_draft_then_sent_injects_once_when_the_composer_is_empty(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        panes = [pane_fx.claude_draft("1. teams."), pane_fx.claude_draft("1. teams. and more"), pane_fx.CLAUDE_IDLE]
        fake, rearm = self._notify_with_pane(capture=panes)
        self.assertEqual([kind for kind, _w, _p in fake.sent()].count("text"), 1)
        names = fake.method_names()
        self.assertGreaterEqual(names[:names.index("send_text")].count("capture"), 4)  # 2 drafts skipped
        rearm.assert_not_called()
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])

    def test_draft_started_after_the_first_capture_stops_the_injection(self) -> None:
        # Idle on the first capture; the user starts typing before the confirming
        # capture (taken right before the keystrokes) — nothing is typed that round.
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        panes = [pane_fx.CLAUDE_IDLE, pane_fx.claude_draft("hm"), pane_fx.CLAUDE_IDLE]
        fake, _rearm = self._notify_with_pane(capture=panes)
        names = fake.method_names()
        self.assertEqual(names.count("send_text"), 1)
        self.assertGreaterEqual(names[:names.index("send_text")].count("capture"), 3)
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])

    def test_fresh_session_placeholder_does_not_block_the_flush(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        fake, rearm = self._notify_with_pane(capture=READY_PANE)  # ``❯ Try "help"``: an empty composer
        self.assertEqual([kind for kind, _w, _p in fake.sent()].count("text"), 1)
        rearm.assert_not_called()

    def test_idle_only_on_the_first_capture_is_not_enough(self) -> None:
        # A gap between two tool calls looks idle for one capture. The confirming
        # capture (taken right before the keystrokes) sees the next spinner, so
        # nothing is typed that round; the flush happens once idle holds.
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        panes = [pane_fx.CLAUDE_IDLE, pane_fx.CLAUDE_BUSY, pane_fx.CLAUDE_IDLE]
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=panes) as fake,
            patch("fleet.leader_notifier.start_detached"),
        ):
            leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=5.0,
                poll_interval=0.001,
            )
        sent = fake.method_names()
        self.assertEqual(sent.count("send_text"), 1)
        # capture(idle) → capture(busy: confirm fails) → capture(idle) → capture(idle) → send
        first_send = sent.index("send_text")
        self.assertGreaterEqual(sent[:first_send].count("capture"), 3)
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])

    def test_confirming_capture_precedes_the_keystrokes(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=pane_fx.CLAUDE_IDLE) as fake:
            leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.5,
                poll_interval=0.001,
            )
        names = fake.method_names()
        send = names.index("send_text")
        self.assertEqual(names[send - 1], "capture")  # confirm, immediately before typing
        self.assertGreaterEqual(names[:send].count("capture"), 2)

    def _run_with_stuck_composer(self, *, swallow_enters: int):
        """Inject into an idle leader whose next ``swallow_enters`` Enters do nothing.

        The typed text stays in the composer (stuck pane) until an Enter that is
        not swallowed clears it — the Issue #288 "unsent in the composer" case.
        """
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        state = {"typed": None, "enters_left": swallow_enters}
        with use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=pane_fx.CLAUDE_IDLE) as fake:
            def on_send_text(_session, _window, text, *, enter=True):
                state["typed"] = text
                if state["enters_left"] > 0:
                    state["enters_left"] -= 1
                    fake._capture = pane_fx.claude_stuck_composer(text)

            def on_send_key(_session, _window, key):
                if state["typed"] is None:
                    return
                if state["enters_left"] > 0:
                    state["enters_left"] -= 1
                else:
                    fake._capture = pane_fx.claude_submitted_echo(state["typed"])

            fake.on["send_text"] = on_send_text
            fake.on["send_key"] = on_send_key
            with patch("fleet.leader_notifier.start_detached"):
                leader_notifier.notify(
                    session_dir=self.session_dir,
                    session="fleet-main",
                    window="leader",
                    agent_spec="claude:opus",
                    timeout=5.0,
                    poll_interval=0.001,
                )
        return fake

    def test_unsubmitted_text_gets_one_more_enter(self) -> None:
        fake = self._run_with_stuck_composer(swallow_enters=1)  # the submit Enter is lost
        self.assertEqual(len(fake.calls_named("send_text")), 1)  # typed exactly once
        self.assertEqual(len(fake.calls_named("send_key")), 1)
        self.assertEqual(fake.calls_named("send_key")[0][0][2], "Enter")
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])
        last = self._events()[-1]
        self.assertEqual(last["type"], "leader_notified")
        self.assertTrue(last["submit_confirmed"])
        self.assertEqual(last["enter_retries"], 1)

    def test_enter_retry_is_bounded_and_never_retypes(self) -> None:
        fake = self._run_with_stuck_composer(swallow_enters=99)  # composer never clears
        self.assertEqual(len(fake.calls_named("send_text")), 1)
        self.assertEqual(len(fake.calls_named("send_key")), leader_notifier.SUBMIT_ENTER_RETRIES)
        # Queue is cleared (the text IS typed; re-injecting would append a duplicate)
        # and the event records that the submit could not be confirmed.
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])
        last = self._events()[-1]
        self.assertFalse(last["submit_confirmed"])
        self.assertEqual(last["enter_retries"], leader_notifier.SUBMIT_ENTER_RETRIES)

    def test_submitted_text_needs_no_extra_enter(self) -> None:
        fake = self._run_with_stuck_composer(swallow_enters=0)
        self.assertEqual(len(fake.calls_named("send_text")), 1)
        self.assertEqual(fake.calls_named("send_key"), [])
        last = self._events()[-1]
        self.assertTrue(last["submit_confirmed"])
        self.assertEqual(last["enter_retries"], 0)

    def test_dialog_pane_is_not_injected_into(self) -> None:
        # An un-numbered selection menu's cursor line (``  ❯ No, …``) matches
        # the bare ready regex; the notifier must not type into the dialog.
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        dialog = (
            "  ❯ No, keep browser tools off\n"
            "    Yes, use my browser\n\n"
            "  Enter to confirm · Esc to keep browser tools off\n"
        )
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=dialog) as fake,
            patch("fleet.leader_notifier.start_detached"),
        ):
            leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.05,
                poll_interval=0.001,
            )
        self.assertEqual(fake.calls_named("send_text"), [])
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)

    def test_busy_then_idle_eventually_injects(self) -> None:
        # The strand bug fixed: a leader busy at first still gets the queue once
        # it goes idle. Within one process, polling rides through busy turns and
        # flushes on the first idle boundary — no successor needed.
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        panes = [BUSY_PANE, BUSY_PANE, READY_PANE]
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=panes) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            rc = leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=5.0,
                poll_interval=0.001,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.calls_named("send_text")), 1)  # flushed on the idle boundary
        rearm.assert_not_called()  # delivered → no successor
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])

    def test_idle_leader_injects_once_coalesced_and_clears(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        leader_notifier.enqueue(self.session_dir, self._record("2"))
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=READY_PANE) as fake,
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
        self.assertEqual(len(fake.calls_named("send_text")), 1)
        args, kwargs = fake.calls_named("send_text")[-1]
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
            use_fake_mux(sessions={}) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
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
        self.assertEqual(fake.calls_named("send_text"), [])
        rearm.assert_not_called()  # dead session needs no successor
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)

    def test_empty_queue_is_noop(self) -> None:
        with use_fake_mux() as fake:
            rc = leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.05,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls, [])  # bailed before touching the mux

    def test_second_notifier_noops_while_lock_held(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        fp = leader_notifier._acquire_lock(self.session_dir)
        self.assertIsNotNone(fp)
        try:
            with (
                use_fake_mux() as fake,
            ):
                rc = leader_notifier.notify(
                    session_dir=self.session_dir,
                    session="fleet-main",
                    window="leader",
                    agent_spec="claude:opus",
                    timeout=0.05,
                )
            self.assertEqual(rc, 0)
            self.assertEqual(fake.calls, [])  # lock held → immediate no-op
        finally:
            leader_notifier._release_lock(fp)
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)
        self.assertIn("lock held by another notifier", self._log())

    # -- transient mux errors (Issue #292) ---------------------------------

    def _log(self) -> str:
        path = leader_notifier.log_path(self.session_dir)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _notify(self, *, timeout: float = 5.0) -> int:
        return leader_notifier.notify(
            session_dir=self.session_dir,
            session="fleet-main",
            window="leader",
            agent_spec="claude:opus",
            timeout=timeout,
            poll_interval=0.001,
        )

    def test_transient_capture_error_does_not_strand_the_queue(self) -> None:
        # #292: one "tab not found" from capture used to end the poller with the
        # record left queued. It must keep polling and flush at the next idle.
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=READY_PANE) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            fake.fail_next("capture", mux.MuxError("zellij tab not found"), times=3)
            rc = self._notify()
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.calls_named("send_text")), 1)
        rearm.assert_not_called()
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])
        log = self._log()
        self.assertEqual(log.count("capture failed (transient"), 1)  # low volume: once, not per poll
        self.assertIn("zellij tab not found", log)
        self.assertIn("flushed 1 record(s)", log)

    def test_persistent_capture_error_rearms_like_the_busy_case(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=READY_PANE) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            fake.fail["capture"] = mux.MuxError("zellij tab not found")
            rc = self._notify(timeout=0.05)
        self.assertEqual(rc, 0)
        self.assertGreater(len(fake.calls_named("capture")), 1)  # kept polling
        self.assertEqual(fake.calls_named("send_text"), [])
        rearm.assert_called_once()  # handed to a successor, not abandoned
        self.assertEqual(rearm.call_args.kwargs["reason"], "re-arm")
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)
        self.assertIn("re-arming a successor", self._log())

    def test_single_false_session_exists_is_transient(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        checks = {"n": 0}
        with (
            use_fake_mux(capture=READY_PANE) as fake,  # session not listed at first
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):

            def session_back_on_second_check(*_a, **_k) -> None:
                checks["n"] += 1
                if checks["n"] >= 2:
                    fake.sessions["fleet-main"] = ["leader"]

            fake.on["session_exists"] = session_back_on_second_check
            rc = self._notify()
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.calls_named("send_text")), 1)  # did not give up on the first False
        rearm.assert_not_called()
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])
        self.assertIn("was False once but is back", self._log())

    def test_session_confirmed_gone_gives_up_without_rearm(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(sessions={}) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            rc = self._notify(timeout=0.05)
        self.assertEqual(rc, 0)
        rearm.assert_not_called()
        # confirmed by SESSION_RECHECKS consecutive checks, not by the first False
        self.assertEqual(
            len(fake.calls_named("session_exists")), leader_notifier.SESSION_RECHECKS + 1
        )
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)
        self.assertIn("confirmed gone", self._log())

    def test_session_gone_at_the_deadline_is_not_rearmed(self) -> None:
        # The leader went away while busy: no successor, the record stays queued.
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=BUSY_PANE) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            calls = {"n": 0}

            def vanish_after_first_poll(*_a, **_k) -> None:
                calls["n"] += 1
                if calls["n"] >= 2:
                    fake.sessions.pop("fleet-main", None)

            fake.on["session_exists"] = vanish_after_first_poll
            self._notify(timeout=0.05)
        rearm.assert_not_called()
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)

    def test_confirming_capture_error_is_transient_and_types_nothing(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=READY_PANE) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            # 1st capture (poll) fine, 2nd (the confirming one) fails, then all fine.
            calls = {"n": 0}

            def fail_confirming_capture(*_a, **_k) -> None:
                calls["n"] += 1
                if calls["n"] == 2:
                    raise mux.MuxError("zellij tab not found")

            fake.on["capture"] = fail_confirming_capture
            rc = self._notify()
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.calls_named("send_text")), 1)  # exactly one injection
        # ...and it happened after the failed confirming capture, never before.
        names = fake.method_names()
        self.assertGreaterEqual(names[: names.index("send_text")].count("capture"), 3)
        rearm.assert_not_called()
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])
        self.assertIn("confirming capture failed", self._log())

    def test_send_failure_still_leaves_the_record_queued(self) -> None:
        # #290 behaviour kept: a failed send may have half typed the text, so it
        # is not retried blindly (that could double-inject).
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=READY_PANE) as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            fake.fail["send_text"] = mux.MuxError("boom")
            rc = self._notify()
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.calls_named("send_text")), 1)
        rearm.assert_not_called()
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)
        self.assertIn("send_text failed on the mux", self._log())

    # -- renamed / recreated leader window (Issue #302) ----------------------

    LEADER_TITLE = "✳ main-leader"  # claude decorates the --name it was launched with
    DRIVER_WINDOW = "7·implementer"

    def _renamed_leader_fake(self):
        """A session whose leader tab lost its name (zellij's default ``Tab #3``)."""
        return use_fake_mux(
            sessions={"fleet-main": ["Tab #3", self.DRIVER_WINDOW]},
            capture=READY_PANE,
            strict_windows=True,
        )

    def test_renamed_leader_window_is_found_by_pane_title_and_renamed_back(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            self._renamed_leader_fake() as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            fake.titles[("fleet-main", "Tab #3")] = self.LEADER_TITLE
            fake.titles[("fleet-main", self.DRIVER_WINDOW)] = "main-leader-implementer"
            rc = self._notify()
        self.assertEqual(rc, 0)
        self.assertEqual(fake.calls_named("rename_window"), [(("fleet-main", "@0", "leader"), {})])
        self.assertEqual(fake.sessions["fleet-main"], ["leader", self.DRIVER_WINDOW])
        sends = fake.calls_named("send_text")
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0][0][:2], ("fleet-main", "leader"))  # typed into the healed window
        rearm.assert_not_called()
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])
        log = self._log()
        self.assertIn("leader window 'leader' was missing", log)
        self.assertIn("'Tab #3'", log)
        self.assertEqual(log.count("renamed it back to 'leader'"), 1)

    def test_present_leader_window_is_never_looked_up_by_title(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with use_fake_mux(
            sessions={"fleet-main": ["leader"]}, capture=READY_PANE, strict_windows=True
        ) as fake:
            fake.fail_next("capture", mux.MuxError("boom"), times=2)  # an unrelated glitch
            self._notify()
        self.assertEqual(fake.calls_named("list_panes"), [])
        self.assertEqual(fake.calls_named("rename_window"), [])
        self.assertEqual(len(fake.calls_named("send_text")), 1)

    def test_no_pane_with_the_leader_title_renames_nothing_and_logs_once(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            self._renamed_leader_fake() as fake,
            patch("fleet.leader_notifier.start_detached") as rearm,
        ):
            fake.titles[("fleet-main", "Tab #3")] = "just a shell"
            self._notify(timeout=0.05)
        self.assertEqual(fake.calls_named("rename_window"), [])
        self.assertEqual(fake.calls_named("send_text"), [])
        rearm.assert_called_once()  # still re-armed: the user may fix it by hand
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)
        log = self._log()
        self.assertEqual(log.count("no pane is titled 'main-leader'"), 1)  # not once per poll
        self.assertIn("capture failed (transient", log)

    def test_a_driver_pane_is_never_adopted_as_the_leader(self) -> None:
        # Driver windows are named <task>·<role> and their agents <project>-<task>-<role>:
        # even a project called "main" with a task called "leader" is not the leader.
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(
                sessions={"fleet-main": [self.DRIVER_WINDOW, "8"]},
                capture=READY_PANE,
                strict_windows=True,
            ) as fake,
            patch("fleet.leader_notifier.start_detached"),
        ):
            fake.titles[("fleet-main", self.DRIVER_WINDOW)] = "main-leader-implementer"
            fake.titles[("fleet-main", "8")] = "domain-leader"
            self._notify(timeout=0.05)
        self.assertEqual(fake.calls_named("rename_window"), [])
        self.assertEqual(fake.calls_named("send_text"), [])
        self.assertEqual(fake.sessions["fleet-main"], [self.DRIVER_WINDOW, "8"])

    def test_two_windows_with_the_leader_title_are_left_alone(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(
                sessions={"fleet-main": ["Tab #3", "Tab #4"]},
                capture=READY_PANE,
                strict_windows=True,
            ) as fake,
            patch("fleet.leader_notifier.start_detached"),
        ):
            fake.titles[("fleet-main", "Tab #3")] = "main-leader"
            fake.titles[("fleet-main", "Tab #4")] = "main-leader"
            self._notify(timeout=0.05)
        self.assertEqual(fake.calls_named("rename_window"), [])
        self.assertIn("2 windows hold a pane titled 'main-leader'", self._log())

    def test_heal_never_touches_a_session_that_is_not_a_fleet_session(self) -> None:
        with use_fake_mux(sessions={"work": ["Tab #1"]}, strict_windows=True) as fake:
            fake.titles[("work", "Tab #1")] = "main-leader"
            healed, note = leader_notifier.heal_leader_window(
                fake, "work", "leader", self.session_dir
            )
        self.assertEqual((healed, note), (False, None))
        self.assertEqual(fake.calls, [])  # not even a list_windows

    def test_heal_swallows_mux_errors(self) -> None:
        with use_fake_mux(sessions={"fleet-main": ["Tab #3"]}) as fake:
            fake.titles[("fleet-main", "Tab #3")] = "main-leader"
            fake.fail["rename_window"] = mux.MuxError("rename refused")
            healed, note = leader_notifier.heal_leader_window(
                fake, "fleet-main", "leader", self.session_dir
            )
            self.assertFalse(healed)
            self.assertIn("rename refused", note)
            del fake.fail["rename_window"]
            fake.fail["list_windows"] = mux.MuxError("session gone")
            healed, note = leader_notifier.heal_leader_window(
                fake, "fleet-main", "leader", self.session_dir
            )
        self.assertFalse(healed)
        self.assertIn("session gone", note)

    def test_heal_uses_the_session_label_for_the_title(self) -> None:
        with use_fake_mux(sessions={"fleet-hotfix": ["Tab #2"]}) as fake:
            fake.titles[("fleet-hotfix", "Tab #2")] = "main-leader"  # another session's leader
            healed, _note = leader_notifier.heal_leader_window(
                fake, "fleet-hotfix", "leader", self.session_dir
            )
            self.assertFalse(healed)
            fake.titles[("fleet-hotfix", "Tab #2")] = "hotfix-leader - Claude"
            healed, _note = leader_notifier.heal_leader_window(
                fake, "fleet-hotfix", "leader", self.session_dir
            )
        self.assertTrue(healed)
        self.assertEqual(fake.sessions["fleet-hotfix"], ["leader"])

    def test_title_names_agent_matches_whole_tokens_only(self) -> None:
        match = leader_notifier.title_names_agent
        for title in ("main-leader", "✳ main-leader", "Main-Leader", "main-leader - Claude", "⠂ main-leader"):
            self.assertTrue(match(title, "main-leader"), title)
        for title in (
            "",
            "claude",
            "main-leader-implementer",  # a driver named <project>-<task>-<role>
            "domain-leader",
            "x-main-leader",
            "main-leaders",
            "main leader",
        ):
            self.assertFalse(match(title, "main-leader"), title)
        self.assertTrue(match("a.b-leader", "a.b-leader"))
        self.assertFalse(match("aXb-leader", "a.b-leader"))  # the label is escaped, not a regex

    # -- pending summary (Issue #302 item 2) -----------------------------------

    def test_pending_summary_is_none_for_an_empty_or_absent_queue(self) -> None:
        self.assertIsNone(leader_notifier.pending_summary(self.session_dir))
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        leader_notifier.clear_task_records(self.session_dir, "1")
        self.assertIsNone(leader_notifier.pending_summary(self.session_dir))

    def test_pending_summary_counts_and_ages_the_oldest_record(self) -> None:
        from datetime import datetime, timedelta, timezone

        def ts(ago: timedelta) -> str:
            return (datetime.now(timezone.utc) - ago).strftime("%Y-%m-%dT%H:%M:%SZ")

        for task_id, ago in (
            ("1", timedelta(minutes=5)),
            ("2", timedelta(hours=3)),
            ("3", timedelta(seconds=9)),
        ):
            leader_notifier.enqueue(self.session_dir, dict(self._record(task_id), ts=ts(ago)))
        summary = leader_notifier.pending_summary(self.session_dir)
        self.assertEqual(summary["count"], 3)
        self.assertAlmostEqual(summary["oldest_age_seconds"], 3 * 3600, delta=5)
        self.assertEqual(
            leader_notifier.describe_pending(summary),
            "3 leader notifications pending (oldest 3h ago)",
        )

    def test_describe_pending_singular_and_missing_timestamp(self) -> None:
        leader_notifier.enqueue(self.session_dir, dict(self._record("1"), ts="not a timestamp"))
        summary = leader_notifier.pending_summary(self.session_dir)
        self.assertEqual(summary["count"], 1)
        self.assertIsNone(summary["oldest_age_seconds"])
        self.assertEqual(leader_notifier.describe_pending(summary), "1 leader notification pending")
        self.assertEqual(
            leader_notifier.describe_pending({"count": 2, "oldest_age_seconds": 125.0}),
            "2 leader notifications pending (oldest 2m ago)",
        )

    # -- leader-notifier.log ------------------------------------------------

    def test_log_records_start_busy_wait_and_deadline_rearm(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=BUSY_PANE),
            patch("fleet.leader_notifier.start_detached"),
        ):
            self._notify(timeout=0.05)
        log = self._log()
        self.assertIn("started: session=fleet-main window=leader agent=claude:opus timeout=0.05s", log)
        self.assertEqual(log.count("leader not idle"), 1)  # once, not once per poll
        self.assertIn("deadline 0.05s reached: 1 record(s) pending; re-arming a successor", log)
        self.assertRegex(log.splitlines()[0], r"^\d{4}-\d\d-\d\dT[\d:]+Z? \[pid \d+\] started")

    def test_log_records_flush_and_empty_queue_exit(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._record("1"))
        leader_notifier.enqueue(self.session_dir, self._record("2"))
        with use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=READY_PANE):
            self._notify()
        log = self._log()
        self.assertIn("flushed 2 record(s) ['1', '2']: submit_confirmed=", log)
        self.assertIn("exit: queue empty", log)

    def test_start_detached_logs_spawn_pid_and_timeout(self) -> None:
        proc = type("Proc", (), {"pid": 4242})()
        with patch("fleet.leader_notifier.spawn_detached", return_value=proc) as spawn:
            path = leader_notifier.start_detached(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=30.0,
                reason="re-arm",
            )
        self.assertEqual(path, leader_notifier.log_path(self.session_dir))
        self.assertEqual(spawn.call_args.kwargs["log_path"], path)
        self.assertIn("spawn (re-arm): notifier pid=4242 session=fleet-main timeout=30s", self._log())

    # -- delivery-failed records (Issue #289) -------------------------------

    def _failed_record(self, task_id: str, **over) -> dict:
        return self._record(
            task_id,
            status="failed",
            kind=leader_notifier.KIND_DELIVERY_FAILED,
            summary="prompt deliverer cannot paste prompt: zellij tab not found",
            project="demo",
            **over,
        )

    def test_delivery_failed_record_skips_pr_lookup_and_names_send_prompt(self) -> None:
        self._seed_task("5", pr_url="https://github.com/o/r/pull/1")
        rec = self._failed_record("5")
        self.assertEqual(rec["kind"], "delivery_failed")
        self.assertIsNone(rec["pr_url"])  # not scanned even though the outbox has one
        text = leader_notifier.render_block([rec])
        self.assertNotIn("\n", text)
        self.assertIn("task-5 [delivery failed] prompt deliverer cannot paste prompt", text)
        self.assertIn("retry with: fleet-agent send-prompt 5 --project demo", text)
        self.assertNotIn("pull the diff", text)  # not a gate

    def test_delivery_failed_never_triggers_a_pr_refill(self) -> None:
        rec = self._failed_record("5")
        with patch.object(leader_notifier, "scan_pr_url") as scan:
            leader_notifier._refill_pr_urls([rec])
        scan.assert_not_called()

    def test_mixed_batch_keeps_each_instruction(self) -> None:
        text = leader_notifier.render_block(
            [self._record("1"), self._failed_record("2")]
        )
        self.assertIn("NOT marked [ask] or [delivery failed]: pull the diff", text)
        self.assertIn("[delivery failed] entry:", text)
        self.assertNotIn("[ask] entry", text)
        text = leader_notifier.render_block(
            [self._record("1"), self._failed_record("2"), self._record("3", kind="ask", question="q?")]
        )
        self.assertIn("For each [ask] entry:", text)
        self.assertIn("For each [delivery failed] entry:", text)

    def test_delivery_failed_record_is_flushed_to_the_leader(self) -> None:
        leader_notifier.enqueue(self.session_dir, self._failed_record("2"))
        with use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=READY_PANE) as fake:
            self._notify()
        (kind, _window, payload) = [e for e in fake.sent() if e[0] == "text"][0]
        self.assertIn("fleet-agent send-prompt 2 --project demo", payload)
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])


class PrUrlLookupTests(unittest.TestCase):
    """``scan_pr_url``: full URL, then ``PR #<n>`` + origin remote, then ``gh`` by branch."""

    BRANCH = "fleet/task/7"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        self.state_dir = root / "state"
        state.init_state(self.state_dir, name="demo", repo=self.repo.resolve())
        state.save_task(self.state_dir, "7", {"id": "7", "branch": self.BRANCH})
        leader_notifier._ORIGIN_CACHE.clear()
        self.addCleanup(leader_notifier._ORIGIN_CACHE.clear)
        self.calls: list[tuple[list[str], str | None]] = []
        self.remote = "https://github.com/o/r.git\n"
        self.gh_out: str | None = json.dumps([{"url": "https://github.com/o/r/pull/9"}])
        self.gh_path: str | None = "/usr/bin/gh"
        for patcher in (
            patch.object(leader_notifier, "_run_capture", side_effect=self._fake_run),
            patch("fleet.leader_notifier.shutil.which", side_effect=lambda _n: self.gh_path),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fake_run(self, argv, cwd):
        self.calls.append((list(argv), str(cwd) if cwd else None))
        return self.remote if argv[0] == "git" else self.gh_out

    def _outbox(self, text: str) -> None:
        tdir = state.task_dir(self.state_dir, "7")
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "outbox.md").write_text(text, encoding="utf-8")

    def _gh_calls(self) -> list[list[str]]:
        return [a for a, _ in self.calls if a[0] != "git"]

    # -- 1. full URL -------------------------------------------------------

    def test_full_url_wins_without_any_subprocess(self) -> None:
        self._outbox("PR #5 opened\nhttps://github.com/o/r/pull/12\nlater: PR #99\n")
        self.assertEqual(
            leader_notifier.scan_pr_url(self.state_dir, "7", use_gh=True),
            "https://github.com/o/r/pull/12",
        )
        self.assertEqual(self.calls, [])

    # -- 2. PR #<n> + origin ----------------------------------------------

    def test_pr_number_is_expanded_with_origin_remote(self) -> None:
        self._outbox("## done\nPR #280 opened, CI green\n")
        self.assertEqual(
            leader_notifier.scan_pr_url(self.state_dir, "7"),
            "https://github.com/o/r/pull/280",
        )
        self.assertEqual(self.calls, [(["git", "remote", "get-url", "origin"], str(self.repo.resolve()))])

    def test_pr_number_uses_last_mention_and_pull_request_wording(self) -> None:
        self._outbox("PR #5 superseded by Pull Request #6\n")
        self.assertEqual(
            leader_notifier.scan_pr_url(self.state_dir, "7"), "https://github.com/o/r/pull/6"
        )

    def test_origin_remote_forms(self) -> None:
        cases = [
            "https://github.com/o/r.git",
            "https://github.com/o/r",
            "https://user@github.com/o/r.git",
            "git@github.com:o/r.git",
            "ssh://git@github.com/o/r.git",
            "ssh://git@github.com:22/o/r",
        ]
        self._outbox("PR #3\n")
        for remote in cases:
            with self.subTest(remote=remote):
                leader_notifier._ORIGIN_CACHE.clear()
                self.remote = remote + "\n"
                self.assertEqual(
                    leader_notifier.scan_pr_url(self.state_dir, "7"),
                    "https://github.com/o/r/pull/3",
                )

    def test_non_github_or_missing_origin_yields_none(self) -> None:
        self._outbox("PR #3\n")
        for remote in ("git@gitlab.com:o/r.git\n", ""):
            with self.subTest(remote=remote):
                leader_notifier._ORIGIN_CACHE.clear()
                self.remote = remote
                self.assertIsNone(leader_notifier.scan_pr_url(self.state_dir, "7"))

    def test_origin_lookup_is_cached_per_directory(self) -> None:
        self._outbox("PR #3\n")
        leader_notifier.scan_pr_url(self.state_dir, "7")
        leader_notifier.scan_pr_url(self.state_dir, "7")
        self.assertEqual(len(self.calls), 1)

    def test_git_failure_yields_none(self) -> None:
        self._outbox("PR #3\n")
        with patch.object(leader_notifier, "_run_capture", return_value=None):
            self.assertIsNone(leader_notifier.scan_pr_url(self.state_dir, "7"))

    # -- 3. gh by branch ---------------------------------------------------

    def test_gh_fallback_looks_up_by_branch(self) -> None:
        self._outbox("finished, no PR mentioned\n")
        self.assertEqual(
            leader_notifier.scan_pr_url(self.state_dir, "7", use_gh=True),
            "https://github.com/o/r/pull/9",
        )
        self.assertEqual(
            self._gh_calls(),
            [["/usr/bin/gh", "pr", "list", "--head", self.BRANCH, "--state", "all",
              "--json", "url", "--limit", "1"]],
        )
        self.assertEqual(self.calls[-1][1], str(self.repo.resolve()))

    def test_gh_fallback_works_without_outbox_and_prefers_explicit_branch(self) -> None:
        self.assertEqual(
            leader_notifier.scan_pr_url(self.state_dir, "7", branch="other/br", use_gh=True),
            "https://github.com/o/r/pull/9",
        )
        self.assertIn("other/br", self._gh_calls()[0])

    def test_gh_is_off_by_default(self) -> None:
        self._outbox("nothing here\n")
        self.assertIsNone(leader_notifier.scan_pr_url(self.state_dir, "7"))
        self.assertEqual(self._gh_calls(), [])

    def test_gh_not_on_path_is_skipped(self) -> None:
        self.gh_path = None
        self.assertIsNone(leader_notifier.scan_pr_url(self.state_dir, "7", use_gh=True))
        self.assertEqual(self.calls, [])

    def test_gh_bad_output_yields_none(self) -> None:
        for out in (None, "", "not json", "[]", "{}", '[{"number": 1}]', '[{"url": 5}]'):
            with self.subTest(out=out):
                self.gh_out = out
                self.assertIsNone(leader_notifier.scan_pr_url(self.state_dir, "7", use_gh=True))

    def test_outbox_pr_number_is_preferred_over_gh(self) -> None:
        self._outbox("PR #280\n")
        self.assertEqual(
            leader_notifier.scan_pr_url(self.state_dir, "7", use_gh=True),
            "https://github.com/o/r/pull/280",
        )
        self.assertEqual(self._gh_calls(), [])

    def test_gh_used_when_origin_unresolvable(self) -> None:
        self._outbox("PR #280\n")
        self.remote = ""
        self.assertEqual(
            leader_notifier.scan_pr_url(self.state_dir, "7", use_gh=True),
            "https://github.com/o/r/pull/9",
        )

    # -- never raises / never blocks done ---------------------------------

    def test_scan_never_raises(self) -> None:
        self._outbox("PR #3\n")
        with patch.object(leader_notifier, "_run_capture", side_effect=RuntimeError("boom")):
            self.assertIsNone(leader_notifier.scan_pr_url(self.state_dir, "7", use_gh=True))

    def test_run_capture_swallows_timeouts_and_missing_binaries(self) -> None:
        for exc in (
            subprocess.TimeoutExpired(cmd="gh", timeout=1),
            FileNotFoundError("gh"),
            OSError("nope"),
        ):
            with self.subTest(exc=type(exc).__name__):
                with patch("fleet.leader_notifier.subprocess.run", side_effect=exc):
                    self.assertIsNone(_REAL_RUN_CAPTURE(["gh", "x"], None))

    def test_run_capture_returns_stdout_only_on_success(self) -> None:
        ok = subprocess.CompletedProcess(["x"], 0, stdout="out\n", stderr="")
        bad = subprocess.CompletedProcess(["x"], 1, stdout="out\n", stderr="err")
        with patch("fleet.leader_notifier.subprocess.run", return_value=ok) as run:
            self.assertEqual(_REAL_RUN_CAPTURE(["x"], self.repo), "out\n")
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs["timeout"], leader_notifier.LOOKUP_TIMEOUT_SECONDS)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["cwd"], str(self.repo))
            self.assertNotIn("shell", kwargs)
        with patch("fleet.leader_notifier.subprocess.run", return_value=bad):
            self.assertIsNone(_REAL_RUN_CAPTURE(["x"], None))

    # -- call sites --------------------------------------------------------

    def test_build_record_resolves_pr_number_but_never_calls_gh(self) -> None:
        self._outbox("PR #280\n")
        rec = leader_notifier.build_record(
            state_dir=self.state_dir, task_id="7", status="completed",
            branch=self.BRANCH, worktree=None, summary="done",
        )
        self.assertEqual(rec["pr_url"], "https://github.com/o/r/pull/280")
        self.assertEqual(self._gh_calls(), [])

    def test_build_record_does_not_call_gh_when_nothing_found(self) -> None:
        rec = leader_notifier.build_record(
            state_dir=self.state_dir, task_id="7", status="completed",
            branch=self.BRANCH, worktree=None, summary="done",
        )
        self.assertIsNone(rec["pr_url"])
        self.assertEqual(self._gh_calls(), [])

    def test_refill_falls_back_to_gh_by_record_branch(self) -> None:
        rec = {"task_id": "7", "state_dir": str(self.state_dir), "branch": "rec/branch",
               "pr_url": None}
        leader_notifier._refill_pr_urls([rec])
        self.assertEqual(rec["pr_url"], "https://github.com/o/r/pull/9")
        self.assertIn("rec/branch", self._gh_calls()[0])

    def test_refill_resolves_pr_number_written_after_done(self) -> None:
        rec = {"task_id": "7", "state_dir": str(self.state_dir), "branch": self.BRANCH,
               "pr_url": None}
        self._outbox("PR #280\n")
        leader_notifier._refill_pr_urls([rec])
        self.assertEqual(rec["pr_url"], "https://github.com/o/r/pull/280")


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

    def test_intermediate_handoff_does_not_enqueue(self) -> None:
        """An intermediate stage transition (status=running) must not notify the leader.

        implementer→code-reviewer and code-reviewer→implementer handoffs are
        internal driver-to-driver events; the leader has nothing to act on.
        Only completed and awaiting_orders reach the leader.
        """
        project = {"name": "demo", "notify_leader_on_driver_done": "true"}
        with patch("fleet.leader_notifier.start_detached") as spawn:
            done_cmd._maybe_notify_leader(
                self.state_dir, self.task_id, self.task, project, "demo",
                status="running", result="approved",
                summary="task-1 stage 1 done → next stage (code-reviewer) starting",
            )
        spawn.assert_not_called()
        self.assertFalse(leader_notifier.queue_path(self.session_dir).exists())

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
            use_fake_mux(sessions={"fleet-main": ["leader"], "fleet-migration": ["leader"]}),
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
            use_fake_mux(sessions={"fleet-main": ["leader"], "fleet-migration": ["leader"]}),
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
            use_fake_mux(sessions={"fleet-main": ["leader"], "fleet-migration": ["leader"]}),
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


class AskHookTests(unittest.TestCase):
    """ask.py wiring (issue #287): with ``notify_leader_on_driver_done`` on, a driver's
    ``fleet-agent ask`` also enqueues a kind=ask record into the owner session's
    queue and spawns the detached notifier. The OS notification is unchanged."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        self._old_fleet_home = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo")
        self.task_id = "1"
        state.save_task(self.state_dir, self.task_id, {
            "id": self.task_id,
            "status": "running",
            "branch": "fleet/task/1",
            "worktree": "/wt/1",
        })
        self.session_dir = state.session_dir("main")

    def tearDown(self) -> None:
        if self._old_fleet_home is None:
            os.environ.pop("FLEET_HOME", None)
        else:
            os.environ["FLEET_HOME"] = self._old_fleet_home
        self._tmp.cleanup()

    def _enable(self, value: str = "true") -> None:
        project = state.load_project(self.state_dir)
        project["notify_leader_on_driver_done"] = value
        state.save_project(self.state_dir, project)

    def _write_session_record(self, label: str = "main", agent: str = "claude:opus") -> None:
        rec_path = state.session_record_path(label)
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(json.dumps({"label": label, "agent": agent}), encoding="utf-8")

    def _ask(self, question: str = "Should I use A or B?") -> tuple[int, object]:
        with (
            patch("fleet.commands.ask.task_context.resolve",
                  return_value=(self.state_dir, self.task_id)),
            patch("fleet.commands.ask.notify.send") as send,
        ):
            rc = ask_cmd.run(argparse.Namespace(question=question, task_id=None))
        return rc, send

    def test_default_off_enqueues_and_spawns_nothing(self) -> None:
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}),
            patch("fleet.leader_notifier.start_detached") as spawn,
        ):
            rc, send = self._ask()
        self.assertEqual(rc, 0)
        spawn.assert_not_called()
        self.assertFalse(leader_notifier.queue_path(self.session_dir).exists())
        send.assert_called_once()  # the OS notification is independent of the opt-in

    def test_on_enqueues_ask_record_and_spawns_notifier(self) -> None:
        self._enable()
        self._write_session_record()
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}),
            patch("fleet.leader_notifier.start_detached") as spawn,
        ):
            rc, _send = self._ask("Should I use A or B?")
        self.assertEqual(rc, 0)
        spawn.assert_called_once()
        kwargs = spawn.call_args.kwargs
        self.assertEqual(kwargs["session_dir"], self.session_dir)
        self.assertEqual(kwargs["session"], "fleet-main")
        self.assertEqual(kwargs["window"], "leader")
        self.assertEqual(kwargs["agent_spec"], "claude:opus")
        (rec,) = leader_notifier.read_queue(self.session_dir)
        self.assertEqual(rec["kind"], "ask")
        self.assertEqual(rec["status"], "awaiting_orders")
        self.assertEqual(rec["question"], "Should I use A or B?")
        self.assertEqual(rec["task_id"], "1")
        self.assertEqual(rec["project"], "demo")
        self.assertEqual(rec["state_dir"], str(self.state_dir))
        # The task was still parked as awaiting_orders (unchanged behaviour).
        self.assertEqual(state.load_task(self.state_dir, "1")["status"], "awaiting_orders")

    def test_on_keeps_os_notification_unchanged(self) -> None:
        self._enable()
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}),
            patch("fleet.leader_notifier.start_detached"),
        ):
            _rc, send = self._ask("Should I use A or B?")
        send.assert_called_once_with(
            self.state_dir,
            title="fleet demo: task-1 awaiting orders",
            message="Should I use A or B?",
            level="waiting",
            project="demo",
            task_id="1",
        )

    def test_on_without_leader_record_leaves_ask_queued(self) -> None:
        self._enable()
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}),
            patch("fleet.leader_notifier.start_detached") as spawn,
        ):
            rc, _send = self._ask()
        self.assertEqual(rc, 0)
        spawn.assert_not_called()
        self.assertEqual(len(leader_notifier.read_queue(self.session_dir)), 1)

    def test_owner_session_routes_ask_to_named_session(self) -> None:
        self._enable()
        self._write_session_record("migration", "codex:gpt-5.5")
        task = state.load_task(self.state_dir, "1")
        task["owner_session"] = "migration"
        state.save_task(self.state_dir, "1", task)
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"], "fleet-migration": ["leader"]}),
            patch("fleet.leader_notifier.start_detached") as spawn,
        ):
            self._ask()
        self.assertEqual(spawn.call_args.kwargs["session"], "fleet-migration")
        self.assertEqual(len(leader_notifier.read_queue(state.session_dir("migration"))), 1)
        self.assertFalse(leader_notifier.queue_path(state.session_dir("main")).exists())

    def test_ask_then_done_gate_both_queued_and_injected(self) -> None:
        """End-to-end through the producers: an ask followed by a done for the same
        task yields two independent records; one idle-flush injects both, with the
        answer command for the ask and the gate instruction for the done."""
        self._enable()
        with (
            use_fake_mux(sessions={"fleet-main": ["leader"]}, capture=READY_PANE) as fake,
            patch("fleet.leader_notifier.start_detached"),
            patch("fleet.leader_notifier.time.sleep", return_value=None),
            patch.object(leader_notifier, "_run_capture", return_value=None),
        ):
            self._ask("A or B?")
            task = state.load_task(self.state_dir, "1")
            task["status"] = "completed"
            done_cmd._maybe_notify_leader(
                self.state_dir, "1", task, state.load_project(self.state_dir), "demo",
                status="completed", result="approved", summary="task-1 completed",
            )
            recs = leader_notifier.read_queue(self.session_dir)
            self.assertEqual([r["kind"] for r in recs], ["ask", "done"])
            rc = leader_notifier.notify(
                session_dir=self.session_dir,
                session="fleet-main",
                window="leader",
                agent_spec="claude:opus",
                timeout=0.5,
                poll_interval=0.001,
            )
        self.assertEqual(rc, 0)
        (args, _kwargs), = fake.calls_named("send_text")
        self.assertIn('fleet-agent inbox 1 "<answer>" --project demo', args[2])
        self.assertIn("task-1 [completed]", args[2])
        self.assertEqual(leader_notifier.read_queue(self.session_dir), [])


if __name__ == "__main__":
    unittest.main()
