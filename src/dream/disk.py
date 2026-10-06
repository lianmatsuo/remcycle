"""Writing remcycle's files and moving them into place, the same on every system."""

import os
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path

PATIENCE = 2.0 if sys.platform == "win32" else 0.0
"""Seconds a move is tried again for after it is refused. Windows refuses to move a file while
another process has it open, and a folder while one has a file in it open, so a reader passing at
that moment is waited out."""

_STEP = 0.02


def put(file: Path, text: str) -> None:
    """Replace the file whole, so a reader never finds it half written.

    Each writer works in a file of its own, so two writing at once do not take each other's away.
    The text is stored as UTF-8 with `\\n` line ends, whatever the system would choose.
    """
    fresh = file.with_name(f"{file.name}.{os.getpid()}.{secrets.token_hex(4)}.new")
    try:
        fresh.write_text(text, encoding="utf-8", newline="\n")
        replace(fresh, file)
    finally:
        fresh.unlink(missing_ok=True)


def replace(source: Path, target: Path) -> None:
    """Move a file to `target`, in place of the file already there."""
    _patiently(os.replace, source, target)


def rename(source: Path, target: Path) -> None:
    """Move a file or a folder to `target`, where nothing is yet."""
    _patiently(os.rename, source, target)


def _patiently(move: Callable[[Path, Path], None], source: Path, target: Path) -> None:
    deadline = time.monotonic() + PATIENCE
    while True:
        try:
            move(source, target)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(_STEP)
        else:
            return
