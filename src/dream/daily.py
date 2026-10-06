"""The daily dream: started by a session when one is due, so the dream runs with no scheduler to set up."""

import json
import os
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from dream import disk, lock

DUE_AFTER = timedelta(hours=20)
"""How long after one daily dream started the next may start."""
WEEK = timedelta(days=7)
_COMING_UP = timedelta(minutes=1)
"""How long a daily dream that was just started is given to take the lock it runs under."""


@dataclass(frozen=True)
class State:
    began: str | None = None
    """When this machine's first daily dream was asked for. The chosen history counts back from it."""
    started: str | None = None
    finished: str | None = None
    failed: str | None = None
    """Why the last daily dream failed, or None if it did not."""
    log: str | None = None
    pid: int | None = None
    """The process the last daily dream runs in."""


def load(file: Path) -> State:
    if not file.exists():
        return State()
    return State(**json.loads(file.read_text(encoding="utf-8")))


def save(file: Path, state: State) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    disk.put(file, json.dumps(asdict(state)))


def begin(state: State, now: str) -> State:
    return state if state.began else replace(state, began=now)


def cutoff(history: str | None, began: str) -> str | None:
    """The moment before which sessions are left for an explicit `dream run`. None reads them all."""
    if history == "all":
        return None
    if history == "week":
        return (datetime.fromisoformat(began) - WEEK).isoformat()
    return began


def is_due(state: State, now: str) -> bool:
    return state.started is None or datetime.fromisoformat(now) - datetime.fromisoformat(state.started) >= DUE_AFTER


# What ties a process to the Claude Code session that started it. The background run outlives that
# session, so it must not reach for the session's host, its sign-in refresh or its plugins. What
# chooses how Claude Code signs in at all (an API key, Bedrock, a config folder) is kept.
_SESSION_TIES = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_HOST_SESSION_ID",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH",
        "CLAUDE_CODE_SESSION_ATTENDED",
        "CLAUDE_CODE_OAUTH_SCOPES",
        "CLAUDE_CODE_PLUGIN_DIRS",
        "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS",
        "CLAUDE_EFFORT",
        "CLAUDE_PID",
    }
)


if sys.platform == "win32":
    # No console window opens for the run, and it heads a process group of its own, which a Ctrl+C typed
    # in the session's terminal is not sent to.
    _CUT_LOOSE = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
else:
    _CUT_LOOSE = {"start_new_session": True}


def start(argv: list[str], log: Path, env: Mapping[str, str] | None = None) -> int:
    """Run argv cut loose from this process, and return the new process's id.

    The daily dream is started this way so as to outlast the Claude Code session that starts it.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    loose = {name: value for name, value in (os.environ if env is None else env).items() if name not in _SESSION_TIES}
    with log.open("a", encoding="utf-8", newline="\n") as out:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=out, stderr=out, cwd=log.parent, env=loose, **_CUT_LOOSE
        )
    return process.pid


@contextmanager
def running(file: Path) -> Iterator[None]:
    """Hold, for as long as the block runs, the lock that says a daily dream is running. `file` is where its state is kept.

    Raises lock.Busy if another daily dream holds it.
    """
    with lock.held(_running_lock(file), wait=5.0):
        yield


def is_running(file: Path, state: State, now: str) -> bool:
    """Whether the daily dream the state describes is still running.

    It is while it holds its lock, which the system lets go of when the process ends, however it
    ends. The number of its process says less: once the process is gone the system gives the
    number out again. So the number is only believed for the moment before the run has its lock.
    """
    try:
        with lock.held(_running_lock(file), wait=0):
            pass
    except lock.Busy:
        return True
    if state.started is None or datetime.fromisoformat(now) - datetime.fromisoformat(state.started) >= _COMING_UP:
        return False
    return is_alive(state.pid)


def _running_lock(file: Path) -> Path:
    return file.with_name(f"{file.name}.running")


def is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    if sys.platform == "win32":
        return _is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_running(pid: int) -> bool:
    """Whether Windows has a process of this id that has not ended.

    Asked of Windows itself, because `os.kill(pid, 0)` there does not ask: it sends the process a Ctrl+C.
    """
    import ctypes
    from ctypes import wintypes

    synchronize, access_denied, still_running = 0x00100000, 5, 0x102
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    process = kernel.OpenProcess(synchronize, False, pid)
    if not process:
        # A process that belongs to someone else cannot be opened, and is there all the same.
        return ctypes.get_last_error() == access_denied
    try:
        # A process that has ended keeps its id for as long as anyone holds it open, so it is asked whether it has.
        return kernel.WaitForSingleObject(process, 0) == still_running
    finally:
        kernel.CloseHandle(process)
