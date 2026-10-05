"""What changed in a project's repository outside any session: commits and merged pull requests.

The dream reads only sessions, so work finished by hand or in another tool would leave
its thread open until it aged out. This is the evidence that such work was done. A change
closes a thread only when the model says so and backs it with words quoted from the change.
"""

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dream.extract import Runner
from dream.memory import Left
from dream.redact import redact

_NEWEST = 60
_TEXT_LIMIT = 800
_TIMEOUT = 30


@dataclass(frozen=True)
class Change:
    ref: str
    """How a person would name it: `commit 3f2a9c1`, `pull request #12`."""
    when: str
    text: str


@dataclass(frozen=True)
class Finished:
    slot: str
    ref: str
    quote: str
    """Words from the change that show it finished the thread."""


@dataclass
class Witnessed:
    finished: list[Finished] = field(default_factory=list)
    asked: bool = False
    """Whether there were new changes to put to the model."""
    cost_usd: float = 0.0


Run = Callable[[Sequence[str], Path], str | None]
Read = Callable[[Path, str], list[Change]]
Witness = Callable[[Path, Sequence[Left]], Witnessed]


def run(argv: Sequence[str], cwd: Path) -> str | None:
    """What the command printed, or None if it could not be run or failed."""
    try:
        done = subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def changes(repository: Path, since: str, *, run: Run = run) -> list[Change]:
    """Commits and merged pull requests in the repository after `since`, oldest first.

    Commits come from git. Pull requests come from the `gh` command where it is installed,
    signed in and the repository is on GitHub; anywhere else there are simply none.
    """
    after = datetime.fromisoformat(since)
    found: list[Change] = []
    log = run(["git", "log", f"--since={since}", "--no-merges", "-n", str(_NEWEST), "--format=%h%x1f%cI%x1f%B%x1e"], repository)
    for record in (log or "").split("\x1e"):
        if record.count("\x1f") == 2:
            short, when, message = record.strip().split("\x1f")
            found.append(Change(f"commit {short}", when, message.strip()))
    merged = run(["gh", "pr", "list", "--state", "merged", "--limit", str(_NEWEST), "--json", "number,title,body,mergedAt"], repository)
    try:
        pull_requests = json.loads(merged) if merged else []
    except json.JSONDecodeError:
        pull_requests = []
    for pr in pull_requests:
        text = "\n\n".join(part for part in (pr.get("title", ""), pr.get("body", "")) if part)
        found.append(Change(f"pull request #{pr['number']}", pr["mergedAt"], text))
    recent = sorted((c for c in found if datetime.fromisoformat(c.when) > after), key=lambda c: datetime.fromisoformat(c.when))
    return [Change(c.ref, c.when, redact(c.text)[0][:_TEXT_LIMIT]) for c in recent[-_NEWEST:]]


def witness_with(runner: Runner, *, read: Read = changes) -> Witness:
    """A witness that reads the repository's changes and asks the model which threads they finished."""

    def witness(repository: Path, threads: Sequence[Left]) -> Witnessed:
        # A change can only finish a thread a session reported open before it, and is looked at once.
        since = {thread.slot: max(_time(thread.seen_at), _time(thread.checked_to or thread.seen_at)) for thread in threads}
        if not since:
            return Witnessed()
        earliest = min(threads, key=lambda thread: since[thread.slot])
        found = read(repository, max(earliest.seen_at, earliest.checked_to or earliest.seen_at, key=_time))
        fresh = [change for change in found if any(_time(change.when) > start for start in since.values())]
        if not fresh:
            return Witnessed()
        reply = runner(_prompt(threads, fresh))
        by_ref = {change.ref: change for change in fresh}
        finished = [
            Finished(claim["slot"], claim["ref"], claim["quote"])
            for claim in reply.data.get("finished", ())
            if isinstance(claim, dict)
            and claim.get("slot") in since
            and (change := by_ref.get(claim.get("ref")))
            and _time(change.when) > since[claim["slot"]]
            and _quoted(claim.get("quote"), change.text)
        ]
        return Witnessed(finished, True, reply.cost_usd)

    return witness


def _time(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _quoted(quote: object, text: str) -> bool:
    plain = " ".join(str(quote or "").lower().split())
    return len(plain) >= 10 and plain in " ".join(text.lower().split())


def _prompt(threads: Sequence[Left], found: Sequence[Change]) -> str:
    return _INSTRUCTIONS.format(
        threads="\n".join(f"- {thread.slot} (open as of {thread.seen_at[:10]}): {thread.statement}" for thread in threads),
        changes="\n\n".join(f"### {change.ref} ({change.when[:10]})\n\n{change.text}" for change in found),
    )


SYSTEM = "You match a software project's unfinished work to the changes that finished it. You answer only through the structured output."

_INSTRUCTIONS = """\
# Task

Below are threads of unfinished work in one software project, and the changes made to its repository \
since: commits and merged pull requests.

List each thread that a change finished. A thread is finished only when a change clearly did what the \
thread says was left to do. Work on the same subject is not enough, and neither is a change made before \
the thread was open. For each, give:

- slot: the thread's name, exactly as given.
- ref: the change that finished it, exactly as given, such as "commit 3f2a9c1".
- quote: a short passage copied exactly from that change's text that shows it.

Most changes finish no thread. Return an empty list when none did.

## Open threads

{threads}

## Changes

{changes}
"""

_TEXT = {"type": "string"}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["finished"],
    "properties": {
        "finished": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["slot", "ref", "quote"],
                "properties": {"slot": _TEXT, "ref": _TEXT, "quote": _TEXT},
            },
        }
    },
}
