"""Turn one session into claims. The model reads and quotes; code checks every quote.

This is the dream's only model step. Whatever the model returns is treated as a
suggestion: a claim survives only if the passage it quotes is really in the turns
it cites, and it carries a person's authority only if a person typed that passage.
"""

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass

from dream.claims import Claim, ClaimType, Evidence, Provenance, Scope, claim_from_json, claim_to_json
from dream.transcript import Author, Kind, Turn


class ExtractionError(Exception):
    """The model could not be asked, or did not answer in the agreed shape."""


@dataclass(frozen=True)
class Reply:
    data: dict
    cost_usd: float = 0.0


Runner = Callable[[str], Reply]


@dataclass(frozen=True)
class Thread:
    """Work a session left unfinished, or finished."""

    slot: str
    statement: str
    is_open: bool
    turn: int


@dataclass(frozen=True)
class Correction:
    """The person corrected the assistant after it followed a memory entry."""

    slot: str
    evidence: Evidence


@dataclass(frozen=True)
class Rejected:
    slot: str
    statement: str
    reason: str


@dataclass(frozen=True)
class Extraction:
    summary: str
    claims: tuple[Claim, ...]
    threads: tuple[Thread, ...]
    rejected: tuple[Rejected, ...]
    cost_usd: float
    corrections: tuple[Correction, ...] = ()


def to_json(extraction: Extraction) -> dict:
    return {
        "summary": extraction.summary,
        "claims": [claim_to_json(claim) for claim in extraction.claims],
        "threads": [asdict(thread) for thread in extraction.threads],
        "rejected": [asdict(rejected) for rejected in extraction.rejected],
        "corrections": [asdict(correction) for correction in extraction.corrections],
    }


def from_json(data: dict) -> Extraction:
    """An extraction kept earlier. It cost nothing this time."""
    return Extraction(
        summary=data["summary"],
        claims=tuple(claim_from_json(claim) for claim in data["claims"]),
        threads=tuple(Thread(**thread) for thread in data["threads"]),
        rejected=tuple(Rejected(**rejected) for rejected in data["rejected"]),
        cost_usd=0.0,
        corrections=tuple(
            Correction(c["slot"], Evidence(**c["evidence"])) for c in data.get("corrections", ())
        ),
    )


_SLOT = re.compile(r"[a-z0-9][a-z0-9_-]{1,60}")
_SHORTEST_QUOTE = 10
_TURN_LIMIT = 6_000
_BUDGET = 300_000
_SPEAKER = {
    Author.HUMAN: "person",
    Author.ASSISTANT: "assistant",
    Author.SUBAGENT: "subagent report",
    Author.AUTOMATION: "automation",
    Author.PEER: "another session",
    Author.UNKNOWN: "unknown",
}


def extract(
    session_id: str,
    turns: Sequence[Turn],
    *,
    known: Mapping[str, str],
    runner: Runner,
    open_threads: Mapping[str, str] | None = None,
    topics: Sequence[str] = (),
    budget: int = _BUDGET,
) -> Extraction:
    """What the session established, as far as the model's quotes can be checked.

    `known` maps the slots memory already holds to their statements, so the model can
    reuse a slot instead of minting a second name for the same thing. A session longer
    than `budget` characters is read in several passes.
    """
    by_seq = {turn.seq: turn for turn in turns}
    summaries: list[str] = []
    claims: list[Claim] = []
    threads: list[Thread] = []
    rejected: list[Rejected] = []
    corrections: list[Correction] = []
    cost = 0.0
    for prose in _passes([t for t in turns if t.kind != Kind.TOOL], budget):
        reply = runner(_prompt(prose, known, open_threads or {}, topics))
        cost += reply.cost_usd
        summaries.append(str(reply.data.get("summary", "")).strip())
        for raw in reply.data.get("claims", ()):
            checked = _checked(session_id, raw, by_seq)
            (claims if isinstance(checked, Claim) else rejected).append(checked)
        threads.extend(thread for raw in reply.data.get("threads", ()) if (thread := _thread(raw)))
        corrections.extend(
            found for raw in reply.data.get("corrections", ()) if (found := _correction(session_id, raw, by_seq))
        )
    return Extraction(
        " ".join(s for s in summaries if s),
        tuple(claims),
        tuple(threads),
        tuple(rejected),
        cost,
        tuple(corrections),
    )


