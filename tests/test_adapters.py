"""The adapter registry drives every former vendor hardcode point.

Registering one fake adapter must make a brand-new vendor visible at all
three places that used to hardcode claude/codex: ``agents.parse_spec``,
``agents.cli_command`` and the prompt deliverer's ready/gate detection.
That proves "1 vendor = 1 file" — no scattered edits required.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import adapters, agents, prompt_deliverer, state  # noqa: E402
from fleet.adapters.base import VendorAdapter  # noqa: E402
from fleet.events import append_event  # noqa: E402


class FakeAdapter(VendorAdapter):
    name = "fake"
    ready = re.compile(r"(?m)^FAKE-READY$")
    gate = re.compile(r"(?m)^FAKE-GATE$")

    @classmethod
    def cli_command(cls, model: str) -> list[str]:
        return ["fake-cli", "--model", model]


class RegistryDrivesVendorsTests(unittest.TestCase):
    def setUp(self) -> None:
        adapters.REGISTRY["fake"] = FakeAdapter
        self.addCleanup(adapters.REGISTRY.pop, "fake", None)

    def test_supported_vendors_derives_from_registry(self) -> None:
        self.assertEqual(agents.SUPPORTED_VENDORS, frozenset(adapters.REGISTRY))
        self.assertIn("fake", agents.SUPPORTED_VENDORS)

    def test_parse_spec_accepts_registered_vendor(self) -> None:
        self.assertEqual(agents.parse_spec("fake:m1"), ("fake", "m1"))

    def test_cli_command_uses_adapter(self) -> None:
        self.assertEqual(agents.cli_command("fake:m1"), ["fake-cli", "--model", "m1"])


class SessionNamingTests(unittest.TestCase):
    """Session-naming branches in the adapter layer (claude vs codex)."""

    def test_claude_names_at_launch(self) -> None:
        self.assertEqual(
            adapters.ClaudeAdapter.session_name_launch_args("x"),
            ["--name", "x"],
        )
        self.assertEqual(adapters.ClaudeAdapter.session_rename_keys("x"), [])

    def test_codex_names_via_rename_keys(self) -> None:
        self.assertEqual(adapters.CodexAdapter.session_name_launch_args("x"), [])
        self.assertEqual(
            adapters.CodexAdapter.session_rename_keys("x"),
            [
                ("/rename", False),
                ("", True),
                ("C-u", False),
                ("x", False),
                ("", True),
            ],
        )

    def test_base_default_is_noop(self) -> None:
        self.assertEqual(VendorAdapter.session_name_launch_args("x"), [])
        self.assertEqual(VendorAdapter.session_rename_keys("x"), [])

    def test_submit_retries_live_only_in_the_codex_adapter(self) -> None:
        # The submit-Enter retry is a codex quirk (it drops the bare submit
        # Enter); the policy lives in the adapter so the deliverer stays
        # vendor-agnostic. claude and the base submit exactly once.
        self.assertEqual(VendorAdapter.submit_retries, 0)
        self.assertEqual(adapters.ClaudeAdapter.submit_retries, 0)
        self.assertGreater(adapters.CodexAdapter.submit_retries, 0)
        self.assertGreater(adapters.CodexAdapter.submit_retry_interval_seconds, 0)

    def test_agents_wrappers_resolve_vendor(self) -> None:
        self.assertEqual(
            agents.session_name_launch_args("claude:opus", "n"),
            ["--name", "n"],
        )
        self.assertEqual(agents.session_rename_keys("claude:opus", "n"), [])
        self.assertEqual(agents.session_name_launch_args("codex:gpt-5.5", "n"), [])
        self.assertEqual(
            agents.session_rename_keys("codex:gpt-5.5", "n"),
            [
                ("/rename", False),
                ("", True),
                ("C-u", False),
                ("n", False),
                ("", True),
            ],
        )


class DelivererUsesRegistryTests(unittest.TestCase):
    """The deliverer's readiness detection comes from the registered adapter."""

    def setUp(self) -> None:
        adapters.REGISTRY["fake"] = FakeAdapter
        self.addCleanup(adapters.REGISTRY.pop, "fake", None)

        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo")
        self.task_id = "1"
        self.task_dir = state.task_dir(self.state_dir, self.task_id)
        self.task_dir.mkdir(parents=True)
        self.prompt_path = self.task_dir / "driver-prompt.md"
        self.prompt_path.write_text("PROMPT\n")
        state.save_task(
            self.state_dir,
            self.task_id,
            {
                "id": self.task_id,
                "status": "spawning",
                "current_stage": 0,
                "stages": [{"role": "driver", "agent": "fake:m1", "status": "running"}],
            },
        )
        self._old_no_notify = os.environ.get("FLEET_NO_NOTIFY")
        os.environ["FLEET_NO_NOTIFY"] = "1"
        self.addCleanup(self._restore_notify)
        sleep_patch = patch("fleet.prompt_deliverer.time.sleep", return_value=None)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def _restore_notify(self) -> None:
        if self._old_no_notify is None:
            os.environ.pop("FLEET_NO_NOTIFY", None)
        else:
            os.environ["FLEET_NO_NOTIFY"] = self._old_no_notify

    def _ack_on_enter(self, *_args, **_kwargs) -> None:
        append_event(
            self.state_dir / "events.jsonl",
            "inbox_seen",
            task_id=self.task_id,
            watermark=None,
        )

    def _events(self) -> list[dict]:
        path = self.state_dir / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def test_fake_adapter_ready_marker_drives_delivery(self) -> None:
        with (
            patch("fleet.prompt_deliverer.tmux.capture_pane", return_value="FAKE-READY\n"),
            patch("fleet.prompt_deliverer.tmux.load_buffer"),
            patch("fleet.prompt_deliverer.tmux.paste_buffer"),
            patch("fleet.prompt_deliverer.tmux.send_keys", side_effect=self._ack_on_enter),
        ):
            result = prompt_deliverer.deliver(
                state_dir=self.state_dir,
                task_id=self.task_id,
                session="fleet-demo",
                window="1·driver",
                prompt_path=self.prompt_path,
                buffer_name="fleet-task-1",
                agent_spec="fake:m1",
                timeout=1.0,
                poll_interval=0.01,
            )

        self.assertEqual(result, 0)
        self.assertEqual(self._events()[-1]["type"], "prompt_delivered")


if __name__ == "__main__":
    unittest.main()
