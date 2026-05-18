"""Tests for ``fleet.heartbeat`` — humanize_age, last_per_task, parse_ts."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet import heartbeat  # noqa: E402


def _ts(ago_seconds: float) -> str:
    when = datetime.now(timezone.utc) - timedelta(seconds=ago_seconds)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class HumanizeAgeTests(unittest.TestCase):
    def test_seconds(self) -> None:
        self.assertEqual(heartbeat.humanize_age(15), "15s ago")

    def test_minutes(self) -> None:
        self.assertEqual(heartbeat.humanize_age(125), "2m ago")

    def test_hours(self) -> None:
        self.assertEqual(heartbeat.humanize_age(3 * 3600 + 5), "3h ago")

    def test_days(self) -> None:
        self.assertEqual(heartbeat.humanize_age(2 * 86400), "2d ago")

    def test_negative_clamps(self) -> None:
        self.assertEqual(heartbeat.humanize_age(-5), "0s ago")


class ParseTsTests(unittest.TestCase):
    def test_valid(self) -> None:
        self.assertIsNotNone(heartbeat.parse_ts("2026-05-19T00:00:00Z"))

    def test_invalid(self) -> None:
        self.assertIsNone(heartbeat.parse_ts("not a timestamp"))
        self.assertIsNone(heartbeat.parse_ts(None))


class LastPerTaskTests(unittest.TestCase):
    def test_latest_wins(self) -> None:
        events = [
            {"ts": _ts(120), "type": "spawn", "task_id": "1"},
            {"ts": _ts(60), "type": "milestone", "task_id": "1"},
            {"ts": _ts(10), "type": "heartbeat", "task_id": "1"},
            {"ts": _ts(5), "type": "heartbeat", "task_id": "2"},
        ]
        out = heartbeat.last_per_task(events)
        self.assertIn(out["1"], ("10s ago", "11s ago", "12s ago"))
        self.assertIn(out["2"], ("5s ago", "6s ago", "7s ago"))

    def test_ignores_eventless_task(self) -> None:
        events = [{"ts": _ts(10), "type": "x"}]  # no task_id
        self.assertEqual(heartbeat.last_per_task(events), {})

    def test_handles_bad_ts(self) -> None:
        events = [
            {"ts": "garbage", "type": "x", "task_id": "1"},
        ]
        out = heartbeat.last_per_task(events)
        self.assertEqual(out["1"], "garbage")


if __name__ == "__main__":
    unittest.main()
