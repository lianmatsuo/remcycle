"""One memory folder: the files Claude Code reads, plus what remcycle knows about each entry.

The memory files and `MEMORY.md` stay in Claude Code's own format, so a session works
with or without remcycle. Provenance, evidence and history live beside them in
`.remcycle/`, which Claude Code never loads.
"""

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dream.claims import Claim, ClaimType, Entry, Evidence, Provenance, Status, claim_from_json, claim_to_json
from dream.extract import Thread
from dream.reconcile import Add, Alias, Confirm, Contest, Demote, Op, Question, Review, Supersede, Withhold

INDEX = "MEMORY.md"
TOPIC_PAGE = "_topic-"
# Claude Code loads this much of MEMORY.md at session start and silently drops the rest.
INDEX_LINE_LIMIT = 200
INDEX_BYTE_LIMIT = 25_000
# Past this many entries, a session should pick a topic before it picks an entry.
TOPICS_AFTER = 60
SIDE = ".remcycle"
# Claude Code's own memory types, which decide how a session treats the file.
_BUILT_IN_TYPE = {
    ClaimType.PREFERENCE: "feedback",
    ClaimType.DECISION: "project",
    ClaimType.FACT: "reference",
    ClaimType.LESSON: "feedback",
}
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)
_LINK = re.compile(r"\]\(([^)]+)\.md\)")
_TITLE = re.compile(r"\[([^\]]+)\]")
_HOOK_LIMIT = 150
# How much of a topic's titles its index line may list: all of them, or less when the index has no room.
_TOPIC_HOOK_LIMITS = (None, 600, 300, _HOOK_LIMIT)
_THREAD_LIFE = timedelta(days=14)


@dataclass(frozen=True)
class Past:
    """A statement a slot used to hold."""

    statement: str
    provenance: Provenance | None
    said_at: str | None
    replaced_at: str


@dataclass(frozen=True)
class Left:
    """Something a session left unfinished."""

    slot: str
    statement: str
    seen_at: str
    """When a session last reported it open."""
    checked_to: str | None = None
    """Up to when the repository's own changes have been looked at for it."""


@dataclass(frozen=True)
class Closed:
    """A thread that was closed, kept for a while so it can be seen and reopened."""

    slot: str
    statement: str
    at: str
    by: str
    """`you`, a `session` that said it had finished the work, or the `dream`."""
    session_id: str | None = None
    why: str = ""


@dataclass(frozen=True)
class Open:
    """Something only the person can settle.

    With a claim, the question is whether the claim should replace the entry.
    Without one, it is whether the entry should be retired, for `reason`.
    """

    slot: str
    claim: Claim | None
    withheld: bool
    """Whether the entry is kept out of sessions until it is settled."""
    reason: str = ""


@dataclass(frozen=True)
class Found:
    slot: str
    statement: str
    matched: str
    """How the query matched: `name`, `alias` or `words`."""


