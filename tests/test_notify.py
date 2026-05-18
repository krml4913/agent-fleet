"""Tests for ``fleet.notify`` — best-effort, never raises."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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


if __name__ == "__main__":
    unittest.main()
