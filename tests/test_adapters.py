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
import tests._fleet_test_helpers  # noqa: E402,F401  (hermetic env: FLEET_NO_NOTIFY / FLEET_NO_MUX)
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import adapters, agents, prompt_deliverer, state  # noqa: E402
from fleet.adapters.base import Key, VendorAdapter  # noqa: E402
from fleet.events import append_event  # noqa: E402
from tests import _pane_fixtures as pane_fx  # noqa: E402
from tests._fake_mux import use_fake_mux  # noqa: E402


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
                (Key("Ctrl-u"), False),
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
                (Key("Ctrl-u"), False),
                ("n", False),
                ("", True),
            ],
        )


RULE = "─" * 120

# Real claude startup dialog captured via dump-screen (120 cols).
CLAUDE_CHROME_DIALOG = f"""{RULE}
  Claude in Chrome extension detected

  Claude will use your Chrome browser by default — navigating sites, filling forms, and capturing screenshots in your
  existing session.

  This session is in Auto mode, so an AI classifier approves routine browser actions — you are only prompted when it
  is unsure. Turn browser tools off for future sessions with /chrome.

  ❯ No, keep browser tools off
    Yes, use my browser

  Enter to confirm · Esc to keep browser tools off

"""

# The same dialog without its footer: the cursor/sibling structure alone flags it.
CLAUDE_MENU_NO_FOOTER = "  Pick one\n\n  ❯ No, keep browser tools off\n    Yes, use my browser\n"

CLAUDE_READY = 'status\n❯ Try "help"\n'

# A realistic idle claude screen: prompt box at column 0 plus status line.
CLAUDE_IDLE_SCREEN = (
    "● Done. The login flow now handles authentication errors.\n\n"
    f"{RULE}\n❯ \n{RULE}\n  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
)


class DialogDetectionTests(unittest.TestCase):
    """Selection menus must not count as ready and must report as a gate."""

    C = adapters.ClaudeAdapter
    X = adapters.CodexAdapter

    def test_real_chrome_dialog_is_not_ready_and_is_gated(self) -> None:
        # The bare regex is fooled by the cursor line — that's the bug.
        self.assertTrue(self.C.ready.search(CLAUDE_CHROME_DIALOG))
        self.assertFalse(self.C.is_ready(CLAUDE_CHROME_DIALOG))
        self.assertTrue(self.C.is_dialog(CLAUDE_CHROME_DIALOG))
        self.assertTrue(self.C.is_gated(CLAUDE_CHROME_DIALOG))

    def test_unnumbered_menu_without_footer_is_detected(self) -> None:
        self.assertFalse(self.C.is_ready(CLAUDE_MENU_NO_FOOTER))
        self.assertTrue(self.C.is_gated(CLAUDE_MENU_NO_FOOTER))

    def test_normal_prompts_stay_ready_and_ungated(self) -> None:
        for pane in (CLAUDE_READY, CLAUDE_IDLE_SCREEN, "❯ \n"):
            with self.subTest(pane=pane):
                self.assertTrue(self.C.is_ready(pane))
                self.assertFalse(self.C.is_dialog(pane))

    def test_multiline_composer_input_is_not_a_dialog(self) -> None:
        # Continuation lines of typed input are indented under a column-0 ❯.
        pane = "❯ first line of a message\n  second line of it\n"
        self.assertTrue(self.C.is_ready(pane))

    def test_login_in_history_does_not_veto_readiness(self) -> None:
        pane = (
            "user: please fix the login page\n"
            "● I updated the authentication handler; log in works now.\n"
            + CLAUDE_READY
        )
        self.assertTrue(self.C.gate.search(pane))  # broad gate still matches…
        self.assertTrue(self.C.is_ready(pane))  # …but does not veto readiness

    def test_dialog_text_in_history_above_prompt_does_not_veto(self) -> None:
        # A dialog that was dismissed and scrolled into history (or quoted in
        # the conversation) sits above the live prompt → still ready.
        pane = CLAUDE_CHROME_DIALOG + "● ok\n" + CLAUDE_IDLE_SCREEN
        self.assertTrue(self.C.is_ready(pane))

    def test_numbered_menus_still_gated(self) -> None:
        claude = "Do you trust this workspace?\n❯ 1. Yes, proceed\n  2. No\n"
        self.assertFalse(self.C.is_ready(claude))
        self.assertTrue(self.C.is_gated(claude))
        codex = "Update available\n› 1. Update now\n  2. Skip this version\n"
        self.assertFalse(self.X.is_ready(codex))
        self.assertTrue(self.X.is_gated(codex))

    def test_codex_unnumbered_menu_and_ready(self) -> None:
        menu = "  Choose\n  › Keep current setting\n    Change it\n"
        self.assertFalse(self.X.is_ready(menu))
        self.assertTrue(self.X.is_gated(menu))
        self.assertTrue(self.X.is_ready("ready\n›\n"))
        self.assertFalse(self.X.is_gated("ready\n›\n"))

    def test_busy_hint_is_not_a_dialog_footer(self) -> None:
        self.assertFalse(self.C.is_dialog("  esc to interrupt\n❯ \n"))

    def test_base_without_cursor_uses_footer_only(self) -> None:
        self.assertFalse(FakeAdapter.is_dialog("  > a\n    b\n"))
        self.assertTrue(FakeAdapter.is_dialog("FAKE-READY\n  Esc to cancel\n"))
        self.assertFalse(FakeAdapter.is_ready("FAKE-READY\n  Esc to cancel\n"))
        self.assertTrue(FakeAdapter.is_ready("FAKE-READY\n"))