class MemoryStore:
    def __init__(self, folder: Path, topics_after: int = TOPICS_AFTER) -> None:
        self._folder = folder
        self._topics_after = topics_after
        self._records_file = folder / SIDE / "entries.json"
        self._queue_file = folder / SIDE / "queue.json"
        self._withheld = folder / SIDE / "withheld"
        self._threads_file = folder / SIDE / "threads.json"
        self._closed_file = folder / SIDE / "closed.json"
        self._flat_file = folder / SIDE / "flat"
        self._usage_file = folder / SIDE / "usage.json"
        self._lines_file = folder / SIDE / "index.md"

    @property
    def folder(self) -> Path:
        return self._folder

    def entries(self) -> dict[str, Entry]:
        """Every entry by slot. A slot is the memory file's name without `.md`."""
        records = self._records()
        files = [
            p for p in self._folder.glob("*.md") if p.name != INDEX and not p.name.startswith(TOPIC_PAGE)
        ] + list(self._withheld.glob("*.md"))
        return {
            path.stem: _entry(path.stem, path.read_text(), records.get(path.stem, {}))
            for path in sorted(files, key=lambda p: p.stem)
        }

    def apply(self, ops: Sequence[Op]) -> None:
        records = self._records()
        for op in ops:
            match op:
                case Add(claim):
                    self._write(claim)
                    self._index(claim)
                    records[claim.slot] = _record(claim)
                case Confirm(slot, evidence, at):
                    record = records.setdefault(slot, {})
                    record.setdefault("evidence", []).append(_pointer(evidence))
                    record["confirmed_at"] = at
                case Supersede(slot, claim):
                    replaced = _past(self.entries()[slot], claim.said_at)
                    self._write(claim)
                    self._index(claim)
                    kept = records.get(slot, {})
                    records[slot] = {
                        **_record(claim),
                        "aliases": kept.get("aliases", []),
                        "history": [*kept.get("history", []), replaced],
                    }
                case Contest(slot, claim):
                    self._hold(slot, records, Status.CONTESTED)
                    self._ask(Open(slot, claim, withheld=True))
                case Question(slot, claim):
                    self._ask(Open(slot, claim, withheld=False))
                case Alias(slot, alias):
                    records.setdefault(slot, {}).setdefault("aliases", []).append(alias)
                case Withhold(slot, reason):
                    self._hold(slot, records, Status.STALE)
                    self._ask(Open(slot, None, withheld=True, reason=reason))
                case Review(slot, reason):
                    self._ask(Open(slot, None, withheld=False, reason=reason))
                case Demote(slot, reason):
                    self._index_drop(slot)
                    records.setdefault(slot, {})["demoted"] = True
                    self._ask(Open(slot, None, withheld=False, reason=reason))
        self._save(records)
        self._render()  # again, now that what the operations recorded is saved

    def _hold(self, slot: str, records: dict[str, dict], status: Status) -> None:
        """Move the entry's file out of the folder sessions read, and drop its index line."""
        self._withheld.mkdir(parents=True, exist_ok=True)
        file = self._folder / f"{slot}.md"
        if file.exists():
            file.rename(self._withheld / f"{slot}.md")
        self._index_drop(slot)
        records.setdefault(slot, {})["status"] = status

    def _save(self, records: dict[str, dict]) -> None:
        self._records_file.parent.mkdir(exist_ok=True)
        _put(self._records_file, json.dumps(records, indent=2, sort_keys=True) + "\n")

    def forget(self, slot: str) -> None:
        """Drop what remcycle recorded about a slot. The memory file, if any, is left alone."""
        records = self._records()
        if records.pop(slot, None) is not None:
            self._save(records)

    def ensure_indexed(self) -> None:
        """Give every entry remcycle wrote a line in the index, if it has lost its line."""
        lines = self._lines()
        present = {_slot_of(line) for line in lines}
        records = self._records()
        missing = [
            _index_line(slot, entry.statement)
            for slot, entry in self.entries().items()
            if slot in records
            and not records[slot].get("demoted")
            and entry.status == Status.ACTIVE
            and _always_loaded(entry.type, entry.provenance)
            and slot not in present
        ]
        if missing:
            self._keep(lines + missing)

    def set_topic(self, slot: str, topic: str) -> None:
        records = self._records()
        records.setdefault(slot, {})["topic"] = topic
        self._save(records)
        self._render()

    def topic(self, slot: str) -> str:
        return self._records().get(slot, {}).get("topic", "")

    def set_probe(self, slot: str, question: str) -> None:
        """Record the question the entry's index line should lead a session to."""
        records = self._records()
        records.setdefault(slot, {})["asks"] = question
        self._save(records)

    def topics(self) -> list[str]:
        return sorted({record["topic"] for record in self._records().values() if record.get("topic")})

    def absorb(self, before: str, after: str) -> None:
        """Take in a session's edit to the index: `before` is what was last written, `after` what is there now."""
        known = self._lines()
        edited = [line if line.endswith("\n") else line + "\n" for line in after.splitlines(keepends=True)]
        if any(self._folder.glob(f"{TOPIC_PAGE}*.md")):
            # The index lists topics. Any entry line found in it is one a session added by hand.
            for line in edited:
                if (slot := _slot_of(line)) and line not in before.splitlines(keepends=True):
                    known = [existing for existing in known if _slot_of(existing) != slot] + [line]
        else:
            shown = {_slot_of(line) for line in before.splitlines()}
            unshown = [line for line in known if _slot_of(line) and _slot_of(line) not in shown]
            known = edited + unshown
        self._keep(known)

    def resolve(self, slot: str, *, accept: bool) -> None:
        """Settle everything waiting on a slot.

        Accepting takes the newest claim as the entry or, where the question was
        whether to retire the entry, retires it. Declining keeps the entry as it was.
        """
        waiting = [item for item in self.queue() if item.slot == slot]
        if not waiting:
            raise LookupError(f"nothing is waiting on {slot!r}")
        newest = waiting[-1]
        self._queue_save([item for item in self.queue() if item.slot != slot])
        records = self._records()
        if accept and newest.claim is None:
            self._hold(slot, records, Status.RETIRED)
            self._save(records)
            return
        held = self._withheld / f"{slot}.md"
        if held.exists() or records.get(slot, {}).get("demoted"):
            if held.exists():
                held.rename(self._folder / f"{slot}.md")
            records.setdefault(slot, {})["status"] = Status.ACTIVE
            records[slot].pop("demoted", None)
            self._save(records)
            self._index_set(slot, self.entries()[slot].statement)
        if accept:
            # The person chose it, so it now stands on their authority.
            self.apply([Supersede(slot, replace(newest.claim, provenance=Provenance.HUMAN))])

    def probes(self) -> dict[str, str]:
        """For each entry in use that has one: the question its index line should lead a session to."""
        records = self._records()
        return {
            slot: records[slot]["asks"]
            for slot, entry in self.entries().items()
            if entry.status == Status.ACTIVE and records.get(slot, {}).get("asks")
        }

    def find(self, query: str, limit: int = 5) -> list[Found]:
        """Entries in use that match the query: by name first, then by alias, then by their words.

        A name or alias matches when the query is exactly its words, in any order.
        Otherwise an entry matches when it holds every word of the query.
        """
        asked = _terms(query)
        if not asked:
            return []
        ranked: list[tuple[int, Found]] = []
        for slot, entry in self.entries().items():
            if entry.status != Status.ACTIVE:
                continue
            if asked == _terms(slot):
                ranked.append((0, Found(slot, entry.statement, "name")))
            elif any(asked == _terms(alias) for alias in entry.aliases):
                ranked.append((1, Found(slot, entry.statement, "alias")))
            elif asked <= _terms(f"{slot} {entry.statement}"):
                ranked.append((2, Found(slot, entry.statement, "words")))
        return [found for _, found in sorted(ranked, key=lambda pair: pair[0])][:limit]

    def note_read(self, slot: str, repository: Path, at: str) -> str | None:
        """Count a session reading the entry. Returns a warning if what it is about has gone."""
        usage = json.loads(self._usage_file.read_text()) if self._usage_file.exists() else {}
        usage[slot] = {"reads": usage.get(slot, {}).get("reads", 0) + 1, "last": at}
        self._usage_file.parent.mkdir(exist_ok=True)
        _put(self._usage_file, json.dumps(usage, indent=2, sort_keys=True) + "\n")
        anchor = self._records().get(slot, {}).get("anchor")
        if anchor and not (repository / anchor).exists():
            return (
                f"This memory is about {anchor}, which no longer exists in the repository. "
                "Check before relying on it."
            )
        return None

    def reads(self, slot: str) -> int:
        usage = json.loads(self._usage_file.read_text()) if self._usage_file.exists() else {}
        return usage.get(slot, {}).get("reads", 0)

    def note_threads(self, threads: Sequence[Thread], session_id: str, at: str) -> list[str]:
        """Record what a session left open, and close what it finished. Returns the closed threads it reopened."""
        noted, closed = self._threads(), self._closed()
        reopened = []
        for thread in threads:
            if thread.is_open:
                if closed.pop(thread.slot, None):
                    reopened.append(thread.slot)
                noted[thread.slot] = {
                    "statement": thread.statement,
                    "session_id": session_id,
                    "turn": thread.turn,
                    "seen_at": at,
                }
            elif thread.slot in noted:
                closed[thread.slot] = {"thread": noted.pop(thread.slot), "at": at, "by": "dream", "session_id": session_id, "why": "a session finished it"}
        self._threads_save(noted, closed)
        return reopened

    def threads(self, now: str) -> dict[str, str]:
        """Open threads by slot, leaving out any not seen for two weeks."""
        return {thread.slot: thread.statement for thread in self.left_open(now)}

    def left_open(self, now: str) -> list[Left]:
        """Open threads with when each was last seen, leaving out any not seen for two weeks."""
        cutoff = datetime.fromisoformat(now) - _THREAD_LIFE
        return [
            Left(slot, thread["statement"], thread["seen_at"], thread.get("checked_to"))
            for slot, thread in self._threads().items()
            if datetime.fromisoformat(thread["seen_at"]) >= cutoff
        ]

    def note_checked(self, at: str) -> None:
        """Record that the repository's changes up to `at` have been looked at for every open thread."""
        noted = self._threads()
        for thread in noted.values():
            thread["checked_to"] = at
        if noted:
            _put(self._threads_file, json.dumps(noted, indent=2, sort_keys=True) + "\n")

    def close_thread(
        self, slot: str, *, by: str = "you", session_id: str | None = None, why: str = "", at: str | None = None
    ) -> None:
        """Close a thread, keeping who closed it and why. A later session that reports it open reopens it."""
        noted, closed = self._threads(), self._closed()
        if slot not in noted:
            raise LookupError(f"no open thread named {slot!r}")
        closed[slot] = {
            "thread": noted.pop(slot),
            "at": at or datetime.now(UTC).isoformat(),
            "by": by,
            "session_id": session_id,
            "why": why,
        }
        self._threads_save(noted, closed)

    def closed(self, now: str) -> list[Closed]:
        """Threads closed in the last two weeks, newest first."""
        cutoff = datetime.fromisoformat(now) - _THREAD_LIFE
        recent = [
            Closed(slot, record["thread"]["statement"], record["at"], record["by"], record["session_id"], record["why"])
            for slot, record in self._closed().items()
            if datetime.fromisoformat(record["at"]) >= cutoff
        ]
        return sorted(recent, key=lambda closure: datetime.fromisoformat(closure.at), reverse=True)

    def reopen(self, slot: str) -> None:
        """Put a closed thread back as it was."""
        noted, closed = self._threads(), self._closed()
        if slot not in closed:
            raise LookupError(f"no closed thread named {slot!r}")
        noted[slot] = closed.pop(slot)["thread"]
        self._threads_save(noted, closed)

    def _closed(self) -> dict[str, dict]:
        return json.loads(self._closed_file.read_text()) if self._closed_file.exists() else {}

    def _threads_save(self, noted: dict[str, dict], closed: dict[str, dict]) -> None:
        self._threads_file.parent.mkdir(exist_ok=True)
        _put(self._threads_file, json.dumps(noted, indent=2, sort_keys=True) + "\n")
        _put(self._closed_file, json.dumps(closed, indent=2, sort_keys=True) + "\n")

    def _threads(self) -> dict[str, dict]:
        return json.loads(self._threads_file.read_text()) if self._threads_file.exists() else {}

    def indexed(self) -> list[str]:
        """The entries the index holds a line for, in the index's order."""
        return [slot for line in self._lines() if (slot := _slot_of(line))]

    def by_topic(self) -> bool:
        """Whether the index lists topics rather than one line per entry."""
        return any(self._folder.glob(f"{TOPIC_PAGE}*.md"))

    def keep_flat(self) -> None:
        """Keep the index one line per entry however many entries there are, until `allow_topics`."""
        self._flat_file.parent.mkdir(exist_ok=True)
        _put(self._flat_file, "")
        self._render()

    def allow_topics(self) -> None:
        self._flat_file.unlink(missing_ok=True)

    def fit_index(self, max_lines: int, max_bytes: int) -> list[str]:
        """Write the index sessions load, within budget. Returns the entries left out of it.

        Up to the store's `topics_after` entries the index is one line per entry, and lines are left
        out, least useful first, to stay within the budget: memories from before remcycle
        that no session has read go first, oldest first, and what the person said or
        agreed to goes last. Past it, the index lists topics and each topic has a page of
        its entries' lines. A topic's line names every entry on its page while the index has
        room, and fewer when it does not. A line is moved or left out, never shortened or rewritten.
        """
        return self._render(max_lines, max_bytes)

    def _render(self, max_lines: int = INDEX_LINE_LIMIT, max_bytes: int = INDEX_BYTE_LIMIT) -> list[str]:
        lines = self._lines()
        index = self._folder / INDEX
        if lines and not self._lines_file.exists():
            # The index written below may list topics or leave lines out, and until now it was
            # the only place the lines were held.
            self._lines_file.parent.mkdir(exist_ok=True)
            _put(self._lines_file, "".join(lines))
        for page in self._folder.glob(f"{TOPIC_PAGE}*.md"):
            page.unlink()
        if not lines and not index.exists():
            return []
        records, entries = self._records(), self.entries()
        members = [line for line in lines if _slot_of(line)]
        topic_of = {_slot_of(line): records.get(_slot_of(line), {}).get("topic") or "" for line in members}

        by_topic = len(members) > self._topics_after and len({topic for topic in topic_of.values() if topic}) >= 2
        if by_topic and not self._flat_file.exists():
            pages: dict[str, list[str]] = {}
            for line in members:
                pages.setdefault(topic_of[_slot_of(line)] or "Other", []).append(line)
            named = {topic: f"{TOPIC_PAGE}{re.sub(r'[^a-z0-9]+', '-', topic.lower()).strip('-')}.md" for topic in pages}
            for topic, page in named.items():
                _put((self._folder / page), f"# {topic}\n\n" + "".join(pages[topic]))
            kept = "".join(line for line in lines if not _slot_of(line))
            for limit in _TOPIC_HOOK_LIMITS:
                listing = []
                for topic in sorted(pages):
                    titles = ", ".join(_TITLE.search(line)[1] for line in pages[topic])
                    count = f"{len(pages[topic])} {'memory' if len(pages[topic]) == 1 else 'memories'}"
                    hook = titles if limit is None or len(titles) <= limit else titles[: limit - 1] + "…"
                    listing.append(f"- [{topic}]({named[topic]}) — {count}: {hook}\n")
                text = kept + "".join(listing)
                if len(text.splitlines()) <= max_lines and len(text.encode()) <= max_bytes:
                    break
            _put(index, text)
            return []

        def keep_rank(slot: str) -> tuple:
            entry = entries.get(slot)
            endorsed = bool(entry and entry.provenance in (Provenance.HUMAN, Provenance.ACCEPTED))
            last_touched = records.get(slot, {}).get("said_at") or self._modified(slot)
            return (endorsed, self.reads(slot), last_touched)

        shed: list[str] = []
        candidates = sorted((_slot_of(line) for line in members), key=keep_rank)
        while candidates and (len(lines) > max_lines or sum(len(line.encode()) for line in lines) > max_bytes):
            slot = candidates.pop(0)
            lines = [line for line in lines if _slot_of(line) != slot]
            shed.append(slot)
        _put(index, "".join(lines))
        return shed

    def _lines(self) -> list[str]:
        """Every index line the store holds, in order. The index sessions load is drawn from these."""
        source = self._lines_file if self._lines_file.exists() else self._folder / INDEX
        if not source.exists():
            return []
        return [line if line.endswith("\n") else line + "\n" for line in source.read_text().splitlines(keepends=True)]

    def _keep(self, lines: Sequence[str]) -> None:
        self._lines_file.parent.mkdir(exist_ok=True)
        _put(self._lines_file, "".join(lines))
        self._render()

    def modified(self, slot: str) -> str:
        """When the entry was last said or written, as far as its record or its file says."""
        return self._records().get(slot, {}).get("said_at") or self._modified(slot)

    def _modified(self, slot: str) -> str:
        file = self._folder / f"{slot}.md"
        return _front(file.read_text())[0].get("modified", "") if file.exists() else ""

    def history(self, slot: str) -> list[Past]:
        """What the slot held before, oldest first."""
        return [Past(**past) for past in self._records().get(slot, {}).get("history", [])]

    def queue(self) -> list[Open]:
        """Disagreements waiting for the person, oldest first."""
        if not self._queue_file.exists():
            return []
        return [
            Open(
                item["slot"],
                claim_from_json(item["claim"]) if item["claim"] else None,
                item["withheld"],
                item.get("reason", ""),
            )
            for item in json.loads(self._queue_file.read_text())
        ]

    def _ask(self, question: Open) -> None:
        waiting = self.queue()
        if question not in waiting:
            self._queue_save([*waiting, question])

    def _queue_save(self, waiting: Sequence[Open]) -> None:
        self._queue_file.parent.mkdir(exist_ok=True)
        items = [
            {
                "slot": q.slot,
                "claim": claim_to_json(q.claim) if q.claim else None,
                "withheld": q.withheld,
                "reason": q.reason,
            }
            for q in waiting
        ]
        _put(self._queue_file, json.dumps(items, indent=2, sort_keys=True) + "\n")

    def _index_drop(self, slot: str) -> None:
        lines = self._lines()
        if lines or (self._folder / INDEX).exists():
            self._keep([line for line in lines if _slot_of(line) != slot])

    def _records(self) -> dict[str, dict]:
        return json.loads(self._records_file.read_text()) if self._records_file.exists() else {}

    def _write(self, claim: Claim) -> None:
        _put((self._folder / f"{claim.slot}.md"), _memory_file(claim))

    def _index(self, claim: Claim) -> None:
        if _always_loaded(claim.type, claim.provenance):
            self._index_set(claim.slot, claim.statement)
        else:
            self._index_drop(claim.slot)

    def _index_set(self, slot: str, statement: str) -> None:
        """Point the index at the entry: in place if the slot already has a line, else at the end."""
        lines = self._lines()
        line = _index_line(slot, statement)
        if any(_slot_of(existing) == slot for existing in lines):
            lines = [line if _slot_of(existing) == slot else existing for existing in lines]
        else:
            lines.append(line)
        self._keep(lines)


