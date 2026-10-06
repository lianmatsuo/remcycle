"""Writing remcycle's files, moving them into place and taking them away, the same on every system."""

import errno
import os
import secrets
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

PATIENCE = 2.0 if sys.platform == "win32" else 0.0
"""Seconds a move or a delete is tried again for after it is refused. Windows refuses to move or
delete a file while another process has it open, and a folder while one has a file in it open,
so a reader passing at that moment is waited out."""

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
        remove(fresh)


def replace(source: Path, target: Path) -> None:
    """Move a file to `target`, in place of the file already there."""
    _patiently(os.replace, source, target)


def rename(source: Path, target: Path) -> None:
    """Move a file or a folder to `target`, where nothing is yet."""
    _patiently(os.rename, source, target)


def remove(file: Path) -> None:
    """Delete a file, if it is there."""
    try:
        _patiently(os.unlink, file)
    except FileNotFoundError:
        pass


def clear(folder: Path) -> None:
    """Delete a folder and everything in it."""
    # A file deleted while another process has it open can keep its place in the folder until
    # that process lets go, and the folder is then refused as not yet empty.
    _patiently(shutil.rmtree, folder, also=errno.ENOTEMPTY)


def _patiently(act: Callable[..., None], *paths: Path, also: int | None = None) -> None:
    """Do it, and while the system refuses for want of permission, or with the error `also`, try again until patience runs out."""
    deadline = time.monotonic() + PATIENCE
    while True:
        try:
            act(*paths)
        except OSError as e:
            is_refusal = isinstance(e, PermissionError) or (also is not None and e.errno == also)
            if not is_refusal or time.monotonic() >= deadline:
                raise
            time.sleep(_STEP)
        else:
            return
