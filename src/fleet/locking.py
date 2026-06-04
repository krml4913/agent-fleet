"""flock + atomic rename helper.

The pattern is: open ``<path>.lock`` and hold ``fcntl.flock(LOCK_EX)``;
write the new contents to a sibling temp file; ``os.replace`` it onto the
target (atomic on POSIX). This combines:

  * **mutual exclusion**  — only one writer at a time per target
  * **all-or-nothing**    — readers never see a half-written file
  * **partial-update ban** — callers always rewrite the whole file

See design doc §5.4 ("race mitigation").
"""
from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, IO, Iterator


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
    with open(lock_path, "a+") as lock_fp:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
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
                os.replace(tmp_path, target)
            except BaseException:
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)


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

    with open(lock_path, "a+") as lock_fp:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
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
                os.replace(tmp_path, target)
            except BaseException:
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
                raise
            return new
        finally:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
