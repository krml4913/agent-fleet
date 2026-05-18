"""Tests for ``fleet.locking.atomic_write``."""
from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fleet.locking import atomic_write  # noqa: E402


class AtomicWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_basic_write(self) -> None:
        target = self.base / "out.txt"
        with atomic_write(target) as f:
            f.write("hello\n")
        self.assertEqual(target.read_text(), "hello\n")

    def test_creates_parents(self) -> None:
        target = self.base / "deep" / "nested" / "out.txt"
        with atomic_write(target) as f:
            f.write("ok")
        self.assertEqual(target.read_text(), "ok")

    def test_replaces_existing(self) -> None:
        target = self.base / "out.txt"
        target.write_text("old")
        with atomic_write(target) as f:
            f.write("new")
        self.assertEqual(target.read_text(), "new")

    def test_no_partial_on_exception(self) -> None:
        target = self.base / "out.txt"
        target.write_text("preserved")
        with self.assertRaises(RuntimeError):
            with atomic_write(target) as f:
                f.write("would be lost")
                raise RuntimeError("boom")
        self.assertEqual(target.read_text(), "preserved")
        # Temp file should have been cleaned up.
        leftover = [p for p in self.base.iterdir() if ".tmp" in p.name]
        self.assertEqual(leftover, [])

    def test_concurrent_writers_serialize(self) -> None:
        """Threads writing concurrently must each produce one of N final states.

        We don't assert ordering — only that the final content is a valid,
        complete payload from one of the threads (no interleaving).
        """
        target = self.base / "out.txt"
        N = 10
        payloads = [f"payload-{i}\n" * 100 for i in range(N)]

        def worker(payload: str) -> None:
            with atomic_write(target) as f:
                f.write(payload)

        threads = [threading.Thread(target=worker, args=(p,)) for p in payloads]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = target.read_text()
        self.assertIn(final, payloads)


if __name__ == "__main__":
    unittest.main()
