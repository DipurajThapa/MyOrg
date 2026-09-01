#!/usr/bin/env python3
"""Cross-platform exclusive file lock for the Company OS state stores.

POSIX locks with ``fcntl.flock``; Windows locks with ``msvcrt.locking``. Both are
wrapped in the same bounded polling acquire, so lock semantics are identical on
either platform and no caller can block forever waiting on a stale holder.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.05
LOCK_REGION_BYTES = 1

try:  # POSIX
    import fcntl

    def _try_acquire(handle) -> bool:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _release(handle) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)

except ImportError:  # Windows
    import msvcrt

    def _try_acquire(handle) -> bool:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, LOCK_REGION_BYTES)
        except OSError:
            return False
        return True

    def _release(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, LOCK_REGION_BYTES)


@contextmanager
def exclusive_lock(lock_path: Path, timeout: float = LOCK_TIMEOUT_SECONDS):
    """Hold an exclusive lock on ``lock_path``, creating it and its parent if absent."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        deadline = time.monotonic() + timeout
        while not _try_acquire(handle):
            if time.monotonic() >= deadline:
                raise SystemExit(f"could not acquire lock within {timeout:g}s: {lock_path}")
            time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            _release(handle)
