"""Tests for the Approve / Reject toast buttons (#318).

Hermetic: no real registry, toast, dialog or multiplexer. Dialogs are patched,
``fleet.notify.subprocess.run`` is patched, and every state dir lives in a
throwaway ``FLEET_HOME``.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

import tests._fleet_test_helpers as helpers  # noqa: E402
from fleet import leader_notifier, notify, state, toast_nonce  # noqa: E402
from fleet.commands import url_handler  # noqa: E402
from fleet.events import read_events  # noqa: E402

NONCE = "0123456789abcdef0123456789abcdef"


def _gate_task(task_id: str = "1") -> dict:
    return {
        "id": task_id,
        "status": "awaiting_orders",
        "current_stage": 0,
        "stages": [
            {
                "role": "driver",
                "agent": "claude:sonnet",
                "status": "running",
                "user_approval": {"required": True, "status": "asked"},
            }
        ],
    }


class ToastNonceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_issue_and_check_valid(self) -> None:
        n = toast_nonce.issue(self.state_dir, "p", "1", 0)
        self.assertRegex(n, toast_nonce.NONCE_RE)
        self.assertIsNone(toast_nonce.check(self.state_dir, "p", "1", 0, n))

    def test_store_does_not_hold_the_plain_nonce(self) -> None:
        n = toast_nonce.issue(self.state_dir, "p", "1", 0)
        self.assertNotIn(n, toast_nonce.store_path(self.state_dir, "1").read_text("utf-8"))

    def test_unknown_nonce_refused(self) -> None:
        toast_nonce.issue(self.state_dir, "p", "1", 0)
        self.assertEqual(toast_nonce.check(self.state_dir, "p", "1", 0, NONCE), "unknown nonce")
        self.assertEqual(toast_nonce.consume(self.state_dir, "p", "1", 0, NONCE), "unknown nonce")

    def test_missing_store_refused(self) -> None:
        self.assertEqual(toast_nonce.consume(self.state_dir, "p", "1", 0, NONCE), "unknown nonce")

    def test_single_use(self) -> None:
        n = toast_nonce.issue(self.state_dir, "p", "1", 0)
        self.assertIsNone(toast_nonce.consume(self.state_dir, "p", "1", 0, n))
        self.assertEqual(toast_nonce.consume(self.state_dir, "p", "1", 0, n), "nonce already used")
        self.assertEqual(toast_nonce.check(self.state_dir, "p", "1", 0, n), "nonce already used")

    def test_check_does_not_spend(self) -> None:
        n = toast_nonce.issue(self.state_dir, "p", "1", 0)
        toast_nonce.check(self.state_dir, "p", "1", 0, n)
        self.assertIsNone(toast_nonce.consume(self.state_dir, "p", "1", 0, n))

    def test_expiry(self) -> None:
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        n = toast_nonce.issue(self.state_dir, "p", "1", 0, now=t0)
        just_before = t0 + toast_nonce.TTL - timedelta(seconds=1)
        self.assertIsNone(toast_nonce.check(self.state_dir, "p", "1", 0, n, now=just_before))
        expired = t0 + toast_nonce.TTL
        self.assertEqual(
            toast_nonce.consume(self.state_dir, "p", "1", 0, n, now=expired), "nonce expired"
        )

    def test_wrong_stage_refused_and_not_spent(self) -> None:
        n = toast_nonce.issue(self.state_dir, "p", "1", 0)
        self.assertEqual(
            toast_nonce.consume(self.state_dir, "p", "1", 1, n), "nonce is bound to another stage"
        )
        self.assertIsNone(toast_nonce.consume(self.state_dir, "p", "1", 0, n))

    def test_wrong_project_refused(self) -> None:
        n = toast_nonce.issue(self.state_dir, "p", "1", 0)
        self.assertEqual(
            toast_nonce.check(self.state_dir, "other", "1", 0, n),
            "nonce is bound to another task",
        )

    def test_nonce_is_per_task_store(self) -> None:
        n = toast_nonce.issue(self.state_dir, "p", "1", 0)
        self.assertEqual(toast_nonce.check(self.state_dir, "p", "2", 0, n), "unknown nonce")

    def test_issue_prunes_used_and_expired(self) -> None:
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        old = toast_nonce.issue(self.state_dir, "p", "1", 0, now=t0)
        used = toast_nonce.issue(self.state_dir, "p", "1", 0, now=t0)
        toast_nonce.consume(self.state_dir, "p", "1", 0, used, now=t0)
        fresh = toast_nonce.issue(self.state_dir, "p", "1", 0, now=t0 + toast_nonce.TTL * 2)
        data = json.loads(toast_nonce.store_path(self.state_dir, "1").read_text("utf-8"))
        self.assertEqual(len(data), 1)
        self.assertIsNone(
            toast_nonce.check(self.state_dir, "p", "1", 0, fresh, now=t0 + toast_nonce.TTL * 2)
        )
        self.assertIsNotNone(toast_nonce.check(self.state_dir, "p", "1", 0, old))


class GateUriTests(unittest.TestCase):
    def _uri(self, action="approve", **over) -> str:
        q = {"project": "p", "task": "1", "stage": "0", "nonce": NONCE}
        q.update(over)
        return f"fleet://{action}?" + "&".join(f"{k}={v}" for k, v in q.items() if v is not None)

    def test_valid_approve_and_reject(self) -> None:
        for action in ("approve", "reject"):
            with self.subTest(action=action):
                self.assertEqual(
                    url_handler.parse_uri(self._uri(action)),
                    url_handler.GateTarget(action, "p", "1", 0, NONCE),
                )

    def test_missing_stage_or_nonce_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri(self._uri(stage=None)))
        self.assertIsNone(url_handler.parse_uri(self._uri(nonce=None)))

    def test_bad_nonce_shape_rejected(self) -> None:
        for bad in ("", "abc", NONCE.upper(), NONCE + "0", "g" * 32, "%26" + NONCE[3:]):
            with self.subTest(bad=bad):
                self.assertIsNone(url_handler.parse_uri(self._uri(nonce=bad)))

    def test_bad_stage_shape_rejected(self) -> None:
        for bad in ("", "-1", "a", "1.5", "1000", "1%26x"):
            with self.subTest(bad=bad):
                self.assertIsNone(url_handler.parse_uri(self._uri(stage=bad)))

    def test_duplicate_nonce_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri(self._uri() + f"&nonce={NONCE}"))

    def test_injection_in_project_or_task_rejected(self) -> None:
        for bad in ("a%26b", "a%22b", "a%25b", "a+b", "..", "a;b"):
            with self.subTest(bad=bad):
                self.assertIsNone(url_handler.parse_uri(self._uri(project=bad)))
                self.assertIsNone(url_handler.parse_uri(self._uri("reject", task=bad)))

    def test_unknown_action_rejected(self) -> None:
        self.assertIsNone(url_handler.parse_uri(self._uri("delete")))

    def test_attach_still_parses_to_attach_target(self) -> None:
        self.assertEqual(
            url_handler.parse_uri("fleet://attach?project=p&task=1"),
            url_handler.AttachTarget("p", "1"),
        )


class GateHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fleet_home = Path(self._tmp.name) / "fleet-state"
        self.fleet_home.mkdir()
        repo = Path(self._tmp.name) / "repo"
        repo.mkdir()
        old = os.environ.get("FLEET_HOME")
        os.environ["FLEET_HOME"] = str(self.fleet_home)
        self.addCleanup(
            lambda: os.environ.__setitem__("FLEET_HOME", old)
            if old is not None
            else os.environ.pop("FLEET_HOME", None)
        )
        self.state_dir = helpers.make_project(self.fleet_home, "demo", repo)
        state.save_task(self.state_dir, "1", _gate_task())
        self.nonce = toast_nonce.issue(self.state_dir, "demo", "1", 0)

    def _uri(self, action: str, nonce: str | None = None, stage: int = 0) -> str:
        return (
            f"fleet://{action}?project=demo&task=1&stage={stage}&nonce={nonce or self.nonce}"
        )

    def _run(self, uri: str, *, confirm=True, reason="needs tests"):
        with patch.object(url_handler, "confirm_approve", return_value=confirm) as c, \
             patch.object(url_handler, "ask_reject_reason", return_value=reason) as r, \
             patch.object(url_handler.approval, "run_approve", return_value=0) as ra, \
             patch.object(url_handler.approval, "run_reject", return_value=0) as rr, \
             patch.object(url_handler.leader_notifier, "push_to_leader") as push, \
             patch.object(url_handler, "open_attach_terminal", return_value=0) as opened:
            rc = url_handler.run(argparse.Namespace(uri=uri))
        return rc, dict(c=c, r=r, ra=ra, rr=rr, push=push, opened=opened)

    def _log(self) -> str:
        p = state.global_dir() / url_handler.LOG_NAME
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def test_approve_confirms_then_relays_with_toast_source(self) -> None:
        rc, m = self._run(self._uri("approve"))
        self.assertEqual(rc, 0)
        m["c"].assert_called_once_with("demo", "1")
        m["ra"].assert_called_once()
        ns = m["ra"].call_args.args[0]
        self.assertEqual((ns.task_id, ns.project, ns.source), ("1", "demo", "toast"))
        m["rr"].assert_not_called()
        self.assertEqual(
            m["push"].call_args.kwargs["summary"], "[approved by user via toast]"
        )
        self.assertEqual(
            m["push"].call_args.kwargs["kind"], leader_notifier.KIND_TOAST_DECISION
        )

    def test_approve_declined_does_nothing_and_keeps_nonce(self) -> None:
        rc, m = self._run(self._uri("approve"), confirm=False)
        self.assertEqual(rc, 0)
        m["ra"].assert_not_called()
        m["push"].assert_not_called()
        self.assertIsNone(toast_nonce.check(self.state_dir, "demo", "1", 0, self.nonce))

    def test_reject_relays_reason(self) -> None:
        rc, m = self._run(self._uri("reject"), reason="  missing tests ")
        self.assertEqual(rc, 0)
        m["c"].assert_not_called()
        ns = m["rr"].call_args.args[0]
        self.assertEqual((ns.reason, ns.source), ("missing tests", "toast"))
        m["ra"].assert_not_called()
        self.assertEqual(
            m["push"].call_args.kwargs["summary"],
            "[rejected by user via toast: missing tests]",
        )

    def test_reject_cancelled_or_empty_reason_does_nothing(self) -> None:
        for reason in (None, "", "   "):
            with self.subTest(reason=reason):
                rc, m = self._run(self._uri("reject"), reason=reason)
                self.assertEqual(rc, 0)
                m["rr"].assert_not_called()
                m["push"].assert_not_called()
                self.assertIsNone(toast_nonce.check(self.state_dir, "demo", "1", 0, self.nonce))

    def test_reuse_refused_and_logged(self) -> None:
        self._run(self._uri("approve"))
        rc, m = self._run(self._uri("approve"))
        self.assertNotEqual(rc, 0)
        m["c"].assert_not_called()
        m["ra"].assert_not_called()
        self.assertIn("nonce already used", self._log())

    def test_approve_and_reject_share_one_decision(self) -> None:
        self._run(self._uri("approve"))
        rc, m = self._run(self._uri("reject"))
        self.assertNotEqual(rc, 0)
        m["rr"].assert_not_called()

    def test_missing_unknown_and_expired_nonce_refused(self) -> None:
        rc, m = self._run(self._uri("approve", nonce="f" * 32))
        self.assertNotEqual(rc, 0)
        m["ra"].assert_not_called()
        rc, m = self._run("fleet://approve?project=demo&task=1&stage=0")
        self.assertNotEqual(rc, 0)
        m["ra"].assert_not_called()
        with patch.object(
            toast_nonce, "_now",
            return_value=datetime.now(timezone.utc) + toast_nonce.TTL + timedelta(minutes=1),
        ):
            rc, m = self._run(self._uri("approve"))
        self.assertNotEqual(rc, 0)
        m["c"].assert_not_called()
        m["ra"].assert_not_called()
        self.assertIn("nonce expired", self._log())

    def test_wrong_stage_refused(self) -> None:
        rc, m = self._run(self._uri("approve", stage=1))
        self.assertNotEqual(rc, 0)
        m["ra"].assert_not_called()
        self.assertIn("another stage", self._log())

    def test_task_no_longer_at_gate_refused(self) -> None:
        task = _gate_task()
        task["stages"][0]["user_approval"]["status"] = "approved"
        task["status"] = "running"
        state.save_task(self.state_dir, "1", task)
        rc, m = self._run(self._uri("approve"))
        self.assertNotEqual(rc, 0)
        m["c"].assert_not_called()
        m["ra"].assert_not_called()
        self.assertIn("no longer at the approval gate", self._log())

    def test_task_moved_to_another_stage_refused(self) -> None:
        task = _gate_task()
        task["stages"].append({"role": "reviewer", "status": "running"})
        task["current_stage"] = 1
        state.save_task(self.state_dir, "1", task)
        rc, m = self._run(self._uri("reject"))
        self.assertNotEqual(rc, 0)
        m["rr"].assert_not_called()

    def test_gate_settled_while_dialog_open_is_refused(self) -> None:
        def _settle(*_a):
            task = _gate_task()
            task["status"] = "running"
            state.save_task(self.state_dir, "1", task)
            return True

        with patch.object(url_handler, "confirm_approve", side_effect=_settle), \
             patch.object(url_handler.approval, "run_approve") as ra:
            rc = url_handler.run(argparse.Namespace(uri=self._uri("approve")))
        self.assertNotEqual(rc, 0)
        ra.assert_not_called()

    def test_failed_relay_does_not_tell_leader(self) -> None:
        with patch.object(url_handler, "confirm_approve", return_value=True), \
             patch.object(url_handler.approval, "run_approve", return_value=1), \
             patch.object(url_handler.leader_notifier, "push_to_leader") as push:
            rc = url_handler.run(argparse.Namespace(uri=self._uri("approve")))
        self.assertEqual(rc, 1)
        push.assert_not_called()

    def test_attach_button_opens_terminal_and_emits_toast_event(self) -> None:
        rc, m = self._run("fleet://attach?project=demo&task=1")
        self.assertEqual(rc, 0)
        m["opened"].assert_called_once_with("demo", "1")
        events = read_events(self.state_dir / "events.jsonl")
        self.assertTrue(
            any(e["type"] == "toast_action" and e.get("source") == "toast" for e in events)
        )

    def test_real_approval_records_toast_source_event(self) -> None:
        # Un-mocked relay path: only the driver launch / leader push are patched.
        with patch.object(url_handler, "confirm_approve", return_value=True), \
             patch("fleet.orchestrator._launch_driver_for_stage"), \
             patch.object(url_handler.leader_notifier, "push_to_leader") as push:
            rc = url_handler.run(argparse.Namespace(uri=self._uri("approve")))
        self.assertEqual(rc, 0)
        events = read_events(self.state_dir / "events.jsonl")
        approve = [e for e in events if e["type"] == "approve"]
        self.assertEqual(len(approve), 1)
        self.assertEqual(approve[0]["source"], "toast")
        push.assert_called_once()

    def test_real_rejection_records_reason_and_toast_source(self) -> None:
        with patch.object(url_handler, "ask_reject_reason", return_value="add tests"), \
             patch("fleet.orchestrator._launch_driver_for_stage"), \
             patch.object(url_handler.leader_notifier, "push_to_leader"):
            rc = url_handler.run(argparse.Namespace(uri=self._uri("reject")))
        self.assertEqual(rc, 0)
        events = read_events(self.state_dir / "events.jsonl")
        reject = [e for e in events if e["type"] == "reject"]
        self.assertEqual((reject[0]["source"], reject[0]["reason"]), ("toast", "add tests"))


class DialogTests(unittest.TestCase):
    def test_off_windows_dialogs_decline(self) -> None:
        with patch.object(url_handler.sys, "platform", "linux"):
            self.assertFalse(url_handler.confirm_approve("p", "1"))
            self.assertIsNone(url_handler.ask_reject_reason("p", "1"))


class ApprovalToastXmlTests(unittest.TestCase):
    """Approve/Reject/Open actions exist only on approval-gate toasts, once setup is done."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)

    def _script(self, *, configured: bool = True, **kwargs) -> str:
        with patch("fleet.notify.platform.system", return_value="Windows"), \
             patch("fleet.windows_notify_setup.is_aumid_configured", return_value=configured), \
             patch("fleet.windows_notify_setup.is_protocol_configured", return_value=configured), \
             patch("fleet.notify.subprocess.run") as run:
            run.return_value.returncode = 0
            notify._windows_notify({}, "t", "m", "waiting", **kwargs)
        return base64.b64decode(run.call_args.args[0][-1]).decode("utf-16-le")

    def test_gate_toast_has_three_actions_with_nonce(self) -> None:
        script = self._script(
            project="demo", task_id="1", state_dir=self.state_dir, approval_stage=2
        )
        self.assertEqual(script.count("<action "), 3)
        for label in ("Approve", "Reject", "Open"):
            self.assertIn(f'content="{label}"', script)
        self.assertIn("fleet://approve?", script)
        self.assertIn("fleet://reject?", script)
        self.assertIn("stage=2", script)
        self.assertIn("nonce=", script)
        self.assertEqual(script.count('activationType="protocol"'), 4)  # toast + 3 buttons

    def test_button_nonce_is_valid_for_that_gate(self) -> None:
        import re

        script = self._script(
            project="demo", task_id="1", state_dir=self.state_dir, approval_stage=2
        )
        nonce = re.search(r"nonce=([0-9a-f]{32})", script).group(1)
        self.assertIsNone(toast_nonce.check(self.state_dir, "demo", "1", 2, nonce))
        self.assertIsNotNone(toast_nonce.check(self.state_dir, "demo", "1", 3, nonce))

    def test_open_button_is_attach(self) -> None:
        script = self._script(
            project="demo", task_id="1", state_dir=self.state_dir, approval_stage=0
        )
        self.assertIn('arguments="fleet://attach?project=demo&amp;task=1"', script)

    def test_action_arguments_escape_ampersand(self) -> None:
        script = self._script(
            project="demo", task_id="1", state_dir=self.state_dir, approval_stage=0
        )
        line = next(ln for ln in script.splitlines() if "<actions>" in ln)
        self.assertNotRegex(line, r"project=demo&task")
        self.assertIn("&amp;", line)

    def test_plain_toast_has_no_actions(self) -> None:
        script = self._script(project="demo", task_id="1", state_dir=self.state_dir)
        self.assertNotIn("<actions>", script)
        self.assertFalse(toast_nonce.store_path(self.state_dir, "1").exists())

    def test_no_actions_when_setup_not_done(self) -> None:
        script = self._script(
            configured=False,
            project="demo", task_id="1", state_dir=self.state_dir, approval_stage=0,
        )
        self.assertNotIn("<actions>", script)
        self.assertFalse(toast_nonce.store_path(self.state_dir, "1").exists())

    def test_no_actions_without_project_and_task(self) -> None:
        script = self._script(state_dir=self.state_dir, approval_stage=0)
        self.assertNotIn("<actions>", script)

    def test_nonce_store_failure_falls_back_to_plain_toast(self) -> None:
        with patch.object(toast_nonce, "issue", side_effect=OSError("disk")):
            script = self._script(
                project="demo", task_id="1", state_dir=self.state_dir, approval_stage=0
            )
        self.assertNotIn("<actions>", script)
        self.assertIn("fleet://attach?", script)

    def test_send_passes_approval_stage_through(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "FLEET_NO_NOTIFY"}
        with patch.dict(os.environ, env, clear=True), \
             patch("fleet.notify._macos_notify"), patch("fleet.notify._slack_notify"), \
             patch("fleet.notify._windows_notify") as win:
            notify.send(self.state_dir, "t", "m", project="p", task_id="1", approval_stage=3)
        self.assertEqual(win.call_args.kwargs["approval_stage"], 3)
        self.assertEqual(win.call_args.kwargs["state_dir"], self.state_dir)