def _checked(session_id: str, raw: dict, by_seq: Mapping[int, Turn]) -> Claim | Rejected:
    slot = str(raw.get("slot", "")).strip().lower()
    statement = str(raw.get("statement", "")).strip()

    def rejected(reason: str) -> Rejected:
        return Rejected(slot or "?", statement, reason)

    try:
        kind, scope, stated = ClaimType(raw["type"]), Scope(raw["scope"]), Provenance(raw["provenance"])
        first, last = int(raw["first_turn"]), int(raw["last_turn"])
    except (KeyError, ValueError, TypeError) as e:
        return rejected(f"it is not in the agreed shape ({e})")
    if not _SLOT.fullmatch(slot) or not statement:
        return rejected("it has no usable slot or statement")
    cited = [by_seq[seq] for seq in range(first, last + 1) if seq in by_seq]
    if not cited:
        return rejected(f"turns {first} to {last} are not in the session")
    quote = _plain(str(raw.get("quote", "")))
    if len(quote) < _SHORTEST_QUOTE:
        return rejected("its quote is too short to check")
    quoted = [turn for turn in cited if quote in _plain(turn.text)]
    if not quoted:
        return rejected(f"its quote is not in turns {first} to {last}")

    return Claim(
        slot=slot,
        type=kind,
        scope=scope,
        statement=statement,
        why=str(raw.get("why", "")).strip(),
        provenance=_supported(stated, quoted, cited),
        evidence=Evidence(session_id, first, last),
        said_at=cited[0].timestamp or "",
        anchor=str(raw.get("anchor") or "").strip() or None,
        asks=str(raw.get("asks") or "").strip(),
        topic=" ".join(str(raw.get("topic") or "").split())[:60],
    )


def _supported(stated: Provenance, quoted: Sequence[Turn], cited: Sequence[Turn]) -> Provenance:
    """The strongest provenance the cited turns actually bear out."""
    if all(turn.author == Author.SUBAGENT for turn in quoted):
        return Provenance.OBSERVED
    if stated == Provenance.HUMAN and not any(turn.author == Author.HUMAN for turn in quoted):
        return Provenance.INFERRED
    if stated == Provenance.ACCEPTED and not any(turn.author == Author.HUMAN for turn in cited):
        return Provenance.INFERRED
    return stated


def _correction(session_id: str, raw: dict, by_seq: Mapping[int, Turn]) -> Correction | None:
    """A correction the model reported, if the person really typed the words it quotes."""
    slot = str(raw.get("slot", "")).strip().lower()
    quote = _plain(str(raw.get("quote", "")))
    try:
        turn = by_seq.get(int(raw.get("turn", -1)))
    except (ValueError, TypeError):
        return None
    if not _SLOT.fullmatch(slot) or turn is None or turn.author != Author.HUMAN:
        return None
    if len(quote) < _SHORTEST_QUOTE or quote not in _plain(turn.text):
        return None
    return Correction(slot, Evidence(session_id, turn.seq, turn.seq))


def _thread(raw: dict) -> Thread | None:
    slot = str(raw.get("slot", "")).strip().lower()
    statement = str(raw.get("statement", "")).strip()
    if not _SLOT.fullmatch(slot) or not statement or raw.get("state") not in ("open", "done"):
        return None
    try:
        turn = int(raw.get("turn", 0))
    except (ValueError, TypeError):
        turn = 0
    return Thread(slot, statement, raw["state"] == "open", turn)


def _plain(text: str) -> str:
    """Text reduced to what a quote has to match: case, spacing and quote marks ignored."""
    text = text.lower().translate(str.maketrans("“”‘’", "\"\"''"))
    return " ".join(text.split())


def _passes(prose: Sequence[Turn], budget: int) -> Iterator[list[Turn]]:
    batch: list[Turn] = []
    size = 0
    for turn in prose:
        length = min(len(turn.text), _TURN_LIMIT)
        if batch and size + length > budget:
            yield batch
            batch, size = [], 0
        batch.append(turn)
        size += length
    if batch:
        yield batch


def _prompt(
    prose: Sequence[Turn], known: Mapping[str, str], open_threads: Mapping[str, str], topics: Sequence[str]
) -> str:
    def listed(items: Mapping[str, str]) -> str:
        return "\n".join(f"- {slot}: {statement}" for slot, statement in items.items()) or "(nothing yet)"

    session = "\n\n".join(
        f"[{turn.seq}] {_SPEAKER[turn.author]}: {turn.text[:_TURN_LIMIT]}{' …[cut]' if len(turn.text) > _TURN_LIMIT else ''}"
        for turn in prose
    )
    return _INSTRUCTIONS.format(
        known=listed(known),
        threads=listed(open_threads),
        topics="\n".join(f"- {topic}" for topic in topics) or "(none yet)",
        session=session,
    )


SYSTEM = (
    "You read the record of one working session between a person and a coding assistant and extract "
    "what is worth remembering. You answer only through the structured output."
)

