"""Tests for ``fleet.tmux`` — real tmux subprocesses (skipped if missing).

The test uses an isolated tmux session name so it can run alongside an
existing user session without collisions.
"""
from __future__ import annotations

import os
import shutil
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet import tmux  # noqa: E402


SESSION = "fleet-test-" + os.urandom(3).hex()


@unittest.skipIf(not tmux.available(), "tmux not available")
class TmuxTests(unittest.TestCase):
    def setUp(self) -> None:
        if tmux.session_exists(SESSION):
            tmux.kill_session(SESSION)
        tmux.new_session(SESSION)
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        if tmux.session_exists(SESSION):
            tmux.kill_session(SESSION)
        self._tmp.cleanup()

    def test_session_lifecycle(self) -> None:
        self.assertTrue(tmux.session_exists(SESSION))
        self.assertEqual(tmux.list_windows(SESSION), ["leader"])

    def test_new_window_with_env_and_cwd(self) -> None:
        tmux.new_window(
            SESSION,
            "1·driver",
            cwd=str(self.tmp),
            env={"FLEET_TASK_ID": "1", "FLEET_STATE_DIR": str(self.tmp)},
        )
        self.assertIn("1·driver", tmux.list_windows(SESSION))

    def test_load_and_paste_buffer(self) -> None:
        prompt_file = self.tmp / "prompt.txt"
        prompt_file.write_text("hello buffer\n")
        tmux.load_buffer("fleet-test-buf", str(prompt_file))
        # Open a window so we can paste somewhere.
        tmux.new_window(SESSION, "scratch", cwd=str(self.tmp))
        # paste_buffer must not raise.
        tmux.paste_buffer(SESSION, "scratch", "fleet-test-buf")
        tmux.delete_buffer("fleet-test-buf")  # cleanup
        # Re-deleting must be safe (best-effort).
        tmux.delete_buffer("fleet-test-buf")

    def test_kill_window(self) -> None:
        tmux.new_window(SESSION, "doomed")
        self.assertIn("doomed", tmux.list_windows(SESSION))
        tmux.kill_window(SESSION, "doomed")
        self.assertNotIn("doomed", tmux.list_windows(SESSION))

    def test_task_window_names_and_kill_task_windows(self) -> None:
        tmux.new_window(SESSION, "alpha·designer")
        tmux.new_window(SESSION, "alpha·implementer")
        tmux.new_window(SESSION, "alpha-other·driver")
        self.assertEqual(
            tmux.task_window_names(SESSION, "alpha"),
            ["alpha·designer", "alpha·implementer"],
        )
        tmux.kill_task_windows(SESSION, "alpha")
        windows = tmux.list_windows(SESSION)
        self.assertNotIn("alpha·designer", windows)
        self.assertNotIn("alpha·implementer", windows)
        self.assertIn("alpha-other·driver", windows)


class NewWindowMockTests(unittest.TestCase):
    """Mock-based tests for new_window — no live tmux session required."""

    def _patch_run(self):
        return unittest.mock.patch("fleet.tmux._run", return_value=None)

    def test_new_window_includes_detach_flag(self) -> None:
        with self._patch_run() as mock_run:
            tmux.new_window("sess", "mywin")
        args = mock_run.call_args[0][0]
        self.assertIn("-d", args)
        self.assertEqual(args[0], "tmux")
        self.assertEqual(args[1], "new-window")

    def test_new_window_detach_flag_position(self) -> None:
        with self._patch_run() as mock_run:
            tmux.new_window("sess", "mywin", cwd="/tmp", env={"FOO": "bar"})
        args = mock_run.call_args[0][0]
        # -d must appear before -t so it is always a positional flag
        self.assertLess(args.index("-d"), args.index("-t"))

    def test_matching_task_window_names_matches_task_id_prefix(self) -> None:
        self.assertEqual(
            tmux.matching_task_window_names(
                [
                    "leader",
                    "task-one·designer",
                    "task-one·implementer",
                    "task-one-extra·driver",
                    "task-one",
                ],
                "task-one",
            ),
            ["task-one·designer", "task-one·implementer", "task-one"],
        )

    def test_task_window_names_uses_matching_helper(self) -> None:
        with unittest.mock.patch(
            "fleet.tmux.list_windows",
            return_value=[
                "leader",
                "task-one·designer",
                "task-one·implementer",
                "task-one-extra·driver",
                "task-one",
            ],
        ):
            self.assertEqual(
                tmux.task_window_names("sess", "task-one"),
                ["task-one·designer", "task-one·implementer", "task-one"],
            )

    def test_kill_task_windows_kills_all_matches(self) -> None:
        with (
            unittest.mock.patch(
                "fleet.tmux.task_window_names",
                return_value=["task-one·designer", "task-one·implementer"],
            ) as mock_names,
            unittest.mock.patch("fleet.tmux.kill_window") as mock_kill,
        ):
            tmux.kill_task_windows("sess", "task-one")

        mock_names.assert_called_once_with("sess", "task-one")
        mock_kill.assert_has_calls([
            unittest.mock.call("sess", "task-one·designer"),
            unittest.mock.call("sess", "task-one·implementer"),
        ])


class SendKeysMockTests(unittest.TestCase):
    """Mock-based tests for send_keys — no live tmux session required."""

    def _patch_run(self):
        return unittest.mock.patch("fleet.tmux._run", return_value=None)

    def test_empty_text_sends_only_enter(self) -> None:
        with self._patch_run() as mock_run:
            tmux.send_keys("sess", "win", "", enter=True)
        mock_run.assert_called_once_with(
            ["tmux", "send-keys", "-t", "sess:win", "Enter"]
        )

    def test_nonempty_text_sends_text_then_enter(self) -> None:
        with self._patch_run() as mock_run:
            tmux.send_keys("sess", "win", "hello", enter=True)
        self.assertEqual(mock_run.call_count, 2)
        mock_run.assert_any_call(["tmux", "send-keys", "-t", "sess:win", "hello"])
        mock_run.assert_any_call(["tmux", "send-keys", "-t", "sess:win", "Enter"])

    def test_enter_false_skips_enter(self) -> None:
        with self._patch_run() as mock_run:
            tmux.send_keys("sess", "win", "hello", enter=False)
        mock_run.assert_called_once_with(
            ["tmux", "send-keys", "-t", "sess:win", "hello"]
        )

    def test_empty_text_enter_false_sends_nothing(self) -> None:
        with self._patch_run() as mock_run:
            tmux.send_keys("sess", "win", "", enter=False)
        mock_run.assert_not_called()


class CapturePaneMockTests(unittest.TestCase):
    """Mock-based tests for capture_pane — no live tmux session required."""

    @unittest.mock.patch("fleet.tmux.subprocess.run")
    def test_capture_pane_uses_recent_history(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "pane text"
        self.assertEqual(tmux.capture_pane("sess", "win"), "pane text")
        mock_run.assert_called_once_with(
            ["tmux", "capture-pane", "-p", "-J", "-S", "-200", "-t", "sess:win"],
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