class OrchestratorGateToastTests(unittest.TestCase):
    def test_request_user_approval_marks_toast_with_stage(self) -> None:
        from fleet import orchestrator

        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            repo = Path(tmp) / "repo"
            repo.mkdir()
            with patch.dict(os.environ, {"FLEET_HOME": str(home)}):
                sd = helpers.make_project(home, "demo", repo)
                task = _gate_task()
                task["current_stage"] = 0
                state.save_task(sd, "1", task)
                with patch("fleet.notify.send") as send:
                    orchestrator._request_user_approval(sd, "1", task, task["stages"][0])
        self.assertEqual(send.call_args.kwargs["approval_stage"], 0)
        self.assertIn("needs approval", send.call_args.kwargs["title"])


class LeaderToastDecisionTests(unittest.TestCase):
    def _rec(self, kind: str, task_id: str = "1", summary: str = "s") -> dict:
        return {"task_id": task_id, "kind": kind, "status": "running", "summary": summary,
                "pr_url": "https://github.com/o/r/pull/1"}

    def test_toast_decision_is_rendered_with_do_not_reapprove(self) -> None:
        block = leader_notifier.render_block(
            [self._rec(leader_notifier.KIND_TOAST_DECISION, summary="[approved by user via toast]")]
        )
        self.assertIn("[approved by user via toast]", block)
        self.assertIn("do NOT approve/reject it again", block)
        self.assertNotIn("run the gate", block)
        self.assertNotIn("\n", block)

    def test_rejection_reason_is_kept_single_line(self) -> None:
        block = leader_notifier.render_block(
            [self._rec(leader_notifier.KIND_TOAST_DECISION,
                       summary="[rejected by user via toast: a\nb]")]
        )
        self.assertIn("[rejected by user via toast: a b]", block)

    def test_mixed_with_done_keeps_both_instructions(self) -> None:
        block = leader_notifier.render_block(
            [self._rec(leader_notifier.KIND_DONE, "1"),
             self._rec(leader_notifier.KIND_TOAST_DECISION, "2")]
        )
        self.assertIn("run the gate", block)
        self.assertIn("For each [toast decision] entry", block)

    def test_push_to_leader_respects_opt_in(self) -> None:
        with TemporaryDirectory() as tmp, \
             patch.object(leader_notifier, "enqueue") as enq:
            leader_notifier.push_to_leader(
                Path(tmp), "1", {}, {"notify_leader_on_driver_done": False}, "demo",
                status="running", summary="s", kind=leader_notifier.KIND_TOAST_DECISION,
            )
        enq.assert_not_called()


if __name__ == "__main__":
    unittest.main()
