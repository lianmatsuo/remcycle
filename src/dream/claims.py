"""What the dream believes: claims heard in sessions, and the entries memory holds."""

from dataclasses import asdict, dataclass
from enum import StrEnum


class ClaimType(StrEnum):
    DECISION = "decision"
    PREFERENCE = "preference"
    FACT = "fact"
    LESSON = "lesson"


class Scope(StrEnum):
    PROJECT = "project"
    GLOBAL = "global"


class Provenance(StrEnum):
    """Where a claim came from, strongest first."""

    HUMAN = "human"
    ACCEPTED = "accepted"
    INFERRED = "inferred"
    OBSERVED = "observed"


class Status(StrEnum):
    ACTIVE = "active"
    CONTESTED = "contested"
    """Withheld from sessions until a person rules on a disagreement."""


@dataclass(frozen=True)
class Evidence:
    """The turns of one session that a claim rests on."""

    session_id: str
    first_turn: int
    last_turn: int


@dataclass(frozen=True)
class Claim:
    slot: str
    """What the claim is about. Two claims with one slot are about the same thing."""
    type: ClaimType
    scope: Scope
    statement: str
    why: str
    provenance: Provenance
    evidence: Evidence
    said_at: str
    anchor: str | None = None
    """For a fact about the repository: the path it can be checked against."""


@dataclass(frozen=True)
class Entry:
    """What memory currently holds for one slot."""

    slot: str
    statement: str
    status: Status = Status.ACTIVE
    type: ClaimType | None = None
    provenance: Provenance | None = None
    """None for a memory written before remcycle kept provenance."""
    evidence: tuple[Evidence, ...] = ()
    said_at: str | None = None
    why: str = ""
    anchor: str | None = None


def claim_to_json(claim: Claim) -> dict:
    return asdict(claim)


def claim_from_json(data: dict) -> Claim:
    return Claim(
        **{
            **data,
            "type": ClaimType(data["type"]),
            "scope": Scope(data["scope"]),
            "provenance": Provenance(data["provenance"]),
            "evidence": Evidence(**data["evidence"]),
        }
    )
