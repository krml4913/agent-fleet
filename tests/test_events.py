"""Tests for ``fleet.events`` — append_event / read_events."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet.events import append_event, read_events  # noqa: E402


class EventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.events_path = Path(self._tmp.name) / "events.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_append_and_read(self) -> None:
        append_event(self.events_path, "spawn", task_id="1", agent="claude:sonnet")
        append_event(self.events_path, "awaiting_orders", task_id="1", question="ok?")
        events = read_events(self.events_path)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "spawn")
        self.assertEqual(events[0]["task_id"], "1")
        self.assertEqual(events[1]["type"], "awaiting_orders")
        self.assertIn("ts", events[0])

    def test_read_missing_file(self) -> None:
        missing = Path(self._tmp.name) / "nope.jsonl"
        self.assertEqual(read_events(missing), [])

    def test_record_returned(self) -> None:
        rec = append_event(self.events_path, "test", foo="bar")
        self.assertEqual(rec["type"], "test")
        self.assertEqual(rec["foo"], "bar")
        self.assertTrue(rec["ts"].endswith("Z"))


if __name__ == "__main__":
    unittest.main()
