"""What a running session is told about memory, and what the person is shown.

These answers are read from remcycle's own copies of memory, so they describe
what the dream has accepted whether or not it has been published.
"""

from pathlib import Path

from dream.claims import Status
from dream.dreaming import GLOBAL, key, known
from dream.memory import INDEX, INDEX_BYTE_LIMIT, INDEX_LINE_LIMIT, MemoryStore
from dream.mirror import Mirror


def context(memory_root: Path, project: str, now: str) -> dict:
    """What a session starting in `project` should be given beyond Claude Code's own memory."""
    everywhere = MemoryStore(memory_root / key(GLOBAL)).entries().values()
    return {
        "everywhere": [entry.statement for entry in everywhere if entry.status == Status.ACTIVE],
        "threads": [
            {"slot": slot, "statement": statement}
            for slot, statement in MemoryStore(memory_root / key(project)).threads(now).items()
        ],
    }


def status(memory_root: Path, project: str, now: str) -> dict:
    """What the dream holds for a project and what is waiting for the person's ruling."""
    store = MemoryStore(memory_root / key(project))
    entries = store.entries()
    index = store.folder / INDEX
    listed = index.read_text() if index.exists() else ""
    return {
        "project": project,
        "withheld": sum(entry.status != Status.ACTIVE for entry in entries.values()),
        "last_dream": Mirror(store.folder).last("dream"),
        "index": {
            "lines": len(listed.splitlines()),
            "line_limit": INDEX_LINE_LIMIT,
            "bytes": len(listed.encode()),
            "byte_limit": INDEX_BYTE_LIMIT,
        },
        "memories": [
            {
                "slot": slot,
                "statement": entry.statement,
                "from": entry.provenance,
                "said_at": store.modified(slot) or None,
            }
            for slot, entry in sorted(entries.items())
            if entry.status == Status.ACTIVE
        ],
        "waiting": [
            {
                "slot": item.slot,
                "holds": entries[item.slot].statement if item.slot in entries else None,
                "suggests": item.claim.statement if item.claim else None,
                "reason": item.reason,
                "from": item.claim.provenance if item.claim else None,
                "withheld": item.withheld,
                "evidence": item.claim.evidence.command if item.claim else None,
            }
            for item in store.queue()
        ],
        "open_threads": [
            {"slot": thread.slot, "statement": thread.statement, "seen_at": thread.seen_at}
            for thread in store.left_open(now)
        ],
        "elsewhere": _waiting_elsewhere(memory_root, project),
        "closed_lately": [
            {"slot": c.slot, "statement": c.statement, "at": c.at, "by": c.by, "why": c.why} for c in store.closed(now)
        ],
    }


def _waiting_elsewhere(memory_root: Path, project: str) -> list[dict]:
    """The other projects with questions waiting for the person, most questions first."""
    others = {other for other in known(memory_root) if other != project and other.startswith("/")}
    counted = [(len(MemoryStore(memory_root / key(other)).queue()), other) for other in sorted(others)]
    return [{"project": other, "waiting": waiting} for waiting, other in sorted(counted, key=lambda c: -c[0]) if waiting]
