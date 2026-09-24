"""Windows-only: a fleet-owned AUMID + the ``fleet://`` URL protocol (Issue #317).

Two independent, per-user, **HKCU-only** registrations (no admin rights, no
Start-menu shortcut):

- ``HKCU\\Software\\Classes\\AppUserModelId\\agent-fleet`` — a bare
  ``DisplayName`` value. This is the "registry-only" AUMID route the issue
  asked to try first. **Verified live** on Windows 10 Pro 19045
  (2026-09-23): a toast shown via
  ``ToastNotificationManager.CreateToastNotifier("agent-fleet")`` landed in
  ``ToastNotificationManager.History.GetHistory("agent-fleet")`` (Action
  Center) with just that key in place — no Start-menu shortcut needed. The
  shortcut fallback the issue described is therefore not implemented.
- ``HKCU\\Software\\Classes\\fleet`` (+ ``shell\\open\\command``) — registers
  ``fleet://`` so Windows can invoke ``fleet url-handler <uri>`` on a toast
  click. The command runs under **pythonw** (falling back to the running
  interpreter) so no console window flashes when a toast is clicked.

Both registrations are created together by :func:`setup` and removed together
by :func:`teardown`; both are idempotent. :func:`is_setup_done` (used by
``fleet preflight`` and by :mod:`fleet.notify` to choose the toast AUMID) is
read-only and never raises.

``winreg`` is stdlib-only but Windows-only; it is imported defensively so this
module (and anything that imports it) stays importable on macOS/Linux CI. Unit
tests patch the module-level ``winreg`` name with a fake registry (never touch
the real one) — see ``tests/test_windows_notify_setup.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

try:
    import winreg  # type: ignore[import-not-found]
except ImportError:  # non-Windows: every public function degrades to "not set up"
    winreg = None  # type: ignore[assignment]

#: The AUMID fleet registers for itself, and the display name shown as the
#: toast's sender once it is registered.
AUMID = "agent-fleet"
AUMID_DISPLAY_NAME = "agent-fleet"

_AUMID_KEY = f"Software\\Classes\\AppUserModelId\\{AUMID}"
_PROTOCOL_SCHEME = "fleet"
_PROTOCOL_KEY = f"Software\\Classes\\{_PROTOCOL_SCHEME}"
_PROTOCOL_COMMAND_KEY = f"{_PROTOCOL_KEY}\\shell\\open\\command"

# Full HKCU paths, for CLI/log output only (never fed back into winreg calls,
# which take the HKCU-relative path above).
AUMID_KEY_DISPLAY = f"HKCU\\{_AUMID_KEY}"
PROTOCOL_KEY_DISPLAY = f"HKCU\\{_PROTOCOL_KEY}"


class SetupResult(NamedTuple):
    aumid_created: bool  # False when the AUMID key already existed
    protocol_created: bool  # False when the fleet:// key already existed
    command: str  # the shell\\open\\command value that was written


class TeardownResult(NamedTuple):
    aumid_removed: bool  # False when there was no AUMID key to remove
    protocol_removed: bool  # False when there was no fleet:// key to remove


def is_windows() -> bool:
    return sys.platform == "win32"


def _hkcu():
    return winreg.HKEY_CURRENT_USER


def _key_exists(path: str) -> bool:
    if winreg is None:
        return False
    try:
        key = winreg.OpenKey(_hkcu(), path)
    except OSError:
        return False
    winreg.CloseKey(key)
    return True


def is_aumid_configured() -> bool:
    """True when fleet's own AUMID key is registered."""
    return _key_exists(_AUMID_KEY)


def is_protocol_configured() -> bool:
    """True when the ``fleet://`` protocol's command key is registered."""
    return _key_exists(_PROTOCOL_COMMAND_KEY)


def is_setup_done() -> bool:
    """True when both registrations are in place (``fleet notify setup-windows`` ran)."""
    return is_aumid_configured() and is_protocol_configured()


