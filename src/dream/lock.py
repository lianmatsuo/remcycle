"""A lock on a file, held by one process at a time, on every system the command runs on."""

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_STEP = 0.05
"""Seconds between two tries at a lock another process holds."""

if sys.platform == "win32":
    import msvcrt

    def _take(lock: int) -> bool:
        os.lseek(lock, 0, os.SEEK_SET)
        try:
            msvcrt.locking(lock, msvcrt.LK_NBLCK, 1)
        except PermissionError:
            return False
        return True

    def _let_go(lock: int) -> None:
        os.lseek(lock, 0, os.SEEK_SET)
        msvcrt.locking(lock, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _take(lock: int) -> bool:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _let_go(lock: int) -> None:
        fcntl.flock(lock, fcntl.LOCK_UN)


class Busy(Exception):
    """Another process held the lock for longer than the wait."""


@contextmanager
def held(file: Path, wait: float | None = None) -> Iterator[None]:
    """Hold the lock on `file` for as long as the block runs. The file is made if it is not there.

    While another process holds it, this waits: until that one lets go, or with `wait` for that
    many seconds, after which it raises Busy.
    """
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8") as lock:
        deadline = None if wait is None else time.monotonic() + wait
        while not _take(lock.fileno()):
            if deadline is not None and time.monotonic() >= deadline:
                raise Busy(f"{file} is held by another process")
            time.sleep(_STEP)
        try:
            yield
        finally:
            _let_go(lock.fileno())
