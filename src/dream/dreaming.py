"""The dream: read the sessions not yet read, reconcile what they established into memory, report.

Nothing here writes to the memory Claude Code loads unless `publish` is asked for.
Each project's changes are made in a staging copy and accepted into remcycle's own
copy only when the gate finds nothing wrong.
"""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from dream.archive import Archive, Undreamt
from dream.claims import Claim, Provenance, Scope, Status
from dream.extract import ExtractionError, Rejected, Runner, extract, from_json, to_json
from dream.gate import INDEX_BYTE_LIMIT, INDEX_LINE_LIMIT, check
from dream.memory import MemoryStore
from dream.mirror import LiveChanged, Mirror
from dream.reconcile import Add, Confirm, Contest, Op, Question, Reject, Supersede, reconcile

GLOBAL = "(global)"
# Room left in the index for what sessions add during the day.
_HEADROOM_LINES = 10
_HEADROOM_BYTES = 1_500


@dataclass
class ProjectReport:
    project: str
    ops: list[Op] = field(default_factory=list)
    unsupported: list[Rejected] = field(default_factory=list)
    """Claims the model returned that failed the quote check."""
    problems: list[str] = field(default_factory=list)
    """Why the gate refused the staged memory. Empty when it was accepted."""
    merged: bool = False
    published: bool = False
    unindexed: list[str] = field(default_factory=list)
    """Entries whose index line was dropped to keep the index within what Claude Code loads."""

    def count(self, kind: type) -> int:
        return sum(isinstance(op, kind) for op in self.ops)

    @property
    def added(self) -> int:
        return self.count(Add)


@dataclass
class DreamReport:
    projects: list[ProjectReport] = field(default_factory=list)
    sessions: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    """Sessions the model could not be asked about, with the reason. They stay unread."""
    cost_usd: float = 0.0


def dream(
    archive: Archive,
    *,
    memory_root: Path,
    live_root: Path,
    runner: Runner,
    publish: bool = False,
    limit: int | None = None,
    now: str | None = None,
    progress: Callable[[str], None] = lambda line: None,
) -> DreamReport:
    """Read up to `limit` unread sessions and bring each project's memory up to date."""
    now = now or datetime.now(UTC).isoformat()
    report = DreamReport()
    waiting = archive.awaiting_dream()[:limit]
    by_project: dict[str, list[Undreamt]] = defaultdict(list)
    for session in waiting:
        by_project[session.project].append(session)

    global_claims: list[Claim] = []
    global_sessions: list[str] = []
    universal = Mirror(memory_root / key(GLOBAL))
    known_everywhere = _active(MemoryStore(universal.folder)) if universal.folder.exists() else {}

    for project, sessions in by_project.items():
        mirror = Mirror(memory_root / key(project))
        live = live_root / key(project) / "memory"
        mirror.sync(live)
        store = MemoryStore(mirror.stage())
        outcome = ProjectReport(project)
        read: list[str] = []

        for session in sessions:
            progress(f"reading {session.session_id[:8]} {session.title or ''}".rstrip())
            kept = archive.digest(session.session_id)
            try:
                extraction = from_json(kept) if kept else extract(
                    session.session_id,
                    archive.show(session.session_id),
                    known={**known_everywhere, **_active(store)},
                    open_threads=store.threads(now),
                    runner=runner,
                )
            except ExtractionError as e:
                report.failures.append((session.session_id, str(e)))
                continue
            if not kept:
                archive.keep_digest(session.session_id, to_json(extraction))
            store.note_threads(extraction.threads, session.session_id, session.ended_at or now)
            report.sessions += 1
            report.cost_usd += extraction.cost_usd
            outcome.unsupported.extend(extraction.rejected)
            ours, everyone = _routed(extraction.claims)
            global_claims.extend(everyone)
            ops = reconcile(store.entries(), ours)
            store.apply(ops)
            outcome.ops.extend(ops)
            read.append(session.session_id)

        outcome.unindexed = store.fit_index(INDEX_LINE_LIMIT - _HEADROOM_LINES, INDEX_BYTE_LIMIT - _HEADROOM_BYTES)
        if _settle(mirror, outcome, f"dream: {len(read)} sessions"):
            for session_id in read:
                archive.record_dream(session_id)
            global_sessions.extend(read)
            if publish:
                try:
                    mirror.publish(live)
                    outcome.published = True
                except LiveChanged as e:
                    outcome.problems.append(str(e))
        if outcome.ops or outcome.unsupported or outcome.problems or outcome.unindexed:
            report.projects.append(outcome)

    if global_claims:
        universal.sync(None)
        store = MemoryStore(universal.stage())
        outcome = ProjectReport(GLOBAL, ops=reconcile(store.entries(), global_claims))
        store.apply(outcome.ops)
        _settle(universal, outcome, "dream: global")
        report.projects.append(outcome)
    return report


