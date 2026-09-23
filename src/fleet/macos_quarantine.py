"""macOS Gatekeeper quarantine detection (``com.apple.quarantine`` xattr).

A binary carrying this xattr triggers a Gatekeeper dialog on first exec —
and that dialog has a "Move to Trash" button that deletes it (#309). Callers
must check before exec'ing, not after. This module is read-only: it never
writes or removes the xattr; removing it is a security decision only the
user can make.

No primary source (Apple open-source headers/docs) could be found for the
meaning of individual bits in the quarantine value's flags field (e.g. the
commonly cited "user approved" bit 0x0040 — security-research blog posts
only, not Apple sources), so this module does not try to parse or special-
case them: presence of the xattr, at all, means quarantined.
"""
from __future__ import annotations

import ctypes
import os
import sys

#: The xattr this module looks for. Its value (``flags;timestamp;agent;uuid``)
#: is read but not parsed — see the module docstring.
QUARANTINE_XATTR = "com.apple.quarantine"

_libc: ctypes.CDLL | None = None
_libc_load_failed = False


def _libc_handle() -> ctypes.CDLL | None:
    """The ``getxattr``-bearing libc handle (cached), or ``None`` if unavailable."""
    global _libc, _libc_load_failed
    if _libc is not None:
        return _libc
    if _libc_load_failed:
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.getxattr.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_int,
        ]
        libc.getxattr.restype = ctypes.c_ssize_t
    except (OSError, AttributeError):
        _libc_load_failed = True
        return None
    _libc = libc
    return libc


def quarantine_value(path: str) -> str | None:
    """The ``com.apple.quarantine`` xattr of ``path`` (symlinks followed).

    ``None`` means: not darwin, the attribute is absent, or it could not be
    read for any reason (fail-open — the caller then behaves as it did
    before this check existed, i.e. it may proceed to exec the binary).
    """
    if sys.platform != "darwin":
        return None
    libc = _libc_handle()
    if libc is None:
        return None
    try:
        path_b = os.fsencode(path)
    except (TypeError, ValueError):
        return None
    name_b = QUARANTINE_XATTR.encode("ascii")
    try:
        # options=0 follows symlinks (XATTR_NOFOLLOW would not); position=0
        # is unused for this xattr (only relevant to resource forks).
        size = libc.getxattr(path_b, name_b, None, 0, 0, 0)
        if size < 0:
            return None
        if size == 0:
            return ""
        buf = ctypes.create_string_buffer(size)
        size = libc.getxattr(path_b, name_b, buf, size, 0, 0)
        if size < 0:
            return None
        return buf.raw[:size].decode("utf-8", errors="replace")
    except OSError:
        return None


def quarantined_path(binary: str) -> str | None:
    """``os.path.realpath(binary)`` if it carries the quarantine xattr, else ``None``.

    Returns ``None`` immediately off darwin (no ctypes load).
    """
    if sys.platform != "darwin":
        return None
    if quarantine_value(binary) is None:
        return None
    return os.path.realpath(binary)


def fix_hint(path: str, *, brew_formula: str | None = None) -> str:
    """How to unblock ``path``: reinstall via brew (if named), or remove the xattr."""
    manual = (
        f"after confirming where the binary came from: "
        f"`xattr -d com.apple.quarantine {path}`"
    )
    if brew_formula:
        return f"install via `brew install {brew_formula}`, or {manual}"
    return manual


__all__ = ["QUARANTINE_XATTR", "quarantine_value", "quarantined_path", "fix_hint"]
