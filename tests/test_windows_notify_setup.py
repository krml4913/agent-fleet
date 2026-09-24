"""Tests for ``fleet.windows_notify_setup`` (#317) — AUMID + fleet:// protocol.

Hermetic: every test patches the module's ``winreg`` name with
:class:`FakeWinReg`, an in-memory registry. No test ever touches the real
Windows registry, on any host OS (including a Windows CI/dev box).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "vendor"))

from fleet import windows_notify_setup as wns  # noqa: E402


class FakeWinReg:
    """A minimal in-memory stand-in for the stdlib ``winreg`` module.

    Keys are addressed by their HKCU-relative path string (``wns`` never
    passes any other hive). ``DeleteKey`` mirrors real Win32 behavior: it
    refuses a key that still has subkeys, which is exactly why
    ``_delete_key_tree`` recurses bottom-up.
    """

    HKEY_CURRENT_USER = object()
    REG_SZ = 1
    KEY_ALL_ACCESS = 0xF003F
    KEY_READ = 0x20019

    class _Handle:
        def __init__(self, path: str) -> None:
            self.path = path

    def __init__(self) -> None:
        self._keys: dict[str, dict[str, tuple]] = {}  # path -> {value_name: (value, type)}
        self._children: dict[str, set[str]] = {}  # path -> {child leaf names}

    def _ensure_lineage(self, path: str) -> None:
        parts = path.split("\\")
        for i in range(len(parts)):
            here = "\\".join(parts[: i + 1])
            self._keys.setdefault(here, {})
            self._children.setdefault(here, set())
            if i > 0:
                parent = "\\".join(parts[:i])
                self._children[parent].add(parts[i])

    def CreateKeyEx(self, hkey, subkey, *a, **kw):
        self._ensure_lineage(subkey)
        return self._Handle(subkey)

    def OpenKey(self, hkey, subkey, reserved=0, access=0):
        if subkey not in self._keys:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return self._Handle(subkey)

    def SetValueEx(self, key, name, reserved, type_, value):
        self._keys[key.path][name] = (value, type_)

    def QueryValueEx(self, key, name):
        return self._keys[key.path][name]

    def EnumKey(self, key, index):
        children = sorted(self._children.get(key.path, ()))
        if index >= len(children):
            raise OSError("no more data is available")
        return children[index]

    def CloseKey(self, key) -> None:
        pass

    def DeleteKey(self, hkey, subkey) -> None:
        if subkey not in self._keys:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        if self._children.get(subkey):
            raise OSError("access is denied")  # real winreg: key has subkeys
        del self._keys[subkey]
        del self._children[subkey]
        parts = subkey.split("\\")
        if len(parts) > 1:
            parent = "\\".join(parts[:-1])
            self._children.get(parent, set()).discard(parts[-1])

    # Test-only introspection.
    def exists(self, path: str) -> bool:
        return path in self._keys

    def value(self, path: str, name: str):
        return self._keys[path][name][0]


class WindowsNotifySetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeWinReg()
        patches = [
            mock.patch.object(wns, "winreg", self.fake),
            mock.patch.object(wns, "is_windows", return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    # -- setup / teardown key set -------------------------------------------------

    def test_setup_creates_aumid_key_with_display_name(self) -> None:
        wns.setup()
        self.assertTrue(self.fake.exists(wns._AUMID_KEY))
        self.assertEqual(
            self.fake.value(wns._AUMID_KEY, "DisplayName"), wns.AUMID_DISPLAY_NAME
        )

    def test_setup_creates_protocol_key_and_command(self) -> None:
        wns.setup()
        self.assertTrue(self.fake.exists(wns._PROTOCOL_KEY))
        self.assertEqual(self.fake.value(wns._PROTOCOL_KEY, "URL Protocol"), "")
        self.assertTrue(self.fake.exists(wns._PROTOCOL_COMMAND_KEY))
        command = self.fake.value(wns._PROTOCOL_COMMAND_KEY, "")
        self.assertIn("url-handler", command)
        self.assertIn('"%1"', command)

    def test_setup_reports_created_on_first_run(self) -> None:
        result = wns.setup()
        self.assertTrue(result.aumid_created)
        self.assertTrue(result.protocol_created)

    def test_setup_is_idempotent(self) -> None:
        wns.setup()
        result = wns.setup()
        self.assertFalse(result.aumid_created)
        self.assertFalse(result.protocol_created)
        # Values are unchanged, not duplicated / corrupted.
        self.assertEqual(
            self.fake.value(wns._AUMID_KEY, "DisplayName"), wns.AUMID_DISPLAY_NAME
        )

    def test_setup_creates_only_the_documented_keys(self) -> None:
        wns.setup()
        self.assertEqual(
            set(self.fake._keys),
            {
                "Software",
                "Software\\Classes",
                "Software\\Classes\\AppUserModelId",
                "Software\\Classes\\AppUserModelId\\agent-fleet",
                "Software\\Classes\\fleet",
                "Software\\Classes\\fleet\\shell",
                "Software\\Classes\\fleet\\shell\\open",
                "Software\\Classes\\fleet\\shell\\open\\command",
            },
        )

    def test_teardown_removes_exactly_what_setup_created(self) -> None:
        wns.setup()
        wns.teardown()
        self.assertFalse(self.fake.exists(wns._AUMID_KEY))
        self.assertFalse(self.fake.exists(wns._PROTOCOL_KEY))
        self.assertFalse(self.fake.exists(wns._PROTOCOL_COMMAND_KEY))

    def test_teardown_reports_what_was_removed(self) -> None:
        wns.setup()
        result = wns.teardown()
        self.assertTrue(result.aumid_removed)
        self.assertTrue(result.protocol_removed)

    def test_teardown_on_never_set_up_is_a_noop(self) -> None:
        result = wns.teardown()
        self.assertFalse(result.aumid_removed)
        self.assertFalse(result.protocol_removed)
        # Must not raise, must not leave stray keys.
        self.assertEqual(self.fake._keys, {})

    def test_teardown_leaves_unrelated_keys_alone(self) -> None:
        # A sibling AppUserModelId key (someone else's app) must survive.
        self.fake._ensure_lineage("Software\\Classes\\AppUserModelId\\SomeOtherApp")
        wns.setup()
        wns.teardown()
        self.assertTrue(self.fake.exists("Software\\Classes\\AppUserModelId\\SomeOtherApp"))
        self.assertTrue(self.fake.exists("Software\\Classes\\AppUserModelId"))

    # -- is_aumid_configured / is_protocol_configured / is_setup_done -------------

    def test_not_configured_before_setup(self) -> None:
        self.assertFalse(wns.is_aumid_configured())
        self.assertFalse(wns.is_protocol_configured())
        self.assertFalse(wns.is_setup_done())

    def test_configured_after_setup(self) -> None:
        wns.setup()
        self.assertTrue(wns.is_aumid_configured())
        self.assertTrue(wns.is_protocol_configured())
        self.assertTrue(wns.is_setup_done())

    def test_partial_state_is_not_setup_done(self) -> None:
        # Only the AUMID half exists (e.g. a half-finished setup).
        key = self.fake.CreateKeyEx(None, wns._AUMID_KEY)
        self.fake.SetValueEx(key, "DisplayName", 0, self.fake.REG_SZ, wns.AUMID_DISPLAY_NAME)
        self.assertTrue(wns.is_aumid_configured())
        self.assertFalse(wns.is_protocol_configured())
        self.assertFalse(wns.is_setup_done())

    # -- chosen_app_id -------------------------------------------------------------

    def test_chosen_app_id_falls_back_when_unconfigured(self) -> None:
        self.assertEqual(wns.chosen_app_id("fallback-id"), "fallback-id")

    def test_chosen_app_id_uses_fleet_aumid_once_configured(self) -> None:
        wns.setup()
        self.assertEqual(wns.chosen_app_id("fallback-id"), wns.AUMID)

    # -- non-Windows degradation ----------------------------------------------------

    def test_non_windows_never_touches_winreg(self) -> None:
        with mock.patch.object(wns, "is_windows", return_value=False):
            self.assertFalse(wns.is_setup_done())
            with self.assertRaises(RuntimeError):
                wns.setup()
            with self.assertRaises(RuntimeError):
                wns.teardown()

    def test_winreg_none_degrades_to_not_configured(self) -> None:
        # The real import-time state on macOS/Linux: winreg is None.
        with mock.patch.object(wns, "winreg", None):
            self.assertFalse(wns.is_aumid_configured())
            self.assertFalse(wns.is_protocol_configured())
            self.assertFalse(wns.is_setup_done())
            self.assertEqual(wns.chosen_app_id("fallback-id"), "fallback-id")


class HandlerCommandTests(unittest.TestCase):
    """The ``shell\\open\\command`` value: pythonw (preferred) + the fleet script."""

    def test_prefers_pythonw_next_to_the_interpreter(self) -> None:
        with mock.patch.object(sys, "executable", r"C:\Python313\python.exe"), \
             mock.patch.object(Path, "is_file", return_value=True):
            command = wns._handler_command()
        self.assertIn("pythonw.exe", command)
        self.assertIn("url-handler", command)
        self.assertTrue(command.endswith('"%1"'))

    def test_falls_back_to_running_interpreter_without_pythonw(self) -> None:
        with mock.patch.object(sys, "executable", r"C:\Python313\python.exe"), \
             mock.patch.object(Path, "is_file", return_value=False):
            command = wns._handler_command()
        self.assertIn("python.exe", command)
        self.assertNotIn("pythonw.exe", command)


if __name__ == "__main__":
    unittest.main()