def _put(file: Path, text: str) -> None:
    """Replace the file whole, so a reader never finds it half written."""
    fresh = file.with_name(file.name + ".new")
    fresh.write_text(text)
    os.replace(fresh, file)


def _entry(slot: str, text: str, record: dict) -> Entry:
    fields, body = _front(text)
    return Entry(
        slot=slot,
        statement=fields.get("description") or next((line for line in body.splitlines() if line.strip()), ""),
        status=Status(record.get("status", Status.ACTIVE)),
        type=ClaimType(record["type"]) if record.get("type") else None,
        provenance=Provenance(record["provenance"]) if record.get("provenance") else None,
        evidence=tuple(Evidence(*item) for item in record.get("evidence", ())),
        said_at=record.get("said_at"),
        why=record.get("why", ""),
        anchor=record.get("anchor"),
        aliases=tuple(record.get("aliases", ())),
    )


def _front(text: str) -> tuple[dict[str, str], str]:
    """The frontmatter's fields, flattened, and the body. Tolerates a file with none."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    fields: dict[str, str] = {}
    for line in match[1].splitlines():
        key, colon, value = line.strip().partition(":")
        if colon and value.strip():
            fields.setdefault(key.strip(), _unquoted(value.strip()))
    return fields, match[2]


def _unquoted(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _record(claim: Claim) -> dict:
    return {
        "type": claim.type,
        "provenance": claim.provenance,
        "status": Status.ACTIVE,
        "said_at": claim.said_at,
        "why": claim.why,
        "anchor": claim.anchor,
        "evidence": [_pointer(claim.evidence)],
        "history": [],
        "asks": claim.asks,
        "topic": claim.topic,
    }


def _past(entry: Entry, replaced_at: str) -> dict:
    return {
        "statement": entry.statement,
        "provenance": entry.provenance,
        "said_at": entry.said_at,
        "replaced_at": replaced_at,
    }


def _pointer(evidence: Evidence) -> list:
    return [evidence.session_id, evidence.first_turn, evidence.last_turn]


def _memory_file(claim: Claim) -> str:
    parts = [
        (
            "---\n"
            f"name: {claim.slot}\n"
            f"description: {json.dumps(claim.statement, ensure_ascii=False)}\n"
            "metadata:\n"
            f"  type: {_BUILT_IN_TYPE[claim.type]}\n"
            "---\n"
        ),
        claim.statement + "\n",
    ]
    if claim.why:
        parts.append(f"**Why:** {claim.why}\n")
    parts.append(f"Evidence: `{claim.evidence.command}`\n")
    return "\n".join(parts)


def _slot_of(line: str) -> str | None:
    """The entry an index line points at, or None for a heading, a topic line or anything else."""
    link = _LINK.search(line)
    return link[1] if link and not link[1].startswith(TOPIC_PAGE) else None


def _terms(text: str) -> frozenset[str]:
    """The words of a query or an entry, lowercased, with plural endings dropped."""
    return frozenset(word.removesuffix("s") for word in re.findall(r"[a-z0-9]+", text.lower()))


def _always_loaded(kind: ClaimType | None, provenance: Provenance | None) -> bool:
    """Whether an entry earns a line in the index every session loads.

    A lesson the person neither stated nor agreed to is the model's own conclusion.
    It stays on file, where recall and search can find it, but is not put in front
    of every session.
    """
    return not (kind == ClaimType.LESSON and provenance in (Provenance.INFERRED, Provenance.OBSERVED))


def _index_line(slot: str, statement: str) -> str:
    title = slot.replace("-", " ").replace("_", " ").capitalize()
    hook = statement if len(statement) <= _HOOK_LIMIT else statement[: _HOOK_LIMIT - 1] + "…"
    return f"- [{title}]({slot}.md) — {hook}\n"
