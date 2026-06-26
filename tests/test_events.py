"""Tests for ``fleet.events`` — append_event / read_events."""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet.events import (  # noqa: E402
    MAX_EVENT_TEXT,
    append_event,
    read_events,
    truncate_text,
)


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

    def test_read_skips_malformed_lines(self) -> None:
        # Simulate a log where a torn O_APPEND write has corrupted lines:
        # a half-written record concatenated with the next, plain garbage,
        # and a blank line — all interleaved with valid records.
        good_a = '{"ts": "2026-01-01T00:00:00Z", "type": "start", "task_id": "1"}'
        good_b = '{"ts": "2026-01-01T00:00:01Z", "type": "done", "task_id": "1"}'
        torn = '{"ts": "2026-01-01T00:00:00Z", "ty{"ts": "2026", "type": "done"}'
        garbage = "not json at all"
        self.events_path.write_text(
            "\n".join([good_a, torn, garbage, "", good_b]) + "\n",
            encoding="utf-8",
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            events = read_events(self.events_path)
        # No exception, and exactly the two valid records survive in order.
        self.assertEqual([e["type"] for e in events], ["start", "done"])
        # Skips are surfaced (not silently swallowed), once per read.
        self.assertIn("skipped 2 malformed line(s)", err.getvalue())

    def test_read_skips_non_object_records(self) -> None:
        # Valid JSON that is not an object would break downstream ``.get()``
        # calls; read_events must keep its list[dict] contract.
        self.events_path.write_text(
            '42\n["a", "b"]\n{"ts": "2026-01-01T00:00:00Z", "type": "note"}\n',
            encoding="utf-8",
        )
        with contextlib.redirect_stderr(io.StringIO()):
            events = read_events(self.events_path)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "note")

    def test_read_clean_file_is_silent(self) -> None:
        append_event(self.events_path, "spawn", task_id="1")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            events = read_events(self.events_path)
        self.assertEqual(len(events), 1)
        self.assertEqual(err.getvalue(), "")

    def test_truncate_text_short_unchanged(self) -> None:
        text = "short description"
        self.assertEqual(truncate_text(text), text)

    def test_truncate_text_long_is_clamped(self) -> None:
        text = "x" * (MAX_EVENT_TEXT + 5000)
        out = truncate_text(text)
        # Original prefix is kept, a marker notes the real length, and the
        # result is bounded close to the limit (no longer the full body).
        self.assertTrue(out.startswith("x" * MAX_EVENT_TEXT))
        self.assertIn(str(len(text)), out)
        self.assertLess(len(out), len(text))
        self.assertLessEqual(len(out), MAX_EVENT_TEXT + 64)


if __name__ == "__main__":
    unittest.main()