_INSTRUCTIONS = """\
# Task

From the session below, extract the claims worth keeping in long-term memory and the threads it left open.

## What counts as a claim

A claim must be something a future session could not learn by reading the repository, and it must come \
from the person: a correction, a stated preference, or a decision together with its reason. The one \
exception is a lesson: something that was tried and how it turned out, worth knowing before trying again.

Do not extract descriptions of the codebase, file maps, summaries of what was done, restated conventions, \
or anything specific to this one task that will not matter again.

Most sessions yield between zero and five claims. Returning none is correct when nothing qualifies.

## Fields of a claim

- slot: a short kebab-case name for what the claim is about, such as package-manager or commit-message-style. \
If the claim is about the same thing as an entry under "Already known", use that entry's slot exactly. \
If it only repeats that entry, copy the entry's statement exactly too.
- type: preference (how the person wants work done), decision (a choice made for this project, with its \
reason), fact (something true about the project's world that the repository does not record), or lesson \
(something tried and its outcome).
- scope: global only if the person said it applies to all their work, otherwise project.
- statement: one self-contained sentence. Someone reading only this sentence should know what to do or what is true. \
Call the person "the user". Do not use their name, and do not use he, she or any other gendered pronoun for them.
- why: the reason given in the session, in one sentence. Empty if none was given.
- provenance: human if the person typed it; accepted if the assistant proposed it and the person agreed; \
inferred if you concluded it from what happened; observed if it comes from a subagent report.
- first_turn, last_turn: the turn numbers that support the claim.
- quote: a short passage copied exactly from one of those turns that supports the claim. For provenance \
human it must be the person's own words.
- anchor: for a fact about a file in the repository, that file's path. Otherwise empty.
- asks: one question a later session might ask that this claim answers, in the words such a session would use.
- topic: the area the claim belongs to, in two or three words, such as "Deploys and CI". Use one of the \
topics under "Topics so far" when one fits.

## Threads

A thread is work the session left unfinished or explicitly deferred. Give each a slot, a state (open, or \
done if this session finished a thread listed under "Open threads"), one sentence, and the turn where it stands.

## Corrections

If the assistant followed an entry listed under "Already known" and the person then corrected it for \
doing so, report it: the entry's slot, the turn where the person corrected it, and a short passage \
copied exactly from that turn. Report nothing here if that did not happen.

## Already known

{known}

## Topics so far

{topics}

## Open threads

{threads}

## Session

{session}
"""

_TEXT = {"type": "string"}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "claims", "threads", "corrections"],
    "properties": {
        "summary": _TEXT,
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "slot", "type", "scope", "statement", "why", "provenance",
                    "first_turn", "last_turn", "quote", "anchor", "asks", "topic",
                ],
                "properties": {
                    "slot": _TEXT,
                    "type": {"type": "string", "enum": [t.value for t in ClaimType]},
                    "scope": {"type": "string", "enum": [s.value for s in Scope]},
                    "statement": _TEXT,
                    "why": _TEXT,
                    "provenance": {"type": "string", "enum": [p.value for p in Provenance]},
                    "first_turn": {"type": "integer"},
                    "last_turn": {"type": "integer"},
                    "quote": _TEXT,
                    "anchor": _TEXT,
                    "asks": _TEXT,
                    "topic": _TEXT,
                },
            },
        },
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["slot", "turn", "quote"],
                "properties": {"slot": _TEXT, "turn": {"type": "integer"}, "quote": _TEXT},
            },
        },
        "threads": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["slot", "state", "statement", "turn"],
                "properties": {
                    "slot": _TEXT,
                    "state": {"type": "string", "enum": ["open", "done"]},
                    "statement": _TEXT,
                    "turn": {"type": "integer"},
                },
            },
        },
    },
}


_PLUGIN_SWITCHES = ("CLAUDE_CODE_PLUGIN_DIRS", "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS")


class ClaudeCode:
    """Asks the user's own Claude Code, run headless, so remcycle never handles credentials.

    The run loads no settings, tools or project context and writes no transcript, so it
    cannot be mistaken for a session and archived in turn. The answer comes back in the
    shape of `schema`.
    """

    def __init__(
        self,
        model: str = "sonnet",
        *,
        schema: dict = SCHEMA,
        system: str = SYSTEM,
        effort: str | None = None,
        timeout: int = 900,
    ) -> None:
        self._model = model
        self._effort = effort
        self._schema = schema
        self._system = system
        self._timeout = timeout

    def __call__(self, prompt: str) -> Reply:
        command = [
            "claude", "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(self._schema),
            "--model", self._model,
            "--system-prompt", self._system,
            "--tools", "",
            "--setting-sources", "",
            "--no-session-persistence",
            *(["--effort", self._effort] if self._effort else []),
        ]  # fmt: skip
        # A session passes its plugin folders down to what it starts. Loaded here, a plugin would
        # add to the prompt and run its own hooks around every call.
        plain = {name: value for name, value in os.environ.items() if name not in _PLUGIN_SWITCHES}
        try:
            done = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                cwd=tempfile.gettempdir(),
                env=plain,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ExtractionError(f"could not run Claude Code: {e}") from e
        try:
            envelope = json.loads(done.stdout)
        except json.JSONDecodeError as e:
            raise ExtractionError(
                f"Claude Code did not answer in JSON (exit {done.returncode}): {done.stderr.strip()[:300]}"
            ) from e
        answer = envelope.get("structured_output")
        if envelope.get("is_error") or not isinstance(answer, dict):
            raise ExtractionError(f"Claude Code gave no structured answer: {str(envelope.get('result'))[:300]}")
        return Reply(answer, float(envelope.get("total_cost_usd") or 0.0))
