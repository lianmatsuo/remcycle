"""One memory folder: the files Claude Code reads, plus what remcycle knows about each entry.

The memory files and `MEMORY.md` stay in Claude Code's own format, so a session works
with or without remcycle. Provenance, evidence and history live beside them in
`.remcycle/`, which Claude Code never loads.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from dream.claims import Claim, ClaimType, Entry, Evidence, Provenance, Status, claim_from_json, claim_to_json
from dream.extract import Thread
from dream.reconcile import Add, Confirm, Contest, Op, Question, Supersede

INDEX = "MEMORY.md"
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
_HOOK_LIMIT = 150
_THREAD_LIFE = timedelta(days=14)


@dataclass(frozen=True)
class Past:
    """A statement a slot used to hold."""

    statement: str
    provenance: Provenance | None
    said_at: str | None
    replaced_at: str


@dataclass(frozen=True)
class Open:
    """A disagreement only the person can settle."""

    slot: str
    claim: Claim
    withheld: bool
    """Whether the entry is kept out of sessions until it is settled."""


class MemoryStore:
    def __init__(self, folder: Path) -> None:
        self._folder = folder
        self._records_file = folder / SIDE / "entries.json"
        self._queue_file = folder / SIDE / "queue.json"
        self._withheld = folder / SIDE / "withheld"
        self._threads_file = folder / SIDE / "threads.json"
        self._usage_file = folder / SIDE / "usage.json"

    def entries(self) -> dict[str, Entry]:
        """Every entry by slot. A slot is the memory file's name without `.md`."""
        records = self._records()
        files = [p for p in self._folder.glob("*.md") if p.name != INDEX] + list(self._withheld.glob("*.md"))
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
                    records[slot] = {**_record(claim), "history": [*records.get(slot, {}).get("history", []), replaced]}
                case Contest(slot, claim):
                    self._withheld.mkdir(parents=True, exist_ok=True)
                    (self._folder / f"{slot}.md").rename(self._withheld / f"{slot}.md")
                    self._index_drop(slot)
                    records.setdefault(slot, {})["status"] = Status.CONTESTED
                    self._ask(Open(slot, claim, withheld=True))
                case Question(slot, claim):
                    self._ask(Open(slot, claim, withheld=False))
        self._save(records)

    def _save(self, records: dict[str, dict]) -> None:
        self._records_file.parent.mkdir(exist_ok=True)
        self._records_file.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")

    def forget(self, slot: str) -> None:
        """Drop what remcycle recorded about a slot. The memory file, if any, is left alone."""
        records = self._records()
        if records.pop(slot, None) is not None:
            self._save(records)

    def ensure_indexed(self) -> None:
        """Give every entry remcycle wrote a line in the index, if it has lost its line."""
        index = self._folder / INDEX
        text = index.read_text() if index.exists() else ""
        missing = [
            _index_line(slot, entry.statement)
            for slot, entry in self.entries().items()
            if slot in self._records()
            and entry.status == Status.ACTIVE
            and _always_loaded(entry.type, entry.provenance)
            and f"]({slot}.md)" not in text
        ]
        if missing:
            index.write_text(text + "".join(missing))

    def resolve(self, slot: str, *, accept: bool) -> None:
        """Settle every disagreement waiting on a slot: take the newest claim, or keep the entry."""
        waiting = [item for item in self.queue() if item.slot == slot]
        if not waiting:
            raise LookupError(f"nothing is waiting on {slot!r}")
        held = self._withheld / f"{slot}.md"
        if held.exists():
            held.rename(self._folder / f"{slot}.md")
            records = self._records()
            records.setdefault(slot, {})["status"] = Status.ACTIVE
            self._save(records)
            self._index_set(slot, self.entries()[slot].statement)
        self._queue_save([item for item in self.queue() if item.slot != slot])
        if accept:
            # The person chose it, so it now stands on their authority.
            self.apply([Supersede(slot, replace(waiting[-1].claim, provenance=Provenance.HUMAN))])

    def note_read(self, slot: str, repository: Path, at: str) -> str | None:
        """Count a session reading the entry. Returns a warning if what it is about has gone."""
        usage = json.loads(self._usage_file.read_text()) if self._usage_file.exists() else {}
        usage[slot] = {"reads": usage.get(slot, {}).get("reads", 0) + 1, "last": at}
        self._usage_file.parent.mkdir(exist_ok=True)
        self._usage_file.write_text(json.dumps(usage, indent=2, sort_keys=True) + "\n")
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

    def note_threads(self, threads: Sequence[Thread], session_id: str, at: str) -> None:
        """Record what a session left open, and close what it finished."""
        noted = self._threads()
        for thread in threads:
            if thread.is_open:
                noted[thread.slot] = {
                    "statement": thread.statement,
                    "session_id": session_id,
                    "turn": thread.turn,
                    "seen_at": at,
                }
            else:
                noted.pop(thread.slot, None)
        self._threads_file.parent.mkdir(exist_ok=True)
        self._threads_file.write_text(json.dumps(noted, indent=2, sort_keys=True) + "\n")

    def threads(self, now: str) -> dict[str, str]:
        """Open threads by slot, leaving out any not seen for two weeks."""
        cutoff = datetime.fromisoformat(now) - _THREAD_LIFE
        return {
            slot: thread["statement"]
            for slot, thread in self._threads().items()
            if datetime.fromisoformat(thread["seen_at"]) >= cutoff
        }

    def _threads(self) -> dict[str, dict]:
        return json.loads(self._threads_file.read_text()) if self._threads_file.exists() else {}

    def fit_index(self, max_lines: int, max_bytes: int) -> list[str]:
        """Bring the index within budget by dropping lines, least useful first. Files stay.

        The first to go are memories from before remcycle that no session has read,
        oldest first; what the person said or agreed to goes last. A line is only
        ever dropped, never shortened or rewritten.
        """
        index = self._folder / INDEX
        if not index.exists():
            return []
        records, entries = self._records(), self.entries()
        lines = index.read_text().splitlines(keepends=True)

        def slot_of(line: str) -> str | None:
            link = _LINK.search(line)
            return link[1] if link else None

        def keep_rank(slot: str) -> tuple:
            entry = entries.get(slot)
            endorsed = bool(entry and entry.provenance in (Provenance.HUMAN, Provenance.ACCEPTED))
            last_touched = records.get(slot, {}).get("said_at") or self._modified(slot)
            return (endorsed, self.reads(slot), last_touched)

        shed: list[str] = []
        candidates = sorted((slot for line in lines if (slot := slot_of(line))), key=keep_rank)
        while candidates and (len(lines) > max_lines or sum(len(line.encode()) for line in lines) > max_bytes):
            slot = candidates.pop(0)
            lines = [line for line in lines if slot_of(line) != slot]
            shed.append(slot)
        if shed:
            index.write_text("".join(lines))
        return shed

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
            Open(item["slot"], claim_from_json(item["claim"]), item["withheld"])
            for item in json.loads(self._queue_file.read_text())
        ]

    def _ask(self, question: Open) -> None:
        waiting = self.queue()
        if question not in waiting:
            self._queue_save([*waiting, question])

    def _queue_save(self, waiting: Sequence[Open]) -> None:
        self._queue_file.parent.mkdir(exist_ok=True)
        items = [{"slot": q.slot, "claim": claim_to_json(q.claim), "withheld": q.withheld} for q in waiting]
        self._queue_file.write_text(json.dumps(items, indent=2, sort_keys=True) + "\n")

    def _index_drop(self, slot: str) -> None:
        index = self._folder / INDEX
        if index.exists():
            index.write_text("".join(line for line in index.read_text().splitlines(keepends=True) if f"]({slot}.md)" not in line))

    def _records(self) -> dict[str, dict]:
        return json.loads(self._records_file.read_text()) if self._records_file.exists() else {}

    def _write(self, claim: Claim) -> None:
        (self._folder / f"{claim.slot}.md").write_text(_memory_file(claim))

    def _index(self, claim: Claim) -> None:
        if _always_loaded(claim.type, claim.provenance):
            self._index_set(claim.slot, claim.statement)
        else:
            self._index_drop(claim.slot)

    def _index_set(self, slot: str, statement: str) -> None:
        """Point the index at the entry: in place if the slot already has a line, else at the end."""
        index = self._folder / INDEX
        lines = index.read_text().splitlines(keepends=True) if index.exists() else []
        link = f"]({slot}.md)"
        line = _index_line(slot, statement)
        if any(link in existing for existing in lines):
            lines = [line if link in existing else existing for existing in lines]
        else:
            lines.append(line)
        index.write_text("".join(lines))


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
    e = claim.evidence
    parts = [
        "---\n"
        f"name: {claim.slot}\n"
        f"description: {json.dumps(claim.statement, ensure_ascii=False)}\n"
        "metadata:\n"
        f"  type: {_BUILT_IN_TYPE[claim.type]}\n"
        "---\n",
        claim.statement + "\n",
    ]
    if claim.why:
        parts.append(f"**Why:** {claim.why}\n")
    parts.append(f"Evidence: `dream show {e.session_id[:8]} --first {e.first_turn} --last {e.last_turn}`\n")
    return "\n".join(parts)


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
