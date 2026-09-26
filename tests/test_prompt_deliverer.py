from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import tests._fleet_test_helpers  # noqa: E402,F401  (hermetic env: FLEET_NO_NOTIFY / FLEET_NO_MUX)
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import leader_notifier, mux, prompt_deliverer, prompt_pointer, state  # noqa: E402
from fleet.adapters import CodexAdapter  # noqa: E402
from fleet.events import append_event  # noqa: E402
from tests import _pane_fixtures as pane_fx  # noqa: E402
from tests._fake_mux import use_fake_mux  # noqa: E402


class PromptDelivererTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo")
        self.task_id = "1"
        self.task_dir = state.task_dir(self.state_dir, self.task_id)
        self.task_dir.mkdir(parents=True)
        self.prompt_path = self.task_dir / "driver-prompt.md"
        self.prompt_path.write_text("FULL-PROMPT-BODY-MARKER\n", encoding="utf-8")
        state.save_task(
            self.state_dir,
            self.task_id,
            {
                "id": self.task_id,
                "status": "spawning",
                "current_stage": 0,
                "stages": [{"role": "driver", "agent": "codex:o4-mini", "status": "running"}],
            },
        )
        self._old_no_notify = os.environ.get("FLEET_NO_NOTIFY")
        os.environ["FLEET_NO_NOTIFY"] = "1"
        self._sleep_patch = patch("fleet.prompt_deliverer.time.sleep", return_value=None)
        self._sleep_patch.start()

    def tearDown(self) -> None:
        self._sleep_patch.stop()
        if self._old_no_notify is None:
            os.environ.pop("FLEET_NO_NOTIFY", None)
        else:
            os.environ["FLEET_NO_NOTIFY"] = self._old_no_notify
        self._tmp.cleanup()

    def _deliver(self, agent: str = "codex:o4-mini", timeout: float = 1.0) -> int:
        return prompt_deliverer.deliver(
            state_dir=self.state_dir,
            task_id=self.task_id,
            session="fleet-demo",
            window="1·driver",
            prompt_path=self.prompt_path,
            agent_spec=agent,
            timeout=timeout,
            poll_interval=0.01,
        )

    def _events(self) -> list[dict]:
        path = self.state_dir / "events.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def _ack_on_enter(self, *_args, **_kwargs) -> None:
        append_event(
            self.state_dir / "events.jsonl",
            "inbox_seen",
            task_id=self.task_id,
            watermark=None,
        )

    def test_ready_marker_pastes_pointer_and_emits_event(self) -> None:
        with use_fake_mux(capture="ready\n›\n") as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        # The deliverer must paste a pointer to the prompt file, never the
        # prompt body (full-content paste regresses Issue #90).
        pastes = fake.calls_named("paste")
        self.assertEqual(len(pastes), 1)
        session, window, pointer = pastes[0][0]
        self.assertEqual((session, window), ("fleet-demo", "1·driver"))
        self.assertIn(str(self.prompt_path.resolve()), pointer)
        self.assertNotIn("FULL-PROMPT-BODY-MARKER", pointer)
        # The pointer sidecar file is still written next to the prompt.
        sidecar = prompt_pointer.pointer_path(self.prompt_path)
        self.assertEqual(sidecar.read_text(encoding="utf-8"), pointer)
        # paste, then exactly one submit Enter.
        self.assertEqual(
            fake.sent(),
            [("paste", "1·driver", pointer), ("key", "1·driver", "Enter")],
        )
        self.assertEqual(self._events()[-1]["type"], "prompt_delivered")

    def test_codex_session_name_renames_before_paste(self) -> None:
        with use_fake_mux(capture="ready\n›\n") as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = prompt_deliverer.deliver(
                state_dir=self.state_dir,
                task_id=self.task_id,
                session="fleet-demo",
                window="1·driver",
                prompt_path=self.prompt_path,
                    agent_spec="codex:o4-mini",
                session_name="demo-1-driver",
                timeout=1.0,
                poll_interval=0.01,
            )

        self.assertEqual(result, 0)
        # rename keystrokes come first (open popup, clear field, type name,
        # confirm), then the paste, then the submit Enter.
        # Ctrl-u is an explicit key press (never typed as the text "C-u").
        sent = [(kind, payload) for kind, _window, payload in fake.sent()]
        self.assertEqual(
            [(k, p if k != "paste" else None) for k, p in sent],
            [
                ("text", "/rename"),
                ("key", "Enter"),
                ("key", "Ctrl-u"),
                ("text", "demo-1-driver"),
                ("key", "Enter"),
                ("paste", None),
                ("key", "Enter"),
            ],
        )

    def test_claude_session_name_does_not_send_rename_keys(self) -> None:
        with use_fake_mux(capture='status\n❯ Try "help"\n') as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = prompt_deliverer.deliver(
                state_dir=self.state_dir,
                task_id=self.task_id,
                session="fleet-demo",
                window="1·driver",
                prompt_path=self.prompt_path,
                    agent_spec="claude:opus",
                session_name="demo-1-driver",
                timeout=1.0,
                poll_interval=0.01,
            )

        self.assertEqual(result, 0)
        # claude is named at launch → only the submit Enter, no rename keys.
        self.assertEqual(fake.calls_named("send_text"), [])
        self.assertEqual(fake.calls_named("send_key"), [(("fleet-demo", "1·driver", "Enter"), {})])

    def test_submits_once_when_ack_lands_on_first_enter(self) -> None:
        # When the ack arrives on the first submit Enter, no resubmit fires —
        # the retry path is only walked while the ack is still missing.
        with use_fake_mux(capture="ready\n›\n") as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual(fake.calls_named("send_key"), [(("fleet-demo", "1·driver", "Enter"), {})])
        self.assertEqual(self._events()[-1]["type"], "prompt_delivered")

    def test_codex_resubmits_enter_until_ack(self) -> None:
        # codex intermittently drops the first submit Enter; the deliverer must
        # re-press it until the inbox_seen ack lands (Issue #179). The ack here
        # only fires on the third send_keys, proving the retry path runs.
        calls = {"n": 0}

        def ack_on_third_enter(*_args, **_kwargs) -> None:
            calls["n"] += 1
            if calls["n"] >= 3:
                self._ack_on_enter()

        with (
            patch.object(CodexAdapter, "submit_retry_interval_seconds", 0.0),
            use_fake_mux(capture="ready\n›\n") as fake,
        ):
            fake.on["send_key"] = ack_on_third_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        keys = fake.calls_named("send_key")
        self.assertGreaterEqual(len(keys), 3)
        # Every resubmit is the same bare Enter — never a duplicate paste.
        for c in keys:
            self.assertEqual(c, (("fleet-demo", "1·driver", "Enter"), {}))
        self.assertEqual(len(fake.calls_named("paste")), 1)
        self.assertEqual(self._events()[-1]["type"], "prompt_delivered")

    def test_claude_does_not_resubmit_enter(self) -> None:
        # claude submits with a single reliable Enter (submit_retries=0): a
        # missing ack must never trigger a resubmit (which would inject a stray
        # Enter into a working claude pane).
        with use_fake_mux(capture='status\n❯ Try "help"\n') as fake:
            result = self._deliver(agent="claude:opus", timeout=0.05)

        self.assertEqual(result, 1)
        self.assertEqual(len(fake.calls_named("send_key")), 1)
        self.assertEqual(state.load_task(self.state_dir, self.task_id)["status"], "failed")

    def test_ack_from_other_task_does_not_confirm_delivery(self) -> None:
        def ack_other_task(*_args, **_kwargs) -> None:
            append_event(
                self.state_dir / "events.jsonl",
                "inbox_seen",
                task_id="2",
                watermark=None,
            )

        with use_fake_mux(capture="ready\n›\n") as fake:
            fake.on["send_key"] = ack_other_task
            result = self._deliver(timeout=0.02)

        self.assertEqual(result, 1)
        self.assertEqual(len(fake.calls_named("send_key")), 1)
        self.assertEqual(state.load_task(self.state_dir, self.task_id)["status"], "failed")
        self.assertEqual(self._events()[-1]["type"], "error")
        self.assertIn("inbox_seen ack", self._events()[-1]["message"])

    def test_missing_ack_marks_failed(self) -> None:
        with use_fake_mux(capture="ready\n›\n") as fake:
            result = self._deliver(timeout=0.02)

        self.assertEqual(result, 1)
        self.assertEqual(len(fake.calls_named("send_key")), 1)
        self.assertEqual(state.load_task(self.state_dir, self.task_id)["status"], "failed")
        self.assertEqual(self._events()[-1]["type"], "error")
        self.assertIn("inbox_seen ack", self._events()[-1]["message"])

    def test_ack_checkpoint_uses_current_task_timestamp_only(self) -> None:
        events_path = self.state_dir / "events.jsonl"
        events_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2099-01-01T00:00:00Z",
                            "type": "heartbeat",
                            "task_id": "2",
                        }
                    ),
                    json.dumps(
                        {
                            "ts": "2026-05-20T10:00:00Z",
                            "type": "heartbeat",
                            "task_id": self.task_id,
                        }
                    ),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        checkpoint = prompt_deliverer._event_checkpoint(events_path, self.task_id)
        with events_path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": "2026-05-20T10:01:00Z",
                        "type": "inbox_seen",
                        "task_id": self.task_id,
                    }
                )
                + "\n"
            )

        matched, _offset = prompt_deliverer._scan_for_inbox_seen_ack(
            events_path,
            self.task_id,
            checkpoint,
            checkpoint.offset,
        )

        self.assertTrue(matched)

    def test_codex_ready_with_update_banner_is_not_a_gate(self) -> None:
        pane = """
╭─────────────────────────────────────────────────╮
│ ✨ Update available! 0.132.0 -> 0.133.0         │
│ Run npm install -g @openai/codex to update.     │
╰─────────────────────────────────────────────────╯

╭─────────────────────────────────────────────────────╮
│ >_ OpenAI Codex (v0.132.0)                          │
╰─────────────────────────────────────────────────────╯

›\u00a0
"""
        with use_fake_mux(capture=pane) as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual([e["type"] for e in self._events()], ["inbox_seen", "prompt_delivered"])

    def test_codex_blocking_update_menu_emits_awaiting_orders(self) -> None:
        panes = iter(
            [
                "Update available\n› 1. Update now\n  2. Skip this version\n  3. Skip for now\n",
                "›\n",
            ]
        )
        with use_fake_mux(capture=list(panes)) as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual(
            [e["type"] for e in self._events()],
            ["awaiting_orders", "inbox_seen", "prompt_delivered"],
        )

    def test_codex_trust_menu_cursor_is_not_ready(self) -> None:
        pane = """
> You are in /private/tmp
  Do you trust the contents of this directory?
› 1. Yes, continue
  2. No, quit
  Press enter to continue
"""
        panes = iter([pane, "› Run /review on my current changes\n"])
        with use_fake_mux(capture=list(panes)) as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual(
            [e["type"] for e in self._events()],
            ["awaiting_orders", "inbox_seen", "prompt_delivered"],
        )

    def test_claude_ready_marker_matches_current_tui_prompt(self) -> None:
        with use_fake_mux(capture='status\n❯ Try "help"\n') as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver(agent="claude:opus")

        self.assertEqual(result, 0)
        self.assertEqual(self._events()[-1]["type"], "prompt_delivered")

    def test_claude_menu_cursor_is_not_ready(self) -> None:
        panes = iter(
            [
                "Do you trust this workspace?\n❯ 1. Yes, proceed\n  2. No\n",
                'status\n❯ Try "help"\n',
            ]
        )
        with use_fake_mux(capture=list(panes)) as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver(agent="claude:opus")

        self.assertEqual(result, 0)
        self.assertEqual(
            [e["type"] for e in self._events()],
            ["awaiting_orders", "inbox_seen", "prompt_delivered"],
        )

    def test_claude_trust_dialog_is_reported_as_the_trust_prompt(self) -> None:
        # Issue #327: a fresh project's first claude task stops at the workspace
        # trust dialog. The gate must say so (and how to clear it), not just
        # "boot gate".
        repo = Path(self._tmp.name) / "newproj"
        project = state.load_project(self.state_dir)
        project["repo"] = str(repo)
        state.save_project(self.state_dir, project)
        panes = iter([pane_fx.CLAUDE_TRUST_DIALOG, 'status\n❯ Try "help"\n'])
        with use_fake_mux(capture=list(panes)) as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver(agent="claude:opus")

        self.assertEqual(result, 0)
        gate = self._events()[0]
        self.assertEqual(gate["type"], "awaiting_orders")
        self.assertEqual(gate["gate"], "trust")
        self.assertIn("workspace trust prompt", gate["question"])
        self.assertIn(f"run `claude` once in {repo}", gate["question"])
        questions = (self.task_dir / "questions.md").read_text(encoding="utf-8")
        self.assertIn("workspace trust prompt", questions)

    def test_trust_prompt_notification_names_the_cause(self) -> None:
        panes = iter([pane_fx.CLAUDE_TRUST_DIALOG, 'status\n❯ Try "help"\n'])
        with (
            use_fake_mux(capture=list(panes)) as fake,
            patch("fleet.prompt_deliverer.notify.send") as send,
        ):
            fake.on["send_key"] = self._ack_on_enter
            self._deliver(agent="claude:opus")

        send.assert_called_once()
        self.assertIn("trust prompt", send.call_args.kwargs["title"])
        self.assertEqual(send.call_args.kwargs["level"], "error")

    def test_other_gates_keep_the_generic_boot_gate_message(self) -> None:
        panes = iter(
            ["Update available\n› 1. Update now\n  2. Skip this version\n", "›\n"]
        )
        with use_fake_mux(capture=list(panes)) as fake:
            fake.on["send_key"] = self._ack_on_enter
            self._deliver()

        gate = self._events()[0]
        self.assertEqual(gate["type"], "awaiting_orders")
        self.assertNotIn("gate", gate)
        self.assertIn("boot gate detected", gate["question"])

    def test_claude_unnumbered_dialog_is_gate_not_ready(self) -> None:
        # Real "Claude in Chrome extension detected" startup dialog: its
        # un-numbered cursor line matched the bare ready regex, so the pointer
        # used to be pasted into the dialog.
        dialog = (
            "  Claude in Chrome extension detected\n\n"
            "  ❯ No, keep browser tools off\n"
            "    Yes, use my browser\n\n"
            "  Enter to confirm · Esc to keep browser tools off\n"
        )
        panes = iter([dialog, dialog, 'status\n❯ Try "help"\n'])
        with use_fake_mux(capture=list(panes)) as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver(agent="claude:opus")

        self.assertEqual(result, 0)
        # only once the real prompt appeared
        self.assertEqual(len(fake.calls_named("paste")), 1)
        self.assertEqual(
            [e["type"] for e in self._events()],
            ["awaiting_orders", "inbox_seen", "prompt_delivered"],
        )

    def test_gate_emits_awaiting_orders_but_keeps_polling_until_ready(self) -> None:
        panes = iter(
            [
                "Do you trust the contents of this directory?\n› 1. Yes, continue\n",
                "all set\n›\n",
            ]
        )
        with use_fake_mux(capture=list(panes)) as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        events = self._events()
        self.assertEqual([e["type"] for e in events], ["awaiting_orders", "inbox_seen", "prompt_delivered"])
        self.assertEqual(state.load_task(self.state_dir, self.task_id)["status"], "running")
        # the pane is codex's trust dialog, which the gate now names outright
        self.assertIn("workspace trust prompt", (self.task_dir / "questions.md").read_text(encoding="utf-8"))

    def test_timeout_marks_failed_and_emits_error(self) -> None:
        with use_fake_mux(capture="booting..."):
            result = self._deliver(timeout=0.02)

        self.assertEqual(result, 1)
        self.assertEqual(state.load_task(self.state_dir, self.task_id)["status"], "failed")
        self.assertEqual(self._events()[-1]["type"], "error")
        self.assertIn("timed out", self._events()[-1]["message"])