class BusyDetectionTests(unittest.TestCase):
    """``is_busy`` / ``is_idle``: a visible ``❯`` composer does not mean idle (Issue #288)."""

    C = adapters.ClaudeAdapter
    X = adapters.CodexAdapter

    def test_real_busy_claude_screen_is_ready_but_busy(self) -> None:
        # The bug: the composer is on screen mid-turn, so ``is_ready`` alone is True.
        self.assertTrue(self.C.is_ready(pane_fx.CLAUDE_BUSY))
        self.assertTrue(self.C.is_busy(pane_fx.CLAUDE_BUSY))
        self.assertFalse(self.C.is_idle(pane_fx.CLAUDE_BUSY))

    def test_interrupt_hint_layout_is_busy(self) -> None:
        self.assertTrue(self.C.is_busy(pane_fx.CLAUDE_BUSY_ESC_HINT))
        self.assertFalse(self.C.is_idle(pane_fx.CLAUDE_BUSY_ESC_HINT))
        footer_hint = pane_fx.CLAUDE_IDLE.replace("(shift+tab to cycle)", "(shift+tab to cycle) · esc to interrupt")
        self.assertTrue(self.C.is_busy(footer_hint))

    def test_turn_boundary_screen_is_idle(self) -> None:
        for pane in (pane_fx.CLAUDE_IDLE, CLAUDE_IDLE_SCREEN, CLAUDE_READY, "❯ \n"):
            with self.subTest(pane=pane):
                self.assertTrue(self.C.is_ready(pane))
                self.assertFalse(self.C.is_busy(pane))
                self.assertTrue(self.C.is_idle(pane))

    def test_every_spinner_glyph_frame_is_busy(self) -> None:
        for glyph in "·✢✳✶✻✽":
            with self.subTest(glyph=glyph):
                pane = pane_fx.CLAUDE_BUSY.replace("✢ Frosting…", f"{glyph} Frosting…")
                self.assertTrue(self.C.is_busy(pane))

    def test_prose_and_finished_turn_lines_are_not_busy(self) -> None:
        for line in (
            "✻ Cooked for 1m 3s",
            "● The footer says esc to interrupt while a turn runs.",
            "  ⎿  + busy = re.compile(r\"esc to interrupt\")",
            "● Waiting for CI… then I will merge.",  # ``●`` is not a spinner glyph
        ):
            with self.subTest(line=line):
                pane = pane_fx.CLAUDE_IDLE.replace("✻ Cooked for 48s", line)
                self.assertFalse(self.C.is_busy(pane))
                self.assertTrue(self.C.is_idle(pane))

    def test_busy_hint_far_above_the_tail_is_ignored(self) -> None:
        stale = "✻ Thinking… (esc to interrupt)\n" + "".join(f"line {i}\n" for i in range(40))
        self.assertFalse(self.C.is_busy(stale + pane_fx.CLAUDE_IDLE))

    def test_dialog_is_neither_ready_nor_idle(self) -> None:
        self.assertFalse(self.C.is_idle(CLAUDE_CHROME_DIALOG))

    def test_no_prompt_is_not_idle(self) -> None:
        self.assertFalse(self.C.is_idle("✻ Thinking… (esc to interrupt)\n"))

    def test_vendor_without_busy_pattern_is_never_busy(self) -> None:
        self.assertIsNone(VendorAdapter.busy)
        self.assertFalse(FakeAdapter.is_busy("✻ Thinking… (esc to interrupt)\nFAKE-READY\n"))
        self.assertTrue(FakeAdapter.is_idle("FAKE-READY\n"))

    def test_codex_working_hint_is_busy(self) -> None:
        self.assertTrue(self.X.is_ready(pane_fx.CODEX_BUSY))
        self.assertTrue(self.X.is_busy(pane_fx.CODEX_BUSY))
        self.assertFalse(self.X.is_idle(pane_fx.CODEX_BUSY))
        self.assertTrue(self.X.is_idle(pane_fx.CODEX_IDLE))


