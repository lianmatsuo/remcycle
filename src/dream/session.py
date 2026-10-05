"""What a running session is told about memory, and what the person is shown.

These answers are read from remcycle's own copies of memory, so they describe
what the dream has accepted whether or not it has been published.
"""

from pathlib import Path

from dream.claims import Status
from dream.dreaming import GLOBAL, key
from dream.memory import MemoryStore


def context(memory_root: Path, project: str, now: str) -> dict:
    """What a session starting in `project` should be given beyond Claude Code's own memory."""
    everywhere = MemoryStore(memory_root / key(GLOBAL)).entries().values()
    return {
        "everywhere": [entry.statement for entry in everywhere if entry.status == Status.ACTIVE],
        "open_threads": list(MemoryStore(memory_root / key(project)).threads(now).values()),
    }


def status(memory_root: Path, project: str, now: str) -> dict:
    """What the dream holds for a project and what is waiting for the person's ruling."""
    store = MemoryStore(memory_root / key(project))
    entries = store.entries()
    return {
        "project": project,
        "entries": sum(entry.status == Status.ACTIVE for entry in entries.values()),
        "withheld": sum(entry.status != Status.ACTIVE for entry in entries.values()),
        "learned": [
            {
                "slot": slot,
                "statement": entry.statement,
                "from": entry.provenance,
                "evidence": entry.evidence[0].command,
            }
            for slot, entry in entries.items()
            if entry.provenance and entry.evidence and entry.status == Status.ACTIVE
        ],
        "waiting": [
            {
                "slot": item.slot,
                "suggests": item.claim.statement if item.claim else None,
                "reason": item.reason,
                "from": item.claim.provenance if item.claim else None,
                "withheld": item.withheld,
                "evidence": item.claim.evidence.command if item.claim else None,
            }
            for item in store.queue()
        ],
        "open_threads": list(store.threads(now).values()),
    }