def _settle(mirror: Mirror, outcome: ProjectReport, message: str) -> bool:
    """Accept the staged memory if the gate allows it."""
    outcome.problems = check(mirror.folder, mirror.staging)
    outcome.merged = not outcome.problems
    if outcome.merged:
        mirror.accept(message)
    return outcome.merged


def _routed(claims: tuple[Claim, ...]) -> tuple[list[Claim], list[Claim]]:
    """Claims for this project, and claims for every project.

    Only the person's own words can make a claim apply everywhere; anything else
    marked global is kept for the project it was said in.
    """
    ours: list[Claim] = []
    everyone: list[Claim] = []
    for claim in claims:
        if claim.scope == Scope.GLOBAL and claim.provenance == Provenance.HUMAN:
            everyone.append(claim)
        else:
            ours.append(replace(claim, scope=Scope.PROJECT))
    return ours, everyone


def _active(store: MemoryStore) -> dict[str, str]:
    return {slot: entry.statement for slot, entry in store.entries().items() if entry.status == Status.ACTIVE}


def key(project: str) -> str:
    """The folder name Claude Code gives a project: its path with everything but letters and digits dashed."""
    return re.sub(r"[^A-Za-z0-9]", "-", project)


def render(report: DreamReport) -> str:
    """The report as Markdown, for a person to read in the morning."""
    lines = [f"# Dream report", "", f"{report.sessions} sessions read, ${report.cost_usd:.2f} of model use."]
    for session_id, reason in report.failures:
        lines.append(f"- Could not read {session_id[:8]}: {reason}")
    for project in report.projects:
        lines += ["", f"## {project.project}", ""]
        if project.problems:
            lines.append("Not accepted. " + " ".join(f"{problem}." for problem in project.problems))
        counts = [
            (project.count(Add), "added"),
            (project.count(Confirm), "confirmed"),
            (project.count(Supersede), "superseded"),
            (project.count(Contest), "withheld pending your ruling"),
            (project.count(Question), "questions for you"),
            (project.count(Reject) + len(project.unsupported), "rejected"),
        ]
        lines.append(", ".join(f"{n} {label}" for n, label in counts if n) or "No changes.")
        lines.append("")
        for op in project.ops:
            match op:
                case Add(claim):
                    lines.append(f"- Added `{claim.slot}` ({claim.provenance}): {claim.statement}")
                case Supersede(slot, claim):
                    lines.append(f"- Superseded `{slot}` ({claim.provenance}): {claim.statement}")
                case Contest(slot, claim):
                    lines.append(f"- Withheld `{slot}`: a session suggests otherwise: {claim.statement}")
                case Question(slot, claim):
                    lines.append(f"- Question on `{slot}`: a session suggests: {claim.statement}")
                case Reject(claim, reason):
                    lines.append(f"- Rejected `{claim.slot}`: {reason}: {claim.statement}")
        if project.unindexed:
            lines.append(
                f"- {len(project.unindexed)} entries left the index to keep it within what Claude Code loads "
                f"(still on file): {', '.join(project.unindexed)}"
            )
        for rejected in project.unsupported:
            lines.append(f"- Rejected `{rejected.slot}`: {rejected.reason}: {rejected.statement}")
    return "\n".join(lines) + "\n"