def chosen_app_id(fallback: str) -> str:
    """The AUMID to show toasts under: fleet's own once configured, else *fallback*."""
    return AUMID if is_aumid_configured() else fallback


def _resolve_pythonw() -> str:
    """Prefer ``pythonw.exe`` next to the running interpreter (no console flash).

    Falls back to the running interpreter itself (a console briefly flashes,
    but the handler still runs) when no ``pythonw.exe`` sits alongside it.
    """
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    if candidate.is_file():
        return str(candidate)
    return str(exe)


def _clone_root() -> Path:
    # src/fleet/windows_notify_setup.py -> parents[2] = clone root
    return Path(__file__).resolve().parents[2]


def _handler_command() -> str:
    """The ``shell\\open\\command`` value: pythonw running ``fleet url-handler``."""
    fleet_script = _clone_root() / "fleet"
    pythonw = _resolve_pythonw()
    return f'"{pythonw}" "{fleet_script}" url-handler "%1"'


def _require_windows() -> None:
    if not is_windows() or winreg is None:
        raise RuntimeError(
            "Windows notification setup (fleet notify setup-windows / "
            "teardown-windows) is only available on Windows"
        )


def setup() -> SetupResult:
    """Idempotently create the AUMID + ``fleet://`` protocol keys (HKCU only).

    Safe to call repeatedly: existing keys are left with their values
    overwritten to the current ones (no drift), and ``*_created`` reports
    whether each key was newly created by this call.
    """
    _require_windows()

    aumid_created = not is_aumid_configured()
    key = winreg.CreateKeyEx(_hkcu(), _AUMID_KEY)
    try:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, AUMID_DISPLAY_NAME)
    finally:
        winreg.CloseKey(key)

    protocol_created = not is_protocol_configured()
    proto_key = winreg.CreateKeyEx(_hkcu(), _PROTOCOL_KEY)
    try:
        winreg.SetValueEx(proto_key, "", 0, winreg.REG_SZ, "URL:fleet Protocol")
        winreg.SetValueEx(proto_key, "URL Protocol", 0, winreg.REG_SZ, "")
    finally:
        winreg.CloseKey(proto_key)

    command = _handler_command()
    cmd_key = winreg.CreateKeyEx(_hkcu(), _PROTOCOL_COMMAND_KEY)
    try:
        winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, command)
    finally:
        winreg.CloseKey(cmd_key)

    return SetupResult(
        aumid_created=aumid_created, protocol_created=protocol_created, command=command
    )


def teardown() -> TeardownResult:
    """Remove exactly the keys :func:`setup` creates. Idempotent: missing keys are fine."""
    _require_windows()

    aumid_removed = is_aumid_configured()
    _delete_key_tree(_AUMID_KEY)
    protocol_removed = is_protocol_configured()
    _delete_key_tree(_PROTOCOL_KEY)
    return TeardownResult(aumid_removed=aumid_removed, protocol_removed=protocol_removed)


def _delete_key_tree(key_path: str) -> None:
    """Recursively delete *key_path* under HKCU. No-op when it does not exist."""
    try:
        key = winreg.OpenKey(_hkcu(), key_path, 0, winreg.KEY_ALL_ACCESS)
    except OSError:
        return
    try:
        while True:
            try:
                sub = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_key_tree(f"{key_path}\\{sub}")
    finally:
        winreg.CloseKey(key)
    winreg.DeleteKey(_hkcu(), key_path)


__all__ = [
    "AUMID",
    "AUMID_DISPLAY_NAME",
    "AUMID_KEY_DISPLAY",
    "PROTOCOL_KEY_DISPLAY",
    "SetupResult",
    "TeardownResult",
    "is_windows",
    "is_aumid_configured",
    "is_protocol_configured",
    "is_setup_done",
    "chosen_app_id",
    "setup",
    "teardown",
]
