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

from fleet import prompt_deliverer, prompt_pointer, state  # noqa: E402
from fleet.events import append_event  # noqa: E402


class PromptDelivererTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo")
        self.task_id = "1"
        self.task_dir = state.task_dir(self.state_dir, self.task_id)
        self.task_dir.mkdir(parents=True)
        self.prompt_path = self.task_dir / "driver-prompt.md"
        self.prompt_path.write_text("FULL-PROMPT-BODY-MARKER\n")
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
            buffer_name="fleet-task-1",
            agent_spec=agent,
            timeout=timeout,
            poll_interval=0.01,
        )

    def _events(self) -> list[dict]:
        path = self.state_dir / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def _ack_on_enter(self, *_args, **_kwargs) -> None:
        append_event(
            self.state_dir / "events.jsonl",
            "inbox_seen",
            task_id=self.task_id,
            watermark=None,
        )

    def test_ready_marker_pastes_pointer_and_emits_event(self) -> None:
        with (
            patch("fleet.prompt_deliverer.tmux.capture_pane", return_value="ready\n›\n"),
            patch("fleet.prompt_deliverer.tmux.load_buffer") as load_buffer,
            patch("fleet.prompt_deliverer.tmux.paste_buffer") as paste_buffer,
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=self._ack_on_enter) as send_keys,
        ):
            result = self._deliver()

        self.assertEqual(result, 0)
        # The deliverer must paste a pointer to the prompt file, never the
        # prompt body (full-content paste regresses Issue #90).
        load_buffer.assert_called_once()
        buf_name, loaded = load_buffer.call_args.args
        self.assertEqual(buf_name, "fleet-task-1")
        loaded_path = Path(loaded)
        self.assertEqual(loaded_path, prompt_pointer.pointer_path(self.prompt_path))
        pointer = loaded_path.read_text(encoding="utf-8")
        self.assertIn(str(self.prompt_path.resolve()), pointer)
        self.assertNotIn("FULL-PROMPT-BODY-MARKER", pointer)
        paste_buffer.assert_called_once_with("fleet-demo", "1·driver", "fleet-task-1")
        send_keys.assert_called_once_with("fleet-demo", "1·driver", "", enter=True)
        self.assertEqual(self._events()[-1]["type"], "prompt_delivered")

    def test_codex_session_name_renames_before_paste(self) -> None:
        calls: list[tuple] = []

        def record(session, window, text, *, enter=True):
            calls.append(("send_keys", text))
            self._ack_on_enter()

        with (
            patch("fleet.prompt_deliverer.tmux.capture_pane", return_value="ready\n›\n"),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch(
                "fleet.prompt_deliverer.tmux.paste_buffer",
                side_effect=lambda *a, **k: calls.append(("paste", None)),
            ),
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=record),
        ):
            result = prompt_deliverer.deliver(
                state_dir=self.state_dir,
                task_id=self.task_id,
                session="fleet-demo",
                window="1·driver",
                prompt_path=self.prompt_path,
                buffer_name="fleet-task-1",
                agent_spec="codex:o4-mini",
                session_name="demo-1-driver",
                timeout=1.0,
                poll_interval=0.01,
            )

        self.assertEqual(result, 0)
        # rename keystrokes come first (open popup, clear field, type name,
        # confirm), then the paste, then the submit Enter.
        self.assertEqual(
            calls,
            [
                ("send_keys", "/rename"),
                ("send_keys", ""),
                ("send_keys", "C-u"),
                ("send_keys", "demo-1-driver"),
                ("send_keys", ""),
                ("paste", None),
                ("send_keys", ""),
            ],
        )

    def test_claude_session_name_does_not_send_rename_keys(self) -> None:
        with (
            patch("fleet.prompt_deliverer.tmux.capture_pane", return_value='status\n❯ Try "help"\n'),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=self._ack_on_enter) as send_keys,
        ):
            result = prompt_deliverer.deliver(
                state_dir=self.state_dir,
                task_id=self.task_id,
                session="fleet-demo",
                window="1·driver",
                prompt_path=self.prompt_path,
                buffer_name="fleet-task-1",
                agent_spec="claude:opus",
                session_name="demo-1-driver",
                timeout=1.0,
                poll_interval=0.01,
            )

        self.assertEqual(result, 0)
        # claude is named at launch → only the submit Enter, no rename keys.
        send_keys.assert_called_once_with("fleet-demo", "1·driver", "", enter=True)

    def test_does_not_retry_enter_while_waiting_for_ack(self) -> None:
        with (
            patch("fleet.prompt_deliverer.tmux.capture_pane", return_value="ready\n›\n"),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=self._ack_on_enter) as send_keys,
        ):
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual(send_keys.call_count, 1)
        send_keys.assert_called_with("fleet-demo", "1·driver", "", enter=True)
        self.assertEqual(self._events()[-1]["type"], "prompt_delivered")

    def test_ack_from_other_task_does_not_confirm_delivery(self) -> None:
        def ack_other_task(*_args, **_kwargs) -> None:
            append_event(
                self.state_dir / "events.jsonl",
                "inbox_seen",
                task_id="2",
                watermark=None,
            )

        with (
            patch("fleet.prompt_deliverer.tmux.capture_pane", return_value="ready\n›\n"),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=ack_other_task) as send_keys,
        ):
            result = self._deliver(timeout=0.02)

        self.assertEqual(result, 1)
        self.assertEqual(send_keys.call_count, 1)
        self.assertEqual(state.load_task(self.state_dir, self.task_id)["status"], "failed")
        self.assertEqual(self._events()[-1]["type"], "error")
        self.assertIn("inbox_seen ack", self._events()[-1]["message"])

    def test_missing_ack_marks_failed(self) -> None:
        with (
            patch("fleet.prompt_deliverer.tmux.capture_pane", return_value="ready\n›\n"),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys") as send_keys,
        ):
            result = self._deliver(timeout=0.02)

        self.assertEqual(result, 1)
        self.assertEqual(send_keys.call_count, 1)
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
        with (
            patch("fleet.prompt_deliverer.tmux.capture_pane", return_value=pane),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=self._ack_on_enter),
        ):
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
        with (
            patch(
                "fleet.prompt_deliverer.tmux.capture_pane",
                side_effect=lambda *_a, **_k: next(panes),
            ),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=self._ack_on_enter),
        ):
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
        with (
            patch(
                "fleet.prompt_deliverer.tmux.capture_pane",
                side_effect=lambda *_a, **_k: next(panes),
            ),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=self._ack_on_enter),
        ):
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual(
            [e["type"] for e in self._events()],
            ["awaiting_orders", "inbox_seen", "prompt_delivered"],
        )

    def test_claude_ready_marker_matches_current_tui_prompt(self) -> None:
        with (
            patch("fleet.prompt_deliverer.tmux.capture_pane", return_value='status\n❯ Try "help"\n'),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=self._ack_on_enter),
        ):
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
        with (
            patch(
                "fleet.prompt_deliverer.tmux.capture_pane",
                side_effect=lambda *_a, **_k: next(panes),
            ),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=self._ack_on_enter),
        ):
            result = self._deliver(agent="claude:opus")

        self.assertEqual(result, 0)
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
        with (
            patch(
                "fleet.prompt_deliverer.tmux.capture_pane",
                side_effect=lambda *_a, **_k: next(panes),
            ),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=self._ack_on_enter),
        ):
            result = self._deliver()

        self.assertEqual(result, 0)
        events = self._events()
        self.assertEqual([e["type"] for e in events], ["awaiting_orders", "inbox_seen", "prompt_delivered"])
        self.assertEqual(state.load_task(self.state_dir, self.task_id)["status"], "running")
        self.assertIn("boot gate detected", (self.task_dir / "questions.md").read_text())

    def test_timeout_marks_failed_and_emits_error(self) -> None:
        with patch("fleet.prompt_deliverer.tmux.capture_pane", return_value="booting..."):
            result = self._deliver(timeout=0.02)

        self.assertEqual(result, 1)
        self.assertEqual(state.load_task(self.state_dir, self.task_id)["status"], "failed")
        self.assertEqual(self._events()[-1]["type"], "error")
        self.assertIn("timed out", self._events()[-1]["message"])


if __name__ == "__main__":
    unittest.main()