class ComposerHoldsTests(unittest.TestCase):
    """``composer_holds``: is the injected text still typed-but-unsent in the composer?"""

    C = adapters.ClaudeAdapter
    X = adapters.CodexAdapter
    TEXT = (
        "[fleet] 2 driver notification(s) — for each: pull the diff and run the gate. "
        "Skip any task already completed+merged. :: task-a [completed] task-a done "
        "branch=fleet/task/a PR=https://github.com/o/r/pull/1"
    )

    def test_text_left_in_the_composer_is_detected_across_wraps(self) -> None:
        for width in (60, 100, 150):
            with self.subTest(width=width):
                pane = pane_fx.claude_stuck_composer(self.TEXT, width=width)
                self.assertTrue(self.C.composer_holds(pane, self.TEXT))

    def test_submitted_text_echoed_into_history_is_not_in_the_composer(self) -> None:
        self.assertFalse(self.C.composer_holds(pane_fx.claude_submitted_echo(self.TEXT), self.TEXT))

    def test_empty_composer_and_missing_prompt(self) -> None:
        self.assertFalse(self.C.composer_holds(pane_fx.CLAUDE_BUSY, self.TEXT))
        self.assertFalse(self.C.composer_holds("no prompt here\n", self.TEXT))
        self.assertFalse(self.C.composer_holds(pane_fx.CLAUDE_BUSY, ""))

    def test_other_text_in_the_composer_is_not_ours(self) -> None:
        pane = pane_fx.claude_stuck_composer("please also update the README")
        self.assertFalse(self.C.composer_holds(pane, self.TEXT))

    def test_collapsed_paste_placeholder_counts(self) -> None:
        pane = pane_fx.CLAUDE_BUSY.replace("❯ ", "❯ [Pasted text #1 +0 lines]")
        self.assertTrue(self.C.composer_holds(pane, self.TEXT))
        self.assertFalse(self.X.composer_holds(pane_fx.CODEX_IDLE, self.TEXT))

    def test_codex_composer(self) -> None:
        pane = pane_fx.CODEX_BUSY.replace("Find and fix a bug in @filename", self.TEXT)
        self.assertTrue(self.X.composer_holds(pane, self.TEXT))


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
        self.prompt_path.write_text("PROMPT\n", encoding="utf-8")
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
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_fake_adapter_ready_marker_drives_delivery(self) -> None:
        with use_fake_mux(capture="FAKE-READY\n") as fake:
            fake.on["send_key"] = self._ack_on_enter
            result = prompt_deliverer.deliver(
                state_dir=self.state_dir,
                task_id=self.task_id,
                session="fleet-demo",
                window="1·driver",
                prompt_path=self.prompt_path,
                agent_spec="fake:m1",
                timeout=1.0,
                poll_interval=0.01,
            )

        self.assertEqual(result, 0)
        self.assertEqual(self._events()[-1]["type"], "prompt_delivered")


if __name__ == "__main__":
    unittest.main()
