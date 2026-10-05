"""The review: a pass over the memories a project already has.

The dream only touches an entry when a session says something about it. The review
reads the entries themselves. It gives each a topic and a probe question, which the
index and the gate need, and it looks for entries that describe a state that has
passed, repeat each other or disagree. It never changes or removes an entry: what
it finds is put to the person, and only if the words it quotes are really there.
"""

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dream.claims import Status
from dream.extract import ExtractionError, Runner
from dream.memory import SIDE, MemoryStore
from dream.reconcile import Review

_BODY_LIMIT = 1_500
_BUDGET = 120_000
_PER_PASS = 20
"""Entries per model call, so that one call stays inside the runner's time limit."""


@dataclass
class ReviewReport:
    reviewed: int = 0
    questions: int = 0
    unsupported: int = 0
    """Findings dropped because a passage they quoted was not in the memory."""
    cost_usd: float = 0.0
    failures: list[str] = field(default_factory=list)
    """Why each pass the model could not complete failed. Its entries are reviewed next time."""
    problems: list[str] = field(default_factory=list)
    """Why the gate refused the reviewed copy. Empty when it was accepted."""
    merged: bool = False


def review(store: MemoryStore, runner: Runner, *, budget: int = _BUDGET, per_pass: int = _PER_PASS) -> ReviewReport:
    """Review every entry in use that has not been reviewed as it now reads."""
    folder = store.folder
    seen_file = folder / SIDE / "reviewed.json"
    seen: dict[str, str] = json.loads(seen_file.read_text()) if seen_file.exists() else {}
    texts = {
        slot: (folder / f"{slot}.md").read_text()
        for slot, entry in store.entries().items()
        if entry.status == Status.ACTIVE
    }
    fresh = [slot for slot, text in texts.items() if seen.get(slot) != _digest(text)]
    report = ReviewReport()
    asked = store.probes()

    for batch in _passes(fresh, texts, budget, per_pass):
        try:
            reply = runner(_prompt(batch, texts, store.topics()))
        except ExtractionError as e:
            report.failures.append(str(e))
            continue
        report.cost_usd += reply.cost_usd
        ops: list[Review] = []
        for note in reply.data.get("memories", ()):
            slot = note.get("slot")
            if slot not in batch:
                continue
            if note.get("topic") and not store.topic(slot):
                store.set_topic(slot, " ".join(str(note["topic"]).split())[:60])
            if note.get("asks") and slot not in asked:
                store.set_probe(slot, str(note["asks"]).strip())
            if note.get("dated"):
                if _quoted(note.get("quote"), texts[slot]):
                    ops.append(Review(slot, f"it looks dated: {note.get('reason', '').strip()}"))
                else:
                    report.unsupported += 1
        for pair in reply.data.get("pairs", ()):
            a, b = pair.get("a"), pair.get("b")
            if a not in batch or b not in batch or a == b or pair.get("kind") not in ("contradicts", "duplicates"):
                continue
            if not (_quoted(pair.get("quote_a"), texts[a]) and _quoted(pair.get("quote_b"), texts[b])):
                report.unsupported += 1
                continue
            older, newer = sorted((a, b), key=store.modified)
            verb = "contradicts" if pair["kind"] == "contradicts" else "repeats"
            ops.append(Review(older, f"it {verb} `{newer}`: {pair.get('reason', '').strip()}"))
        store.apply(ops)
        report.questions += len(ops)
        report.reviewed += len(batch)
        seen.update({slot: _digest(texts[slot]) for slot in batch})
        seen_file.parent.mkdir(exist_ok=True)
        seen_file.write_text(json.dumps(seen, indent=2, sort_keys=True) + "\n")
    return report


def _quoted(quote: object, text: str) -> bool:
    plain = " ".join(str(quote or "").lower().split())
    return len(plain) >= 10 and plain in " ".join(text.lower().split())


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _passes(slots: Sequence[str], texts: dict[str, str], budget: int, per_pass: int) -> Iterator[list[str]]:
    batch: list[str] = []
    size = 0
    for slot in sorted(slots):
        length = min(len(texts[slot]), _BODY_LIMIT)
        if batch and (size + length > budget or len(batch) == per_pass):
            yield batch
            batch, size = [], 0
        batch.append(slot)
        size += length
    if batch:
        yield batch


def _prompt(slots: Sequence[str], texts: dict[str, str], topics: Sequence[str]) -> str:
    memories = "\n\n".join(f"### {slot}\n\n{texts[slot][:_BODY_LIMIT]}" for slot in slots)
    return _INSTRUCTIONS.format(topics="\n".join(f"- {topic}" for topic in topics) or "(none yet)", memories=memories)


SYSTEM = "You review a coding assistant's notes about one project. You answer only through the structured output."

_INSTRUCTIONS = """\
# Task

Below are notes a coding assistant keeps about one project. Each starts with a line `### name`.

For every note, return:

- slot: its name, exactly as given.
- topic: the area it belongs to, in two or three words, such as "Deploys and CI". Use one of the topics \
under "Topics so far" when one fits, and give notes about the same area the same topic.
- asks: one question a later session might ask that this note answers, in the words such a session would use.
- dated: true only if the note describes work in progress, a plan or a temporary state whose time has \
clearly passed, so that following it now would mislead. A standing rule or preference is never dated.
- quote: if dated, a short passage copied exactly from the note that shows it. Otherwise empty.
- reason: if dated, why, in one sentence. Otherwise empty.

Then list pairs of notes that cannot both be followed (kind: contradicts) or that say the same thing \
(kind: duplicates). For each pair give both names, a short passage copied exactly from each note, and \
the reason in one sentence. List a pair only when you are sure. Most sets of notes have few or none.

## Topics so far

{topics}

## Notes

{memories}
"""

_TEXT = {"type": "string"}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["memories", "pairs"],
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["slot", "topic", "asks", "dated", "quote", "reason"],
                "properties": {
                    "slot": _TEXT,
                    "topic": _TEXT,
                    "asks": _TEXT,
                    "dated": {"type": "boolean"},
                    "quote": _TEXT,
                    "reason": _TEXT,
                },
            },
        },
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["a", "b", "kind", "quote_a", "quote_b", "reason"],
                "properties": {
                    "a": _TEXT,
                    "b": _TEXT,
                    "kind": {"type": "string", "enum": ["contradicts", "duplicates"]},
                    "quote_a": _TEXT,
                    "quote_b": _TEXT,
                    "reason": _TEXT,
                },
            },
        },
    },
}
