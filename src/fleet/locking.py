"""flock + atomic rename helper.

The pattern is: open ``<path>.lock`` and hold an exclusive lock on it
(``fcntl.flock(LOCK_EX)`` on POSIX, ``msvcrt.locking`` on byte 0 on Windows);
write the new contents to a sibling temp file; ``os.replace`` it onto the
target (atomic on POSIX). This combines:

  * **mutual exclusion**  — only one writer at a time per target
  * **all-or-nothing**    — readers never see a half-written file
  * **partial-update ban** — callers always rewrite the whole file

See design doc §5.4 ("race mitigation").
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, IO, Iterator

if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
    import msvcrt

    fcntl = None
else:
    import fcntl

    msvcrt = None

_IS_WINDOWS = sys.platform == "win32"

# Windows: how long to sleep between non-blocking attempts while "blocking".
_WIN_LOCK_POLL_SECONDS = 0.05

# Windows: ``os.replace`` fails with PermissionError while another process
# holds the target open (CPython opens files without FILE_SHARE_DELETE).
# Readers only keep the file open briefly, so a short bounded retry suffices.
_REPLACE_RETRIES = 50
_REPLACE_RETRY_SECONDS = 0.02


def lock_file(fp: IO, *, blocking: bool = True) -> bool:
    """Take an exclusive advisory lock on the open file ``fp``.

    POSIX: ``fcntl.flock(LOCK_EX[|LOCK_NB])``.
    Windows: ``msvcrt.locking`` on byte 0 with ``LK_NBLCK``; when
    ``blocking`` the non-blocking attempt is retried until it succeeds
    (``LK_LOCK`` gives up after ~10 seconds, flock never does).

    Returns True when the lock was acquired. With ``blocking=False`` returns
    False if another holder has it; with ``blocking=True`` it only returns
    True (or raises on an unexpected error).
    """
    fd = fp.fileno()
    if not _IS_WINDOWS:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except OSError:
            if blocking:
                raise
            return False
        return True
    while True:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            # EACCES / EDEADLOCK: the byte is locked by someone else.
            if not blocking:
                return False
            time.sleep(_WIN_LOCK_POLL_SECONDS)


def unlock_file(fp: IO) -> None:
    """Release a lock taken with :func:`lock_file`."""
    fd = fp.fileno()
    if not _IS_WINDOWS:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def replace_file(src: Path, dst: Path) -> None:
    """``os.replace`` with a short bounded retry on Windows ``PermissionError``."""
    if not _IS_WINDOWS:
        os.replace(src, dst)
        return
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_RETRY_SECONDS)


@contextmanager
def atomic_write(path: Path, *, encoding: str = "utf-8") -> Iterator[IO[str]]:
    """Yield a file handle whose contents will be atomically committed to ``path``.

    On normal exit the temp file is ``os.replace``'d onto ``path``. On an
    exception the temp file is unlinked and the exception propagates.

    Concurrency: holds an exclusive ``flock`` on a sibling ``<path>.lock``
    file for the entire duration. Lock files are left behind intentionally
    (zero-byte, reusable).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    lock_path = target.with_name(target.name + ".lock")

    # Use a long-lived lock file (do not delete it — only the lock matters,
    # and unlinking it would race with concurrent acquirers).
    with open(lock_path, "a+", encoding="utf-8") as lock_fp:
        lock_file(lock_fp)
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding=encoding) as tmp_f:
                    yield tmp_f
                    tmp_f.flush()
                    os.fsync(tmp_f.fileno())
                replace_file(tmp_path, target)
            except BaseException:
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
                raise
        finally:
            unlock_file(lock_fp)


def atomic_update(
    path: Path,
    mutate: Callable[[str], str],
    *,
    encoding: str = "utf-8",
) -> str:
    """Lock-guarded read-modify-write of a whole file.

    Unlike :func:`atomic_write` — which only guards the *write* — this holds
    the exclusive ``flock`` across both the read and the write, so a
    concurrent read-modify-write (e.g. two ``fleet init`` registering into
    ``projects.yaml``) cannot lose an update.

    Reuses the same flock + atomic-rename mechanism as :func:`atomic_write`:
    the current file contents (``""`` if absent) are passed to ``mutate``,
    whose return value is committed via ``os.replace``. Returns the new text.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    lock_path = target.with_name(target.name + ".lock")

    with open(lock_path, "a+", encoding="utf-8") as lock_fp:
        lock_file(lock_fp)
        try:
            old = target.read_text(encoding=encoding) if target.exists() else ""
            new = mutate(old)

            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding=encoding) as tmp_f:
                    tmp_f.write(new)
                    tmp_f.flush()
                    os.fsync(tmp_f.fileno())
                replace_file(tmp_path, target)
            except BaseException:
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
                raise
            return new
        finally:
            unlock_file(lock_fp)
