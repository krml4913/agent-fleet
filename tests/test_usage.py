"""Per-task token usage recording (Issue #209).

The seam is ``VendorAdapter.usage_from_session``: each vendor reads its OWN
already-written session log (read-side only, no daemon, no polling) and the
terminal-transition writers fold the RAW token total into the task's
``task.yaml`` via ``state.record_task_usage`` → ``save_task``. These tests
cover claude parsing, codex parsing, graceful degradation to an absent block,
the base default, and the wiring at the terminal transition.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import agents, orchestrator, prompt_deliverer, state  # noqa: E402
from fleet.adapters import ClaudeAdapter, CodexAdapter  # noqa: E402
from fleet.adapters.base import VendorAdapter  # noqa: E402


def _escape_cwd(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _write_claude_log(home: Path, cwd: str, lines: list[dict]) -> Path:
    proj = home / ".claude" / "projects" / _escape_cwd(cwd)
    proj.mkdir(parents=True, exist_ok=True)
    log = proj / "5c49bf3a.jsonl"
    log.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return log


def _claude_assistant(inp: int, out: int, *, cache_creation: int = 0, cache_read: int = 0) -> dict:
    return {
        "type": "assistant",
        "message": {
            "model": "claude-opus-4-8",
            "role": "assistant",
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
            },
        },
    }


def _write_codex_rollout(home: Path, cwd: str, totals: list[tuple[int, int]], *, name: str = "rollout-a.jsonl") -> Path:
    sess = home / ".codex" / "sessions" / "2026" / "06" / "28"
    sess.mkdir(parents=True, exist_ok=True)
    log = sess / name
    lines: list[dict] = [{"type": "session_meta", "payload": {"id": "x", "cwd": cwd}}]
    for inp, out in totals:
        lines.append(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": inp,
                            "cached_input_tokens": 0,
                            "output_tokens": out,
                            "total_tokens": inp + out,
                        }
                    },
                },
            }
        )
    log.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return log


class ClaudeUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.cwd = "/Users/me/dev/agent-fleet/worktrees/task-x"

    def test_sums_input_output_including_cache(self) -> None:
        _write_claude_log(
            self.home,
            self.cwd,
            [
                {"type": "custom-title", "customTitle": "fleet-x-driver"},
                _claude_assistant(100, 20, cache_creation=300, cache_read=50),
                {"type": "user", "message": {"role": "user", "content": "hi"}},
                _claude_assistant(10, 5),
            ],
        )
        usage = ClaudeAdapter.usage_from_session(cwd=self.cwd, home=self.home)
        # input = (100+300+50) + (10+0+0) = 460; output = 20 + 5 = 25
        self.assertEqual(usage, {"input_tokens": 460, "output_tokens": 25})
        # RAW tokens only — cost is approximate-or-absent, so no cost key.
        self.assertNotIn("cost", usage)

    def test_missing_dir_returns_none(self) -> None:
        self.assertIsNone(ClaudeAdapter.usage_from_session(cwd=self.cwd, home=self.home))

    def test_unparseable_lines_degrade_to_none(self) -> None:
        proj = self.home / ".claude" / "projects" / _escape_cwd(self.cwd)
        proj.mkdir(parents=True)
        (proj / "broken.jsonl").write_text("not json\n{also bad\n", encoding="utf-8")
        self.assertIsNone(ClaudeAdapter.usage_from_session(cwd=self.cwd, home=self.home))

    def test_records_across_multiple_session_files(self) -> None:
        proj = self.home / ".claude" / "projects" / _escape_cwd(self.cwd)
        proj.mkdir(parents=True)
        (proj / "a.jsonl").write_text(json.dumps(_claude_assistant(10, 1)) + "\n", encoding="utf-8")
        (proj / "b.jsonl").write_text(json.dumps(_claude_assistant(20, 2)) + "\n", encoding="utf-8")
        usage = ClaudeAdapter.usage_from_session(cwd=self.cwd, home=self.home)
        self.assertEqual(usage, {"input_tokens": 30, "output_tokens": 3})


class CodexUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.cwd = "/Users/me/dev/agent-fleet/worktrees/task-y"

    def test_uses_last_cumulative_total(self) -> None:
        # token_count totals are cumulative; the LAST one is the grand total.
        _write_codex_rollout(self.home, self.cwd, [(100, 10), (250, 30)])
        usage = CodexAdapter.usage_from_session(cwd=self.cwd, home=self.home)
        self.assertEqual(usage, {"input_tokens": 250, "output_tokens": 30})

    def test_only_matching_cwd_counts(self) -> None:
        _write_codex_rollout(self.home, self.cwd, [(250, 30)], name="rollout-mine.jsonl")
        _write_codex_rollout(
            self.home, "/some/other/task", [(9999, 9999)], name="rollout-other.jsonl"
        )
        usage = CodexAdapter.usage_from_session(cwd=self.cwd, home=self.home)
        self.assertEqual(usage, {"input_tokens": 250, "output_tokens": 30})

    def test_missing_dir_returns_none(self) -> None:
        self.assertIsNone(CodexAdapter.usage_from_session(cwd=self.cwd, home=self.home))

    def test_no_token_count_event_returns_none(self) -> None:
        _write_codex_rollout(self.home, self.cwd, [])
        self.assertIsNone(CodexAdapter.usage_from_session(cwd=self.cwd, home=self.home))


class BaseDefaultTests(unittest.TestCase):
    def test_base_default_returns_none(self) -> None:
        self.assertIsNone(VendorAdapter.usage_from_session(cwd="/anywhere"))
        self.assertIsNone(VendorAdapter.usage_from_session(cwd="/anywhere", home=Path("/x")))


class AgentsBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def test_bridge_resolves_claude(self) -> None:
        cwd = "/w/task-z"
        _write_claude_log(self.home, cwd, [_claude_assistant(40, 4)])
        usage = agents.usage_from_session("claude:opus", cwd=cwd, home=self.home)
        self.assertEqual(usage, {"input_tokens": 40, "output_tokens": 4})

    def test_bridge_resolves_codex(self) -> None:
        cwd = "/w/task-z2"
        _write_codex_rollout(self.home, cwd, [(40, 4)])
        usage = agents.usage_from_session("codex:gpt-5.5", cwd=cwd, home=self.home)
        self.assertEqual(usage, {"input_tokens": 40, "output_tokens": 4})


class RecordTaskUsageTests(unittest.TestCase):
    """``state.record_task_usage`` writes the block; absent log → no block."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.state_dir = self.home / "state"
        state.init_state(self.state_dir, name="demo")
        self.cwd = "/Users/me/dev/agent-fleet/worktrees/task-w"

    def _task(self, agent: str = "claude:opus") -> dict:
        return {
            "id": "1",
            "worktree": self.cwd,
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": agent, "status": "done"}],
        }

    def test_writes_usage_block(self) -> None:
        _write_claude_log(self.home, self.cwd, [_claude_assistant(100, 20)])
        task = self._task()
        state.record_task_usage(task, state_dir=self.state_dir, task_id="1", home=self.home)
        self.assertEqual(task["usage"], {"input_tokens": 100, "output_tokens": 20})

    def test_absent_when_no_log(self) -> None:
        task = self._task()
        state.record_task_usage(task, state_dir=self.state_dir, task_id="1", home=self.home)
        self.assertNotIn("usage", task)

    def test_absent_for_vendor_without_implementation(self) -> None:
        # An unknown spec raises inside the helper; it must degrade, not error.
        task = self._task(agent="not-a-vendor:x")
        state.record_task_usage(task, state_dir=self.state_dir, task_id="1", home=self.home)
        self.assertNotIn("usage", task)