READY_PANE = 'status\n❯ Try "help"\n'  # claude ready prompt
SESSIONS = {"fleet-demo": ["1·driver"]}
TAB_GONE = mux.MuxError("zellij tab not found (or has no terminal pane): fleet-demo:1·driver")


class TransientMuxErrorTests(unittest.TestCase):
    """Issue #289: one transient MuxError must not fail the delivery."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo")
        self.task_id = "1"
        self.task_dir = state.task_dir(self.state_dir, self.task_id)
        self.task_dir.mkdir(parents=True)
        self.prompt_path = self.task_dir / "driver-prompt.md"
        self.prompt_path.write_text("FULL-PROMPT-BODY-MARKER\n", encoding="utf-8")
        self._save_task("spawning")
        self._old_env = {k: os.environ.get(k) for k in ("FLEET_NO_NOTIFY", "FLEET_HOME")}
        os.environ["FLEET_NO_NOTIFY"] = "1"
        os.environ["FLEET_HOME"] = str(Path(self._tmp.name) / "fleet-home")
        self._sleep_patch = patch("fleet.prompt_deliverer.time.sleep", return_value=None)
        self._sleep_patch.start()
        self._stderr = contextlib.redirect_stderr(io.StringIO())
        self.log = self._stderr.__enter__()

    def tearDown(self) -> None:
        self._stderr.__exit__(None, None, None)
        self._sleep_patch.stop()
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def _save_task(self, status: str) -> None:
        state.save_task(
            self.state_dir,
            self.task_id,
            {
                "id": self.task_id,
                "status": status,
                "current_stage": 0,
                "stages": [{"role": "driver", "agent": "claude:opus", "status": "running"}],
            },
        )

    def _deliver(self, timeout: float = 5.0) -> int:
        return prompt_deliverer.deliver(
            state_dir=self.state_dir,
            task_id=self.task_id,
            session="fleet-demo",
            window="1·driver",
            prompt_path=self.prompt_path,
            agent_spec="claude:opus",
            timeout=timeout,
            poll_interval=0.01,
        )

    def _events(self) -> list[dict]:
        path = self.state_dir / "events.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def _ack_on_enter(self, *_args, **_kwargs) -> None:
        append_event(self.state_dir / "events.jsonl", "inbox_seen", task_id=self.task_id, watermark=None)

    def _status(self) -> str:
        return state.load_task(self.state_dir, self.task_id)["status"]

    def _enable_leader_push(self) -> None:
        project = state.load_project(self.state_dir)
        project["notify_leader_on_driver_done"] = "true"
        state.save_project(self.state_dir, project)

    # -- transient errors are retried -------------------------------------

    def test_transient_capture_error_is_retried_until_ready(self) -> None:
        with use_fake_mux(sessions=SESSIONS, capture=READY_PANE) as fake:
            fake.fail_next("capture", TAB_GONE, times=3)
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual(len(fake.calls_named("capture")), 4)  # 3 failures + 1 success
        self.assertEqual(len(fake.calls_named("paste")), 1)
        self.assertEqual([e["type"] for e in self._events()], ["inbox_seen", "prompt_delivered"])
        self.assertEqual(self._status(), "spawning")  # never marked failed
        self.assertIn("retrying with backoff", self.log.getvalue())
        self.assertIn("capture recovered", self.log.getvalue())

    def test_transient_paste_error_is_retried(self) -> None:
        # The exact #289 failure: capture fine (is_ready), then paste hits
        # "tab not found". The paste is retried; the submit Enter is pressed once.
        with use_fake_mux(sessions=SESSIONS, capture=READY_PANE) as fake:
            fake.fail_next("paste", TAB_GONE, times=2)
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual(len(fake.calls_named("paste")), 3)  # 2 failures + 1 success
        self.assertEqual(len(fake.calls_named("send_key")), 1)  # a single submit Enter
        self.assertEqual([e["type"] for e in self._events()], ["inbox_seen", "prompt_delivered"])
        self.assertNotEqual(self._status(), "failed")

    def test_transient_submit_enter_error_is_retried_without_repasting(self) -> None:
        with use_fake_mux(sessions=SESSIONS, capture=READY_PANE) as fake:
            fake.fail_next("send_key", TAB_GONE, times=1)
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual(len(fake.calls_named("paste")), 1)  # the pointer is never re-pasted
        self.assertEqual(len(fake.calls_named("send_key")), 2)
        self.assertEqual(self._events()[-1]["type"], "prompt_delivered")

    def test_transient_error_with_one_false_session_check_is_still_retried(self) -> None:
        # zellij session_exists folds its own errors into False: a single False
        # while the mux is inconsistent must not be read as "session gone".
        checks = {"n": 0}

        with use_fake_mux(capture=READY_PANE) as fake:  # session not listed at first

            def flaky_session_exists(*_a, **_k) -> None:
                checks["n"] += 1
                if checks["n"] >= 2:
                    fake.sessions["fleet-demo"] = ["1·driver"]

            fake.on["session_exists"] = flaky_session_exists
            fake.fail_next("capture", TAB_GONE, times=1)
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertGreaterEqual(checks["n"], 2)
        self.assertEqual(self._events()[-1]["type"], "prompt_delivered")

    # -- ... but not forever ---------------------------------------------

    def test_persistent_capture_error_fails_at_the_deadline(self) -> None:
        with use_fake_mux(sessions=SESSIONS, capture=READY_PANE) as fake:
            fake.fail["capture"] = TAB_GONE
            result = self._deliver(timeout=0.05)

        self.assertEqual(result, 1)
        self.assertGreater(len(fake.calls_named("capture")), 1)  # it did retry
        self.assertEqual(self._status(), "failed")
        error = self._events()[-1]
        self.assertEqual(error["type"], "error")
        self.assertIn("still failing", error["message"])
        self.assertIn("tab not found", error["message"])

    def test_persistent_paste_error_fails_at_the_deadline(self) -> None:
        with use_fake_mux(sessions=SESSIONS, capture=READY_PANE) as fake:
            fake.fail["paste"] = TAB_GONE
            result = self._deliver(timeout=0.05)

        self.assertEqual(result, 1)
        self.assertGreater(len(fake.calls_named("paste")), 1)
        self.assertEqual(self._status(), "failed")
        error = self._events()[-1]
        self.assertIn("cannot paste prompt", error["message"])
        self.assertIn("still failing", error["message"])

    def test_session_really_gone_fails_immediately(self) -> None:
        with use_fake_mux(sessions={}, capture=READY_PANE) as fake:
            fake.fail["capture"] = TAB_GONE
            result = self._deliver(timeout=60.0)

        self.assertEqual(result, 1)
        self.assertEqual(len(fake.calls_named("capture")), 1)  # no retry loop
        self.assertEqual(self._status(), "failed")
        self.assertIn("is gone", self._events()[-1]["message"])

    def test_session_gone_during_paste_fails_immediately(self) -> None:
        with use_fake_mux(sessions={}, capture=READY_PANE) as fake:
            fake.fail["paste"] = TAB_GONE
            result = self._deliver(timeout=60.0)

        self.assertEqual(result, 1)
        self.assertEqual(len(fake.calls_named("paste")), 1)
        self.assertIn("cannot paste prompt", self._events()[-1]["message"])
        self.assertIn("is gone", self._events()[-1]["message"])

    # -- recovery of a task the deliverer itself failed ------------------

    def _fail_via_deliverer(self) -> None:
        prompt_deliverer._fail(
            self.state_dir, self.task_id, "prompt deliverer cannot paste prompt: x", "1·driver"
        )

    def test_successful_delivery_revives_a_task_the_deliverer_failed(self) -> None:
        self._fail_via_deliverer()
        self.assertEqual(self._status(), "failed")
        with use_fake_mux(sessions=SESSIONS, capture=READY_PANE) as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        # Derived from the stages (a running stage), not blindly "running".
        self.assertEqual(self._status(), "running")
        types = [e["type"] for e in self._events()]
        self.assertEqual(types[-3:], ["inbox_seen", "prompt_delivery_recovered", "prompt_delivered"])
        recovered = [e for e in self._events() if e["type"] == "prompt_delivery_recovered"][0]
        self.assertEqual(recovered["previous_status"], "failed")
        self.assertEqual(recovered["status"], "running")

    def test_failed_status_from_another_cause_is_not_revived(self) -> None:
        self._save_task("failed")
        append_event(
            self.state_dir / "events.jsonl",
            "error",
            task_id=self.task_id,
            source="somewhere-else",
            message="x",
        )
        with use_fake_mux(sessions=SESSIONS, capture=READY_PANE) as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual(self._status(), "failed")
        self.assertNotIn("prompt_delivery_recovered", [e["type"] for e in self._events()])

    def test_delivery_without_a_prior_failure_emits_no_recovery_event(self) -> None:
        with use_fake_mux(sessions=SESSIONS, capture=READY_PANE) as fake:
            fake.on["send_key"] = self._ack_on_enter
            self._deliver()

        self.assertNotIn("prompt_delivery_recovered", [e["type"] for e in self._events()])

    # -- failure reaches the owning leader --------------------------------

    def _queue(self) -> list[dict]:
        return leader_notifier.read_queue(state.session_dir("main"))

    def test_failure_is_pushed_to_the_leader_when_push_is_on(self) -> None:
        self._enable_leader_push()
        with (
            use_fake_mux(sessions=SESSIONS | {"fleet-main": ["leader"]}, capture="booting..."),
            patch("fleet.leader_notifier.start_detached") as spawn,
            patch.object(
                leader_notifier.formation,
                "read_leader_session",
                return_value={"agent": "claude:opus"},
            ),
        ):
            result = self._deliver(timeout=0.02)

        self.assertEqual(result, 1)
        spawn.assert_called_once()
        (rec,) = self._queue()
        self.assertEqual(rec["kind"], leader_notifier.KIND_DELIVERY_FAILED)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(rec["task_id"], self.task_id)
        self.assertIn("timed out", rec["summary"])
        self.assertIsNone(rec["pr_url"])  # no PR lookup for a delivery failure
        block = leader_notifier.render_block([rec])
        self.assertIn("[delivery failed]", block)
        self.assertIn("fleet-agent send-prompt 1 --project demo", block)

    def test_failure_is_not_pushed_when_push_is_off(self) -> None:
        with (
            use_fake_mux(sessions=SESSIONS, capture="booting..."),
            patch("fleet.leader_notifier.start_detached") as spawn,
        ):
            result = self._deliver(timeout=0.02)

        self.assertEqual(result, 1)
        spawn.assert_not_called()
        self.assertEqual(self._queue(), [])
        self.assertEqual(self._status(), "failed")  # the failure itself is unchanged

    def test_push_trouble_never_breaks_the_failure_report(self) -> None:
        self._enable_leader_push()
        with (
            use_fake_mux(sessions=SESSIONS, capture="booting..."),
            patch("fleet.leader_notifier.push_to_leader", side_effect=RuntimeError("boom")),
        ):
            result = self._deliver(timeout=0.02)

        self.assertEqual(result, 1)
        self.assertEqual(self._status(), "failed")
        self.assertEqual(self._events()[-1]["type"], "error")


if __name__ == "__main__":
    unittest.main()
