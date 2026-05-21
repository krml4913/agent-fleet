"""Tests for ``fleet.notify`` — best-effort, never raises."""
from __future__ import annotations

import os
import sys
import unittest
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
        (self.state_dir / notify.CONFIG_FILE).write_text(cfg)
        # Must not call out to anything — and must not raise.
        notify.send(self.state_dir, "title", "message")

    def test_invalid_config_no_raise(self) -> None:
        (self.state_dir / notify.CONFIG_FILE).write_text("{[}@@@")
        # Bad YAML → warning but no exception.
        notify.send(self.state_dir, "title", "message")

    def test_slack_bad_webhook_no_raise(self) -> None:
        cfg = (
            "macos:\n  enabled: false\n"
            "slack:\n  enabled: true\n"
            "  webhook_url: 'http://127.0.0.1:1/no-listener-here'\n"
        )
        (self.state_dir / notify.CONFIG_FILE).write_text(cfg)
        notify.send(self.state_dir, "title", "message")  # must not raise


class NoNotifyEnvTests(unittest.TestCase):
    """FLEET_NO_NOTIFY suppresses all transports."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_fleet_no_notify_blocks_macos(self) -> None:
        with patch.dict(os.environ, {"FLEET_NO_NOTIFY": "1"}):
            with patch("fleet.notify._macos_notify") as mock_macos, \
                 patch("fleet.notify._slack_notify") as mock_slack:
                notify.send(self.state_dir, "title", "message")
        mock_macos.assert_not_called()
        mock_slack.assert_not_called()

    def test_fleet_no_notify_blocks_slack(self) -> None:
        cfg = (
            "macos:\n  enabled: true\n"
            "slack:\n  enabled: true\n"
            "  webhook_url: 'https://hooks.slack.com/fake'\n"
        )
        (self.state_dir / notify.CONFIG_FILE).write_text(cfg)
        with patch.dict(os.environ, {"FLEET_NO_NOTIFY": "1"}):
            with patch("fleet.notify._macos_notify") as mock_macos, \
                 patch("fleet.notify._slack_notify") as mock_slack:
                notify.send(self.state_dir, "title", "message")
        mock_macos.assert_not_called()
        mock_slack.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
