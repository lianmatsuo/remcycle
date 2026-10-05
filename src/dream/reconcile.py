"""Decide what each claim does to memory. No model is involved: the rules are all here."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from dream.claims import Claim, ClaimType, Entry, Evidence, Provenance, Status


@dataclass(frozen=True)
class Add:
    claim: Claim


@dataclass(frozen=True)
class Confirm:
    """The same statement was heard again."""

    slot: str
    evidence: Evidence
    at: str


@dataclass(frozen=True)
class Supersede:
    """The claim replaces what memory held for its slot."""

    slot: str
    claim: Claim


@dataclass(frozen=True)
class Contest:
    """The claim cannot replace the entry, but the entry may no longer be true: withhold it."""

    slot: str
    claim: Claim


@dataclass(frozen=True)
class Question:
    """The claim disagrees with something only the person can change: keep the entry and ask."""

    slot: str
    claim: Claim


@dataclass(frozen=True)
class Reject:
    """The claim may not enter memory. Nothing changes; the reason goes in the report."""

    claim: Claim
    reason: str


Op = Add | Confirm | Supersede | Contest | Question | Reject

_AUTHORITY = {Provenance.HUMAN: 3, Provenance.ACCEPTED: 2, Provenance.INFERRED: 1, Provenance.OBSERVED: 0}
# A new entry has to rest on what the person said or agreed to, unless it is a lesson.
_THE_PERSONS_WORD = {Provenance.HUMAN, Provenance.ACCEPTED}
# Only the person changes these, so a weaker claim cannot unseat one.
_THE_PERSONS_CALL = {ClaimType.PREFERENCE, ClaimType.DECISION}


def reconcile(entries: Mapping[str, Entry], claims: Iterable[Claim]) -> list[Op]:
    """The operations that bring `entries` up to date with `claims`, oldest claim first."""
    state = dict(entries)
    ops: list[Op] = []
    for claim in sorted(claims, key=lambda c: c.said_at):
        op = _decide(state.get(claim.slot), claim)
        if op:
            ops.append(op)
            if not isinstance(op, Reject):
                state[claim.slot] = after(state.get(claim.slot), op)
    return ops


def _decide(entry: Entry | None, claim: Claim) -> Op | None:
    if entry is None:
        if claim.type != ClaimType.LESSON and claim.provenance not in _THE_PERSONS_WORD:
            return Reject(claim, "nothing the person said supports it")
        return Add(claim)
    if _same_statement(entry.statement, claim.statement):
        return None if claim.evidence in entry.evidence else Confirm(claim.slot, claim.evidence, claim.said_at)
    if entry.said_at and claim.said_at < entry.said_at:
        return None
    if entry.provenance is None:
        # A memory from before remcycle can hold more than the one statement a claim would
        # replace it with, so only the person decides whether it goes.
        return Question(claim.slot, claim)
    if _AUTHORITY[claim.provenance] >= _AUTHORITY[entry.provenance]:
        return Supersede(claim.slot, claim)
    if (entry.type or claim.type) in _THE_PERSONS_CALL:
        return Question(claim.slot, claim)
    return Contest(claim.slot, claim)


def after(entry: Entry | None, op: Op) -> Entry:
    """The entry as it stands once `op` has been applied."""
    match op:
        case Add(claim) | Supersede(_, claim):
            return Entry(
                slot=claim.slot,
                statement=claim.statement,
                type=claim.type,
                provenance=claim.provenance,
                evidence=(claim.evidence,),
                said_at=claim.said_at,
                why=claim.why,
                anchor=claim.anchor,
            )
        case Confirm(_, evidence, _):
            return replace(entry, evidence=(*entry.evidence, evidence))
        case Contest():
            return replace(entry, status=Status.CONTESTED)
        case Question():
            return entry


_NEGATIONS = {"not", "no", "never", "dont", "without", "stop", "avoid", "instead"}


def _same_statement(one: str, other: str) -> bool:
    """Whether two statements say the same thing, allowing for small differences in wording.

    A difference that is a negation or a number is never small.
    """
    a, b = _words(one), _words(other)
    if a == b:
        return True
    differing = a ^ b
    if differing & _NEGATIONS or any(word.isdigit() for word in differing):
        return False
    return len(a & b) / len(a | b) >= 0.8


def _words(statement: str) -> set[str]:
    return set(re.findall(r"\w+", statement.lower().replace("'", "")))
