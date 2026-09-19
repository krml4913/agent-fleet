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
import tests._fleet_test_helpers  # noqa: E402,F401  (hermetic env: FLEET_NO_NOTIFY / FLEET_NO_MUX)
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import agents, orchestrator, prompt_deliverer, prompt_pointer, state  # noqa: E402
from fleet.adapters import ClaudeAdapter, CodexAdapter  # noqa: E402
from fleet.adapters.base import VendorAdapter  # noqa: E402


def _escape_cwd(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _write_claude_log(
    home: Path, cwd: str, lines: list[dict], *, name: str = "5c49bf3a.jsonl"
) -> Path:
    proj = home / ".claude" / "projects" / _escape_cwd(cwd)
    proj.mkdir(parents=True, exist_ok=True)
    log = proj / name
    log.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return log


def _claude_user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _codex_user_message(text: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


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


def _write_codex_rollout(
    home: Path,
    cwd: str,
    totals: list[tuple[int, int]],
    *,
    name: str = "rollout-a.jsonl",
    prompt: str | None = None,
) -> Path:
    sess = home / ".codex" / "sessions" / "2026" / "06" / "28"
    sess.mkdir(parents=True, exist_ok=True)
    log = sess / name
    lines: list[dict] = [{"type": "session_meta", "payload": {"id": "x", "cwd": cwd}}]
    if prompt is not None:
        lines.append(_codex_user_message(prompt))
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


class ClaudePointerUsageTests(unittest.TestCase):
    """``pointer`` narrows a shared cwd (workspace=none) to one task's sessions."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.cwd = "/Users/me/dev/proj"
        self.mine = prompt_pointer.pointer_text(self.home / "task-mine" / "driver-prompt.md")
        self.other = prompt_pointer.pointer_text(self.home / "task-other" / "driver-prompt.md")

    def _usage(self, pointer: str | None):
        return ClaudeAdapter.usage_from_session(cwd=self.cwd, home=self.home, pointer=pointer)

    def test_counts_only_sessions_started_by_the_pointer(self) -> None:
        _write_claude_log(self.home, self.cwd, [_claude_user(self.mine), _claude_assistant(100, 10)], name="mine.jsonl")
        _write_claude_log(self.home, self.cwd, [_claude_user(self.other), _claude_assistant(7000, 700)], name="other.jsonl")
        _write_claude_log(self.home, self.cwd, [_claude_user("hi leader"), _claude_assistant(9000, 900)], name="leader.jsonl")
        self.assertEqual(self._usage(self.mine), {"input_tokens": 100, "output_tokens": 10})

    def test_no_pointer_counts_every_session(self) -> None:
        _write_claude_log(self.home, self.cwd, [_claude_user(self.mine), _claude_assistant(1, 1)], name="a.jsonl")
        _write_claude_log(self.home, self.cwd, [_claude_assistant(2, 2)], name="b.jsonl")
        self.assertEqual(self._usage(None), {"input_tokens": 3, "output_tokens": 3})

    def test_sums_every_stage_session_of_the_task(self) -> None:
        _write_claude_log(self.home, self.cwd, [_claude_user(self.mine), _claude_assistant(10, 1)], name="s1.jsonl")
        _write_claude_log(self.home, self.cwd, [_claude_user(self.mine), _claude_assistant(20, 2)], name="s2.jsonl")
        self.assertEqual(self._usage(self.mine), {"input_tokens": 30, "output_tokens": 3})

    def test_only_the_first_pointer_mention_decides(self) -> None:
        # A leader that later reads/pastes this task's pointer is not the task's driver.
        _write_claude_log(
            self.home,
            self.cwd,
            [_claude_user(self.other), _claude_user(self.mine), _claude_assistant(500, 50)],
            name="leader.jsonl",
        )
        self.assertIsNone(self._usage(self.mine))

    def test_no_matching_session_returns_none(self) -> None:
        _write_claude_log(self.home, self.cwd, [_claude_user(self.other), _claude_assistant(5, 5)])
        self.assertIsNone(self._usage(self.mine))

    def test_pointer_inside_content_block_list(self) -> None:
        # Text nested in a content-block list is found too (log-format agnostic).
        record = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": self.mine}]}}
        _write_claude_log(self.home, self.cwd, [record, _claude_assistant(4, 4)])
        self.assertEqual(self._usage(self.mine), {"input_tokens": 4, "output_tokens": 4})


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


class CodexPointerUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.cwd = "/Users/me/dev/proj"
        self.mine = prompt_pointer.pointer_text(self.home / "task-mine" / "driver-prompt.md")
        self.other = prompt_pointer.pointer_text(self.home / "task-other" / "driver-prompt.md")

    def test_counts_only_rollouts_started_by_the_pointer(self) -> None:
        _write_codex_rollout(self.home, self.cwd, [(250, 30)], name="rollout-mine.jsonl", prompt=self.mine)
        _write_codex_rollout(self.home, self.cwd, [(9000, 900)], name="rollout-other.jsonl", prompt=self.other)
        _write_codex_rollout(self.home, self.cwd, [(8000, 800)], name="rollout-leader.jsonl")
        usage = CodexAdapter.usage_from_session(cwd=self.cwd, home=self.home, pointer=self.mine)
        self.assertEqual(usage, {"input_tokens": 250, "output_tokens": 30})

    def test_pointer_still_requires_matching_cwd(self) -> None:
        _write_codex_rollout(self.home, "/elsewhere", [(250, 30)], prompt=self.mine)
        self.assertIsNone(CodexAdapter.usage_from_session(cwd=self.cwd, home=self.home, pointer=self.mine))


class BaseDefaultTests(unittest.TestCase):
    def test_base_default_returns_none(self) -> None:
        self.assertIsNone(VendorAdapter.usage_from_session(cwd="/anywhere"))
        self.assertIsNone(VendorAdapter.usage_from_session(cwd="/anywhere", home=Path("/x")))
        self.assertIsNone(VendorAdapter.usage_from_session(cwd="/anywhere", pointer="p"))


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


class RecordTaskUsageNoWorkspaceTests(unittest.TestCase):
    """workspace=none: the pane ran in the shared project root (Issue #264)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.state_dir = Path(self._tmp.name) / "state"
        state.init_state(self.state_dir, name="demo")
        project = state.load_project(self.state_dir)
        project["repo"] = str(self.repo)
        state.save_project(self.state_dir, project)
        self.tdir = state.task_dir(self.state_dir, "none1")
        self.tdir.mkdir(parents=True)
        self.pointer = prompt_pointer.pointer_text(self.tdir / "driver-prompt.md")

    def _task(self, agent: str = "claude:opus") -> dict:
        return {
            "id": "none1",
            "current_stage": 0,
            "stages": [{"role": "driver", "agent": agent, "status": "done"}],
        }

    def _record(self, task: dict) -> dict:
        state.record_task_usage(task, state_dir=self.state_dir, task_id="none1", home=self.home)
        return task

    def test_claude_usage_found_in_project_root_by_pointer(self) -> None:
        cwd = str(self.repo)
        _write_claude_log(self.home, cwd, [_claude_user(self.pointer), _claude_assistant(100, 20)], name="mine.jsonl")
        # The leader and another task share the root; neither is attributed.
        _write_claude_log(self.home, cwd, [_claude_user("hello"), _claude_assistant(5000, 500)], name="leader.jsonl")
        other = prompt_pointer.pointer_text(state.task_dir(self.state_dir, "other") / "driver-prompt.md")
        _write_claude_log(self.home, cwd, [_claude_user(other), _claude_assistant(7000, 700)], name="other.jsonl")
        task = self._record(self._task())
        self.assertEqual(task["usage"], {"input_tokens": 100, "output_tokens": 20})

    def test_task_id_prefix_does_not_collide(self) -> None:
        # ``none1`` must not pick up the sessions of a task named ``none1-b``.
        sibling = prompt_pointer.pointer_text(state.task_dir(self.state_dir, "none1-b") / "driver-prompt.md")
        _write_claude_log(self.home, str(self.repo), [_claude_user(sibling), _claude_assistant(700, 70)])
        self.assertNotIn("usage", self._record(self._task()))

    def test_codex_usage_found_in_project_root_by_pointer(self) -> None:
        _write_codex_rollout(self.home, str(self.repo), [(250, 30)], name="rollout-mine.jsonl", prompt=self.pointer)
        _write_codex_rollout(self.home, str(self.repo), [(9000, 900)], name="rollout-leader.jsonl")
        task = self._record(self._task(agent="codex:gpt-5.5"))
        self.assertEqual(task["usage"], {"input_tokens": 250, "output_tokens": 30})

    def test_absent_when_only_unrelated_sessions_in_root(self) -> None:
        _write_claude_log(self.home, str(self.repo), [_claude_user("hi"), _claude_assistant(1, 1)])
        self.assertNotIn("usage", self._record(self._task()))

    def test_task_dir_fallback_when_project_has_no_repo(self) -> None:
        project = state.load_project(self.state_dir)
        project.pop("repo", None)
        state.save_project(self.state_dir, project)
        # The pane ran in the task dir: no pointer needed, nothing else runs there.
        _write_claude_log(self.home, str(self.tdir), [_claude_assistant(11, 2)])
        task = self._record(self._task())
        self.assertEqual(task["usage"], {"input_tokens": 11, "output_tokens": 2})

    def test_task_dir_fallback_when_repo_path_is_missing(self) -> None:
        project = state.load_project(self.state_dir)
        project["repo"] = str(self.repo / "gone")
        state.save_project(self.state_dir, project)
        _write_claude_log(self.home, str(self.tdir), [_claude_assistant(11, 2)])
        self.assertEqual(self._record(self._task())["usage"], {"input_tokens": 11, "output_tokens": 2})

    def test_task_dir_logs_still_counted_when_repo_has_none(self) -> None:
        # A pane that predates #262 ran in the task dir although the project has
        # a repo: the root has no match, the task dir does.
        _write_claude_log(self.home, str(self.tdir), [_claude_assistant(13, 3)])
        self.assertEqual(self._record(self._task())["usage"], {"input_tokens": 13, "output_tokens": 3})

    def test_worktree_task_ignores_pointer_and_root(self) -> None:
        wt = str(Path(self._tmp.name) / "wt")
        _write_claude_log(self.home, wt, [_claude_assistant(40, 4)])
        _write_claude_log(self.home, str(self.repo), [_claude_user(self.pointer), _claude_assistant(9, 9)])
        task = self._task()
        task["worktree"] = wt
        self.assertEqual(self._record(task)["usage"], {"input_tokens": 40, "output_tokens": 4})


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
