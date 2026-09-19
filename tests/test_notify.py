"""Tests for ``fleet.notify`` — best-effort, never raises."""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import notify  # noqa: E402


class NotifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)
        # Never pop a real desktop toast from the suite on Windows hosts.
        win = patch("fleet.notify._windows_notify")
        self.mock_windows = win.start()
        self.addCleanup(win.stop)
        # These tests exercise the real transports, so FLEET_NO_NOTIFY (set
        # suite-wide by _fleet_test_helpers) must not short-circuit ``send``.
        # Previously they were silently no-ops whenever another module had
        # imported the helpers first — i.e. order-dependent.
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("FLEET_NO_NOTIFY", None)
        # ...but never run osascript for real on a macOS developer box.
        # (Patching platform, not subprocess.run: the latter is the global
        # subprocess module, which platform.uname() itself uses on Windows.)
        system = patch("fleet.notify.platform.system", return_value="Linux")
        system.start()
        self.addCleanup(system.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_config_is_safe(self) -> None:
        # Should not raise even with no config file at all.
        notify.send(self.state_dir, "title", "message")

    def test_load_config_empty(self) -> None:
        self.assertEqual(notify.load_config(self.state_dir), {})

    def test_disabled_macos_and_slack(self) -> None:
        cfg = (
            "macos:\n  enabled: false\n"
            "slack:\n  enabled: false\n  webhook_url: ''\n"
        )
        (self.state_dir / notify.CONFIG_FILE).write_text(cfg, encoding="utf-8")
        # Must not call out to anything — and must not raise.
        notify.send(self.state_dir, "title", "message")

    def test_invalid_config_no_raise(self) -> None:
        (self.state_dir / notify.CONFIG_FILE).write_text("{[}@@@", encoding="utf-8")
        # Bad YAML → warning but no exception.
        notify.send(self.state_dir, "title", "message")

    def test_slack_bad_webhook_no_raise(self) -> None:
        cfg = (
            "macos:\n  enabled: false\n"
            "slack:\n  enabled: true\n"
            "  webhook_url: 'http://127.0.0.1:1/no-listener-here'\n"
        )
        (self.state_dir / notify.CONFIG_FILE).write_text(cfg, encoding="utf-8")
        # Simulate the refused connection instead of dialling a closed port:
        # on Windows a real connect() to 127.0.0.1:1 takes ~2 s of SYN retries.
        err = io.StringIO()
        with patch(
            "fleet.notify.urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError(10061, "refused")),
        ) as mock_urlopen, patch("sys.stderr", err):
            notify.send(self.state_dir, "title", "message")  # must not raise
        mock_urlopen.assert_called_once()
        self.assertEqual(
            mock_urlopen.call_args.args[0].full_url, "http://127.0.0.1:1/no-listener-here"
        )
        self.assertIn("slack notify failed", err.getvalue())


class NoNotifyEnvTests(unittest.TestCase):
    """FLEET_NO_NOTIFY suppresses all transports."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)
        # Never pop a real desktop toast from the suite on Windows hosts.
        win = patch("fleet.notify._windows_notify")
        self.mock_windows = win.start()
        self.addCleanup(win.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_fleet_no_notify_blocks_macos(self) -> None:
        with patch.dict(os.environ, {"FLEET_NO_NOTIFY": "1"}):
            with patch("fleet.notify._macos_notify") as mock_macos, \
                 patch("fleet.notify._slack_notify") as mock_slack:
                notify.send(self.state_dir, "title", "message")
        mock_macos.assert_not_called()
        mock_slack.assert_not_called()
        self.mock_windows.assert_not_called()

    def test_fleet_no_notify_blocks_slack(self) -> None:
        cfg = (
            "macos:\n  enabled: true\n"
            "slack:\n  enabled: true\n"
            "  webhook_url: 'https://hooks.slack.com/fake'\n"
        )
        (self.state_dir / notify.CONFIG_FILE).write_text(cfg, encoding="utf-8")
        with patch.dict(os.environ, {"FLEET_NO_NOTIFY": "1"}):
            with patch("fleet.notify._macos_notify") as mock_macos, \
                 patch("fleet.notify._slack_notify") as mock_slack:
                notify.send(self.state_dir, "title", "message")
        mock_macos.assert_not_called()
        mock_slack.assert_not_called()
        self.mock_windows.assert_not_called()

    def test_fleet_no_notify_empty_string_does_not_suppress(self) -> None:
        with patch.dict(os.environ, {"FLEET_NO_NOTIFY": ""}):
            with patch("fleet.notify._macos_notify") as mock_macos:
                notify.send(self.state_dir, "title", "message")
        mock_macos.assert_called_once()

    def test_fleet_no_notify_unset_allows_notify(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "FLEET_NO_NOTIFY"}
        with patch.dict(os.environ, env, clear=True):
            with patch("fleet.notify._macos_notify") as mock_macos:
                notify.send(self.state_dir, "title", "message")
        mock_macos.assert_called_once()


class LevelMappingTests(unittest.TestCase):
    """level → emoji and level → Slack color mappings."""

    def test_level_emoji_mapping(self) -> None:
        self.assertEqual(notify.level_emoji("success"), "✅")
        self.assertEqual(notify.level_emoji("waiting"), "🟡")
        self.assertEqual(notify.level_emoji("progress"), "▶️")
        self.assertEqual(notify.level_emoji("error"), "❌")
        self.assertEqual(notify.level_emoji("info"), "ℹ️")

    def test_level_emoji_unknown_falls_back_to_info(self) -> None:
        self.assertEqual(notify.level_emoji("bogus"), notify.level_emoji("info"))

    def test_level_color_mapping(self) -> None:
        self.assertEqual(notify.level_color("success"), "good")
        self.assertEqual(notify.level_color("waiting"), "warning")
        self.assertEqual(notify.level_color("error"), "danger")
        # progress / info use neutral (non-keyword) colors, distinct from the above.
        self.assertNotIn(
            notify.level_color("progress"), {"good", "warning", "danger"}
        )
        self.assertNotIn(notify.level_color("info"), {"good", "warning", "danger"})

    def test_level_color_unknown_falls_back_to_info(self) -> None:
        self.assertEqual(notify.level_color("bogus"), notify.level_color("info"))


class MacosRenderTests(unittest.TestCase):
    """macOS notification carries the level emoji in the message body."""

    def test_macos_message_includes_emoji(self) -> None:
        with patch("fleet.notify.platform.system", return_value="Darwin"), \
             patch("fleet.notify.shutil.which", return_value="/usr/bin/osascript"), \
             patch("fleet.notify.subprocess.run") as mock_run:
            notify._macos_notify({}, "the title", "the message", "success")
        mock_run.assert_called_once()
        script = mock_run.call_args.args[0][-1]
        self.assertIn("✅", script)
        self.assertIn("the message", script)
        self.assertIn("the title", script)


class WindowsToastTests(unittest.TestCase):
    """Windows toast via PowerShell + WinRT; best-effort, Windows-only."""

    @staticmethod
    def _decode(argv: list) -> str:
        return base64.b64decode(argv[-1]).decode("utf-16-le")

    def _run(self, cfg: dict, title: str, message: str, level: str = "info"):
        with patch("fleet.notify.platform.system", return_value="Windows"), \
             patch("fleet.notify.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            notify._windows_notify(cfg, title, message, level)
        return mock_run

    def test_invokes_powershell_encoded_no_window(self) -> None:
        mock_run = self._run({}, "fleet test", "hello", "success")
        mock_run.assert_called_once()
        argv = mock_run.call_args.args[0]
        self.assertEqual(argv[0], "powershell.exe")
        self.assertIn("-NoProfile", argv)
        self.assertIn("-NonInteractive", argv)
        self.assertEqual(argv[-2], "-EncodedCommand")
        kwargs = mock_run.call_args.kwargs
        self.assertLessEqual(kwargs["timeout"], 10)
        self.assertEqual(
            kwargs["creationflags"],
            getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        script = self._decode(argv)
        self.assertIn("ToastNotificationManager", script)
        self.assertIn(notify._WINDOWS_TOAST_APP_ID, script)
        self.assertIn("<text>fleet test</text>", script)
        # Level emoji as a char ref: the whole script is pure ASCII.
        self.assertIn("&#9989; hello", script)
        self.assertTrue(script.isascii())

    def test_escapes_xml_and_powershell_quotes(self) -> None:
        mock_run = self._run(
            {}, "a <b> & 'c'", "x \"y\" it’s $(evil) 'z'\nline2"
        )
        script = self._decode(mock_run.call_args.args[0])
        self.assertIn("a &lt;b&gt; &amp; &apos;c&apos;", script)
        self.assertIn(
            "x &quot;y&quot; it&#8217;s $(evil) &apos;z&apos; line2", script
        )
        # The only raw single quotes on that line are the PS string delimiters.
        load = next(ln for ln in script.splitlines() if "LoadXml" in ln)
        self.assertEqual(load.count("'"), 2)

    def test_truncates_title_and_body(self) -> None:
        mock_run = self._run({}, "T" * 100, "m" * 1000)
        script = self._decode(mock_run.call_args.args[0])
        self.assertIn("<text>" + "T" * 60 + "</text>", script)
        self.assertNotIn("T" * 61, script)
        self.assertIn("m" * 290, script)
        self.assertNotIn("m" * 300, script)

    def test_disabled_in_config(self) -> None:
        mock_run = self._run({"enabled": False}, "t", "m")
        mock_run.assert_not_called()

    def test_non_windows_never_calls(self) -> None:
        for system in ("Darwin", "Linux"):
            with patch("fleet.notify.platform.system", return_value=system), \
                 patch("fleet.notify.subprocess.run") as mock_run:
                notify._windows_notify({}, "t", "m")
            mock_run.assert_not_called()

    def test_failure_warns_never_raises(self) -> None:
        for effect in (
            subprocess.TimeoutExpired("powershell.exe", 5),
            OSError("nope"),
        ):
            err = io.StringIO()
            with patch("fleet.notify.platform.system", return_value="Windows"), \
                 patch("fleet.notify.subprocess.run", side_effect=effect), \
                 patch("sys.stderr", err):
                notify._windows_notify({}, "t", "m")
            self.assertIn("Windows notify failed", err.getvalue())

    def test_nonzero_exit_warns(self) -> None:
        err = io.StringIO()
        with patch("fleet.notify.platform.system", return_value="Windows"), \
             patch("fleet.notify.subprocess.run") as mock_run, \
             patch("sys.stderr", err):
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = b"boom"
            notify._windows_notify({}, "t", "m")
        self.assertIn("boom", err.getvalue())

    def test_send_honors_windows_disabled(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "FLEET_NO_NOTIFY"}
        with TemporaryDirectory() as d:
            (Path(d) / notify.CONFIG_FILE).write_text(
                "windows:\n  enabled: false\n", encoding="utf-8"
            )
            with patch.dict(os.environ, env, clear=True), \
                 patch("fleet.notify.platform.system", return_value="Windows"), \
                 patch("fleet.notify.subprocess.run") as mock_run:
                notify.send(Path(d), "t", "m")
        mock_run.assert_not_called()

    def test_send_fleet_no_notify_skips_windows(self) -> None:
        with TemporaryDirectory() as d, \
             patch.dict(os.environ, {"FLEET_NO_NOTIFY": "1"}), \
             patch("fleet.notify.platform.system", return_value="Windows"), \
             patch("fleet.notify.subprocess.run") as mock_run:
            notify.send(Path(d), "t", "m")
        mock_run.assert_not_called()


class SlackRenderTests(unittest.TestCase):
    """Slack payload is an attachment with the level color, emoji + title, context."""

    def _capture_payload(self, level: str, title: str, message: str) -> dict:
        captured = {}

        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def read(self_inner):
                return b""

        def fake_urlopen(req, timeout=None):
            captured["data"] = req.data
            return _Resp()

        cfg = {"enabled": True, "webhook_url": "https://hooks.slack.com/fake"}
        with patch("fleet.notify.urllib.request.urlopen", side_effect=fake_urlopen):
            notify._slack_notify(cfg, title, message, level)
        return json.loads(captured["data"].decode("utf-8"))

    def test_slack_attachment_color_and_emoji(self) -> None:
        payload = self._capture_payload(
            "error", "fleet myproj: task-abc boot gate", "ack timed out"
        )
        self.assertIn("attachments", payload)
        att = payload["attachments"][0]
        self.assertEqual(att["color"], "danger")
        self.assertTrue(att["title"].startswith("❌"))
        self.assertIn("fleet myproj: task-abc boot gate", att["title"])
        self.assertEqual(att["text"], "ack timed out")

    def test_slack_color_per_level(self) -> None:
        for level, color in (
            ("success", "good"),
            ("waiting", "warning"),
            ("error", "danger"),
        ):
            payload = self._capture_payload(level, "t", "m")
            self.assertEqual(payload["attachments"][0]["color"], color)

    def test_slack_context_line_from_title(self) -> None:
        payload = self._capture_payload(
            "progress", "fleet myproj: task-abc stage 1 done", "moving on"
        )
        footer = payload["attachments"][0].get("footer", "")
        self.assertIn("myproj", footer)
        self.assertIn("task-abc", footer)


if __name__ == "__main__":
    unittest.main()
