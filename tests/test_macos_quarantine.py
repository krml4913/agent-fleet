"""Tests for ``fleet.macos_quarantine`` (#309).

Off darwin, ``quarantined_path`` must return ``None`` without ever loading
ctypes — that is checked explicitly (a stray ``getxattr`` call on the wrong
platform's ABI would be a real bug, not just a wrong answer). On darwin, the
libc loader is faked so these tests never depend on real xattrs except in
the opt-in live test at the bottom.
"""
from __future__ import annotations

import contextlib
import ctypes
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import tests._fleet_test_helpers  # noqa: E402,F401  (hermetic env)

from fleet import macos_quarantine as q  # noqa: E402


class _FakeLibc:
    """Stands in for the real libc handle: ``getxattr`` backed by a Python bytes value."""

    def __init__(self, value: bytes | None) -> None:
        self._value = value
        self.calls = 0

    def getxattr(self, path, name, buf, size, position, options) -> int:
        self.calls += 1
        if self._value is None:
            return -1
        if buf is None or size == 0:
            return len(self._value)
        n = min(size, len(self._value))
        ctypes.memmove(buf, self._value, n)
        return n


class NonDarwinTests(unittest.TestCase):
    """Off darwin, no ctypes load happens at all."""

    def test_quarantined_path_none_without_loading_ctypes(self) -> None:
        for platform in ("linux", "win32"):
            with self.subTest(platform=platform):
                with (
                    unittest.mock.patch("fleet.macos_quarantine.sys.platform", platform),
                    unittest.mock.patch("ctypes.CDLL") as cdll,
                ):
                    self.assertIsNone(q.quarantined_path("/some/bin"))
                    cdll.assert_not_called()

    def test_quarantine_value_none_without_loading_ctypes(self) -> None:
        with (
            unittest.mock.patch("fleet.macos_quarantine.sys.platform", "linux"),
            unittest.mock.patch("ctypes.CDLL") as cdll,
        ):
            self.assertIsNone(q.quarantine_value("/some/bin"))
            cdll.assert_not_called()


class DarwinFakeLibcTests(unittest.TestCase):
    """darwin behavior with a faked libc handle — no real xattr syscalls."""

    def _value(self, value: bytes | None):
        stack = contextlib.ExitStack()
        stack.enter_context(unittest.mock.patch("fleet.macos_quarantine.sys.platform", "darwin"))
        stack.enter_context(
            unittest.mock.patch(
                "fleet.macos_quarantine._libc_handle", return_value=_FakeLibc(value)
            )
        )
        return stack

    def test_present_returns_the_value(self) -> None:
        raw = b"0081;00000000;Safari;12345678-1234-1234-1234-123456789012"
        with self._value(raw):
            self.assertEqual(q.quarantine_value("/bin/zellij"), raw.decode("ascii"))

    def test_absent_returns_none(self) -> None:
        with self._value(None):
            self.assertIsNone(q.quarantine_value("/bin/zellij"))

    def test_quarantined_path_realpath_when_present(self) -> None:
        with (
            self._value(b"0081;0;Safari;uuid"),
            unittest.mock.patch(
                "fleet.macos_quarantine.os.path.realpath", return_value="/real/bin/zellij"
            ),
        ):
            self.assertEqual(q.quarantined_path("/bin/zellij"), "/real/bin/zellij")

    def test_quarantined_path_none_when_absent(self) -> None:
        with self._value(None):
            self.assertIsNone(q.quarantined_path("/bin/zellij"))

    def test_loader_failure_is_none(self) -> None:
        with (
            unittest.mock.patch("fleet.macos_quarantine.sys.platform", "darwin"),
            unittest.mock.patch("fleet.macos_quarantine._libc_handle", return_value=None),
        ):
            self.assertIsNone(q.quarantine_value("/bin/zellij"))
            self.assertIsNone(q.quarantined_path("/bin/zellij"))

    def test_cdll_raising_is_none(self) -> None:
        # _libc_handle itself must swallow a load failure (dlopen-style
        # OSError, or an ABI without getxattr → AttributeError).
        q._libc = None
        q._libc_load_failed = False
        self.addCleanup(setattr, q, "_libc", None)
        self.addCleanup(setattr, q, "_libc_load_failed", False)
        with (
            unittest.mock.patch("fleet.macos_quarantine.sys.platform", "darwin"),
            unittest.mock.patch("ctypes.CDLL", side_effect=OSError("no libc")),
        ):
            self.assertIsNone(q.quarantine_value("/bin/zellij"))

    def test_symlink_uses_realpath_of_the_target(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "real-zellij"
            target.write_text("x")
            link = Path(tmp) / "zellij"
            link.symlink_to(target)
            with self._value(b"0081;0;curl;uuid"):
                self.assertEqual(q.quarantined_path(str(link)), str(target.resolve()))


class FixHintTests(unittest.TestCase):
    def test_with_formula(self) -> None:
        hint = q.fix_hint("/opt/homebrew/bin/zellij", brew_formula="zellij")
        self.assertIn("brew install zellij", hint)
        self.assertIn("xattr -d com.apple.quarantine /opt/homebrew/bin/zellij", hint)
        self.assertIn("confirming", hint)

    def test_without_formula(self) -> None:
        hint = q.fix_hint("/opt/bin/claude")
        self.assertNotIn("brew install", hint)
        self.assertIn("xattr -d com.apple.quarantine /opt/bin/claude", hint)
        self.assertIn("confirming", hint)


@unittest.skipUnless(sys.platform == "darwin", "live xattr test: darwin only")
class LiveXattrTests(unittest.TestCase):
    """Real ``getxattr`` against a real (non-executable) temp file."""

    def test_plain_file_is_not_quarantined(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain"
            path.write_text("x")
            self.assertIsNone(q.quarantined_path(str(path)))

    def test_xattr_write_is_detected(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "quarantined"
            path.write_text("x")
            subprocess.run(
                [
                    "/usr/bin/xattr",
                    "-w",
                    "com.apple.quarantine",
                    "0081;00000000;curl;12345678-1234-1234-1234-123456789012",
                    str(path),
                ],
                check=True,
            )
            self.assertEqual(q.quarantined_path(str(path)), str(path.resolve()))


if __name__ == "__main__":
    unittest.main()