class TerminalTransitionWiringTests(unittest.TestCase):
    """Usage is recorded ONLY at the terminal transition (no daemon/polling)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo")

    def _save(self, task: dict) -> None:
        state.save_task(self.state_dir, task["id"], task)

    def test_completion_records_usage(self) -> None:
        task = {
            "id": "1",
            "status": "running",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:opus", "status": "running"}],
        }
        self._save(task)
        with patch("fleet.state.record_task_usage") as rec:
            orchestrator.advance(self.state_dir, "1", task, result="approved")
        rec.assert_called_once()
        # The persisted task is terminal (completed).
        self.assertEqual(state.load_task(self.state_dir, "1")["status"], "completed")

    def test_intermediate_advance_does_not_record_usage(self) -> None:
        task = {
            "id": "2",
            "status": "running",
            "current_stage": 0,
            "stages": [
                {"role": "a", "agent": "claude:opus", "status": "running"},
                {"role": "b", "agent": "claude:opus", "status": "pending"},
            ],
        }
        self._save(task)
        with (
            patch("fleet.state.record_task_usage") as rec,
            patch("fleet.orchestrator._launch_driver_for_stage"),
        ):
            orchestrator.advance(self.state_dir, "2", task, result="approved")
        rec.assert_not_called()

    def test_prompt_deliverer_failure_records_usage(self) -> None:
        task = {
            "id": "3",
            "status": "running",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": "claude:opus", "status": "running"}],
        }
        self._save(task)
        with patch("fleet.state.record_task_usage") as rec:
            prompt_deliverer._fail(self.state_dir, "3", "boom", "3·driver")
        rec.assert_called_once()
        self.assertEqual(state.load_task(self.state_dir, "3")["status"], "failed")


if __name__ == "__main__":
    unittest.main()
