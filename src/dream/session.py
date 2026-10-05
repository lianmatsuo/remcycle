"""What a running session is told about memory, and what the person is shown.

These answers are read from remcycle's own copies of memory, so they describe
what the dream has accepted whether or not it has been published.
"""

from pathlib import Path

from dream.claims import Provenance, Status
from dream.dreaming import GLOBAL, key, known
from dream.memory import INDEX, INDEX_BYTE_LIMIT, INDEX_LINE_LIMIT, MemoryStore
from dream.mirror import Mirror


_PACK_ROOM = 4_000
"""Characters of learned statements a new session is handed. What does not fit is a search away."""


def context(memory_root: Path, project: str, now: str, live_root: Path, room: int = _PACK_ROOM) -> dict:
    """What a session starting in `project` should be given beyond Claude Code's own memory."""
    everywhere = MemoryStore(memory_root / key(GLOBAL)).entries().values()
    store = MemoryStore(memory_root / key(project))
    # Sessions started without a folder share one group and little else, so they are handed none of it.
    learned, more = _learned(store, live_root / key(project) / "memory", room) if project.startswith("/") else ([], 0)
    return {
        "everywhere": [entry.statement for entry in everywhere if entry.status == Status.ACTIVE],
        "learned": learned,
        "learned_in": str(store.folder),
        "learned_more": more,
        "threads": [{"slot": slot, "statement": statement} for slot, statement in store.threads(now).items()],
    }


def _learned(store: MemoryStore, live: Path, room: int) -> tuple[list[dict], int]:
    """What the dream holds that Claude Code's own memory folder does not, newest first, and how many did not fit.

    Only what the person said or agreed to, and only what the index lists: what publishing
    would add to the memory a session loads, handed to the session without publishing.
    """
    entries = store.entries()
    slots = [
        slot
        for slot in store.indexed()
        if (entry := entries.get(slot))
        and entry.status == Status.ACTIVE
        and entry.provenance in (Provenance.HUMAN, Provenance.ACCEPTED)
        and not (live / f"{slot}.md").exists()
    ]
    slots.sort(key=store.modified, reverse=True)
    handed: list[dict] = []
    used = 0
    for slot in slots:
        statement = entries[slot].statement
        if handed and used + len(statement) > room:
            break
        handed.append({"slot": slot, "statement": statement})
        used += len(statement)
    return handed, len(slots) - len(handed)


def status(memory_root: Path, project: str, now: str, live_root: Path | None = None) -> dict:
    """What the dream holds for a project and what is waiting for the person's ruling.

    With `live_root`, a memory in question is pointed at in Claude Code's own folder where it
    has a file there, since that is the file a session would edit.
    """
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
        "waiting": _questions(store, live_root / key(project) / "memory" if live_root else None),
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


def _questions(store: MemoryStore, live: Path | None) -> list[dict]:
    """What waits for the person, one question per memory.

    A ruling settles everything waiting on a memory at once and acts on the newest of it,
    so the newest is what the question puts to the person.
    """
    entries = store.entries()
    by_slot: dict[str, list] = {}
    for item in store.queue():
        by_slot.setdefault(item.slot, []).append(item)
    questions = []
    for slot, items in by_slot.items():
        newest = items[-1].claim
        questions.append(
            {
                "slot": slot,
                "holds": entries[slot].statement if slot in entries else None,
                "suggests": newest.statement if newest else None,
                "reasons": [item.reason for item in items if item.reason],
                "from": newest.provenance if newest else None,
                "withheld": any(item.withheld for item in items),
                "evidence": newest.evidence.command if newest else None,
                "file": str(live / f"{slot}.md" if live and (live / f"{slot}.md").exists() else store.folder / f"{slot}.md"),
            }
        )
    return questions
