"""The daily dream: started by a session when one is due, so the dream runs with no scheduler to set up."""

import json
import os
import subprocess
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


def start(argv: list[str], log: Path) -> None:
    """Run argv in a session of its own, so it carries on after the Claude Code session that started it ends."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as out:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
