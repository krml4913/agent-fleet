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

    def tearDown(self) -> None:
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

    def test_ready_marker_pastes_pointer_and_emits_event(self) -> None:
        with (
            patch("fleet.prompt_deliverer.tmux.capture_pane", return_value="ready\n›\n"),
            patch("fleet.prompt_deliverer.tmux.load_buffer") as load_buffer,
            patch("fleet.prompt_deliverer.tmux.paste_buffer") as paste_buffer,
            patch("fleet.prompt_deliverer.tmux.send_keys") as send_keys,
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
            patch("fleet.prompt_deliverer.tmux.send_keys"),
        ):
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual([e["type"] for e in self._events()], ["prompt_delivered"])

    def test_codex_blocking_update_menu_emits_needs_input(self) -> None:
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
            patch("fleet.prompt_deliverer.tmux.send_keys"),
        ):
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual(
            [e["type"] for e in self._events()],
            ["needs_input", "prompt_delivered"],
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
            patch("fleet.prompt_deliverer.tmux.send_keys"),
        ):
            result = self._deliver()

        self.assertEqual(result, 0)
        self.assertEqual(
            [e["type"] for e in self._events()],
            ["needs_input", "prompt_delivered"],
        )

    def test_claude_ready_marker_matches_current_tui_prompt(self) -> None:
        with (
            patch("fleet.prompt_deliverer.tmux.capture_pane", return_value='status\n❯ Try "help"\n'),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys"),
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
            patch("fleet.prompt_deliverer.tmux.send_keys"),
        ):
            result = self._deliver(agent="claude:opus")

        self.assertEqual(result, 0)
        self.assertEqual(
            [e["type"] for e in self._events()],
            ["needs_input", "prompt_delivered"],
        )

    def test_gate_emits_needs_input_but_keeps_polling_until_ready(self) -> None:
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
            patch("fleet.prompt_deliverer.tmux.send_keys"),
        ):
            result = self._deliver()

        self.assertEqual(result, 0)
        events = self._events()
        self.assertEqual([e["type"] for e in events], ["needs_input", "prompt_delivered"])
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
