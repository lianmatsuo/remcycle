"""The daily dream: started by a session when one is due, so the dream runs with no scheduler to set up."""

import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

DUE_AFTER = timedelta(hours=20)
"""How long after one daily dream started the next may start."""
WEEK = timedelta(days=7)


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
    return State(**json.loads(file.read_text()))


def save(file: Path, state: State) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    written = file.with_name(f"{file.name}.{os.getpid()}.new")
    written.write_text(json.dumps(asdict(state)))
    written.replace(file)


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


def start(argv: list[str], log: Path, env: Mapping[str, str] | None = None) -> int:
    """Run argv in a session of its own, so it carries on after the Claude Code session that started it ends.

    Returns the new process's id.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    loose = {name: value for name, value in (os.environ if env is None else env).items() if name not in _SESSION_TIES}
    with log.open("a") as out:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True, cwd=log.parent, env=loose
        )
    return process.pid


def is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
