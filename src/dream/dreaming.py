"""The dream: read the sessions not yet read, reconcile what they established into memory, report.

Nothing here writes to the memory Claude Code loads unless `publish` is asked for.
Each project's changes are made in a staging copy and accepted into remcycle's own
copy only when the gate finds nothing wrong.
"""

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from dream.archive import Archive, Undreamt, has_folder
from dream.claims import Claim, Provenance, Scope, Status
from dream.extract import ExtractionError, Rejected, Runner, extract, from_json, to_json
from dream.gate import Judge, check, index_still_leads
from dream.memory import INDEX_BYTE_LIMIT, INDEX_LINE_LIMIT, SIDE, MemoryStore
from dream.mirror import LiveChanged, Mirror
from dream.outside import Witness
from dream.reconcile import (
    Add,
    Alias,
    Confirm,
    Contest,
    Demote,
    Op,
    Question,
    Reject,
    Review,
    Supersede,
    Withhold,
    reconcile,
)
from dream.review import ReviewReport, review

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
    closed: list[tuple[str, str]] = field(default_factory=list)
    """Threads a change in the repository finished, each with that change."""
    reopened: list[str] = field(default_factory=list)
    """Closed threads that a session reports as unfinished."""

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


@dataclass
class ReadAhead:
    failures: list[tuple[str, str]] = field(default_factory=list)
    """Sessions the model could not be asked about, with the reason. They stay unread."""
    cost_usd: float = 0.0


def read_ahead(
    archive: Archive,
    *,
    memory_root: Path,
    runner: Runner,
    limit: int | None = None,
    since: str | None = None,
    now: str | None = None,
    progress: Callable[[str], None] = lambda line: None,
) -> ReadAhead:
    """Ask the model about each due session that has no digest yet, and keep what it made of each.

    This is the slow part of a dream, and it only reads memory, so it runs while other commands
    may change it. Each session is read against memory as it stood before the run, not with
    what an earlier session in the same run added; reconciling settles the difference.
    """
    now = now or datetime.now(UTC).isoformat()
    ahead = ReadAhead()
    everywhere = memory_root / key(GLOBAL)
    for session in archive.awaiting_dream(since)[:limit]:
        if archive.digest(session.session_id):
            continue
        progress(f"reading {session.session_id[:8]} {session.title or ''}".rstrip())
        try:
            store = MemoryStore(memory_root / key(session.project))
            known = {**(_active(MemoryStore(everywhere)) if everywhere.exists() else {}), **_active(store)}
            threads = {**store.threads(now), **_closed_by(store, session.session_id, now)}
            topics = store.topics()
        except (OSError, ValueError):
            # A copy being replaced at this moment, or one that does not exist yet: read against nothing.
            known, threads, topics = {}, {}, []
        try:
            extraction = extract(
                session.session_id,
                archive.show(session.session_id),
                known=known,
                open_threads=threads,
                topics=topics,
                runner=runner,
            )
        except ExtractionError as e:
            ahead.failures.append((session.session_id, str(e)))
            continue
        archive.keep_digest(session.session_id, to_json(extraction))
        ahead.cost_usd += extraction.cost_usd
    return ahead


def dream(
    archive: Archive,
    *,
    memory_root: Path,
    live_root: Path,
    runner: Runner,
    publish: bool = False,
    limit: int | None = None,
    since: str | None = None,
    ask: bool = True,
    now: str | None = None,
    judge: Judge | None = None,
    witness: Witness | None = None,
    progress: Callable[[str], None] = lambda line: None,
) -> DreamReport:
    """Read up to `limit` unread sessions and bring each project's memory up to date.

    With `since`, only sessions that ended at or after that moment are read. With `ask` false the
    model is not asked about any session: one with no digest kept by `read_ahead` stays unread.

    With a `witness`, each project's repository is also asked whether its own changes
    finished a thread that no session has reported finished.
    """
    now = now or datetime.now(UTC).isoformat()
    report = DreamReport()
    work: dict[str, list[Undreamt]] = {project: [] for project in known(memory_root)}
    for session in archive.awaiting_dream(since)[:limit]:
        work.setdefault(session.project, []).append(session)

    global_claims: list[Claim] = []
    universal = Mirror(memory_root / key(GLOBAL))
    known_everywhere = _active(MemoryStore(universal.folder)) if universal.folder.exists() else {}

    for project, sessions in work.items():
        mirror = Mirror(memory_root / key(project))
        live = live_root / key(project) / "memory"
        mirror.sync(live, project)
        store = MemoryStore(mirror.stage())
        outcome = ProjectReport(project)
        read: list[str] = []

        for session in sessions:
            progress(f"reading {session.session_id[:8]} {session.title or ''}".rstrip())
            kept = archive.digest(session.session_id)
            if not kept and not ask:
                continue
            try:
                extraction = from_json(kept) if kept else extract(
                    session.session_id,
                    archive.show(session.session_id),
                    known={**known_everywhere, **_active(store)},
                    open_threads={**store.threads(now), **_closed_by(store, session.session_id, now)},
                    topics=store.topics(),
                    runner=runner,
                )
            except ExtractionError as e:
                report.failures.append((session.session_id, str(e)))
                continue
            if not kept:
                archive.keep_digest(session.session_id, to_json(extraction))
            outcome.reopened += store.note_threads(extraction.threads, session.session_id, session.ended_at or now)
            report.sessions += 1
            report.cost_usd += extraction.cost_usd
            outcome.unsupported.extend(extraction.rejected)
            ours, everyone = _routed(extraction.claims)
            global_claims.extend(everyone)
            ops: list[Op] = reconcile(store.entries(), ours)
            ops += [
                Demote(c.slot, f"the person corrected the assistant after it followed this: {c.evidence.command}")
                for c in extraction.corrections
                if c.slot in _active(store)
            ]
            store.apply(ops)
            outcome.ops.extend(ops)
            read.append(session.session_id)

        gone = _gone(store, project)
        store.apply(gone)
        outcome.ops.extend(gone)
        outcome.unindexed = store.fit_index(INDEX_LINE_LIMIT - _HEADROOM_LINES, INDEX_BYTE_LIMIT - _HEADROOM_BYTES)
        looked = False
        if witness and Path(project).is_dir() and (left := store.left_open(now)):
            seen = witness(Path(project), left)
            looked = seen.asked
            report.cost_usd += seen.cost_usd
            for done in seen.finished:
                store.close_thread(done.slot, by="dream", why=f"finished by {done.ref}", at=now)
                outcome.closed.append((done.slot, done.ref))
            if looked:
                store.note_checked(now)
        if not (read or outcome.ops or outcome.unindexed or looked):
            continue
        if _settle(mirror, outcome, f"dream: {len(read)} sessions", judge):
            for session_id in read:
                archive.record_dream(session_id)
            if publish:
                try:
                    mirror.publish(live)
                    outcome.published = True
                except LiveChanged as e:
                    outcome.problems.append(str(e))
        if outcome.ops or outcome.unsupported or outcome.problems or outcome.unindexed or outcome.closed or outcome.reopened:
            report.projects.append(outcome)

    if global_claims:
        universal.sync(None)
        store = MemoryStore(universal.stage())
        outcome = ProjectReport(GLOBAL, ops=reconcile(store.entries(), global_claims))
        store.apply(outcome.ops)
        _settle(universal, outcome, "dream: global", judge)
        report.projects.append(outcome)
    return report


def review_project(
    project: str, *, memory_root: Path, live_root: Path, runner: Runner, judge: Judge | None = None
) -> ReviewReport:
    """Review the memories one project already has, in its copy, behind the same gate as the dream."""
    mirror = Mirror(memory_root / key(project))
    mirror.sync(live_root / key(project) / "memory", project)
    store = MemoryStore(mirror.stage())
    store.allow_topics()
    report = review(store, runner)
    budget = (INDEX_LINE_LIMIT - _HEADROOM_LINES, INDEX_BYTE_LIMIT - _HEADROOM_BYTES)
    store.fit_index(*budget)
    judge = _asked_once(judge) if judge else None
    if judge and store.by_topic() and index_still_leads(mirror.folder, mirror.staging, judge):
        # The topics are kept for the next time. Only the index goes back to one line per entry.
        store.keep_flat()
        store.fit_index(*budget)
        report.kept_flat = True
    report.problems = check(mirror.folder, mirror.staging, judge)
    report.merged = not report.problems
    if report.merged:
        mirror.accept(f"review: {report.reviewed} memories")
    return report


@dataclass
class Publication:
    """What publishing a project changes in the folder Claude Code loads its memory from."""

    project: str
    live: Path
    added: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    removed: list[tuple[str, str]] = field(default_factory=list)
    """Each file that would go, with why the dream no longer holds it."""
    written: bool = False
    backup: Path | None = None
    """Where the files that were there before were copied, once written."""
    refused: str = ""
    """Why nothing was written although it was asked for."""


def publish_project(
    project: str, *, memory_root: Path, live_root: Path, backups: Path, write: bool, now: str | None = None
) -> Publication:
    """Work out what the dream's copy of a project's memory would change in Claude Code's own, and with `write` do it.

    The copy first takes in whatever sessions wrote since the dream last looked, so publishing
    never overwrites or removes a session's work.
    """
    if not has_folder(project):
        raise LookupError(f"{project} has no folder of its own for Claude Code to load memory from")
    mirror = Mirror(memory_root / key(project))
    if not mirror.folder.exists():
        raise LookupError(f"the dream holds no memory for {project}")
    live = live_root / key(project) / "memory"
    mirror.sync(live, project)

    ours = {path.name: path for path in mirror.folder.glob("*.md")}
    theirs = {path.name: path for path in live.glob("*.md")} if live.is_dir() else {}
    entries = MemoryStore(mirror.folder).entries()

    def why(name: str) -> str:
        entry = entries.get(Path(name).stem)
        if entry and entry.status == Status.RETIRED:
            return "you retired it"
        if entry and entry.status != Status.ACTIVE:
            return "held back until you rule"
        return "no longer in the dream's copy"

    plan = Publication(
        project,
        live,
        added=sorted(set(ours) - set(theirs)),
        replaced=sorted(name for name in set(ours) & set(theirs) if ours[name].read_bytes() != theirs[name].read_bytes()),
        removed=[(name, why(name)) for name in sorted(set(theirs) - set(ours))],
    )
    if write and (plan.added or plan.replaced or plan.removed):
        stamp = datetime.fromisoformat(now or datetime.now(UTC).isoformat()).strftime("%Y-%m-%dT%H%M%SZ")
        try:
            mirror.publish(live, backup=backups / key(project) / stamp)
        except LiveChanged as e:
            plan.refused = str(e)
        else:
            plan.written = True
            plan.backup = backups / key(project) / stamp
    return plan


def _asked_once(judge: Judge) -> Judge:
    """The same judge, answering a question it has already been asked from memory."""
    answers: dict[tuple[str, tuple[str, ...]], list[str]] = {}

    def asked_once(index: str, questions: Sequence[str]) -> list[str]:
        if (index, tuple(questions)) not in answers:
            answers[index, tuple(questions)] = judge(index, questions)
        return answers[index, tuple(questions)]

    return asked_once


def _settle(mirror: Mirror, outcome: ProjectReport, message: str, judge: Judge | None) -> bool:
    """Accept the staged memory if the gate allows it."""
    outcome.problems = check(mirror.folder, mirror.staging, judge)
    outcome.merged = not outcome.problems
    if outcome.merged:
        mirror.accept(message)
    return outcome.merged


def _closed_by(store: MemoryStore, session_id: str, now: str) -> dict[str, str]:
    """Threads this session closed itself, put back before the model so that its transcript is the second opinion."""
    return {
        closure.slot: f"{closure.statement} (this session closed it, saying: {closure.why})"
        for closure in store.closed(now)
        if closure.by == "session" and closure.session_id == session_id
    }


def known(memory_root: Path) -> list[str]:
    """Projects the dream already keeps a copy of memory for."""
    noted = sorted(memory_root.glob(f"*/{SIDE}/project.json")) if memory_root.is_dir() else []
    return [json.loads(file.read_text(encoding="utf-8"))["project"] for file in noted]


def _gone(store: MemoryStore, project: str) -> list[Op]:
    """Withhold each fact whose file is no longer in the project's repository."""
    root = Path(project)
    if not root.is_dir():
        return []
    return [
        Withhold(slot, f"{entry.anchor}, which it is about, is no longer in the repository")
        for slot, entry in store.entries().items()
        if entry.status == Status.ACTIVE and entry.anchor and not _in_repository(root, entry.anchor)
    ]


def _in_repository(root: Path, anchor: str) -> bool:
    """Whether the path is in the working tree or in the commit that is checked out."""
    path = (root / anchor).resolve()
    if not path.is_relative_to(root.resolve()):
        return True  # not a path inside the repository, so nothing here can say it is gone
    if path.exists():
        return True
    try:
        seen = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"HEAD:{anchor}"], capture_output=True, check=False)
    except OSError:
        return False
    return seen.returncode == 0


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


_KEY_LIMIT = 200


def key(project: str) -> str:
    """The folder name Claude Code gives a project: its path with everything but letters and digits dashed.

    Past 200 characters Claude Code keeps the first 200 and adds a short hash of the whole path.
    """
    dashed = re.sub(r"[^A-Za-z0-9]", "-", project)
    if len(dashed) <= _KEY_LIMIT:
        return dashed
    encoded = project.encode("utf-16-le")
    hashed = 0
    for at in range(0, len(encoded), 2):
        hashed = (hashed * 31 + int.from_bytes(encoded[at : at + 2], "little")) & 0xFFFFFFFF
    if hashed & 0x80000000:
        hashed = (1 << 32) - hashed
    digits = ""
    while hashed:
        hashed, digit = divmod(hashed, 36)
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + digits
    return f"{dashed[:_KEY_LIMIT]}-{digits or '0'}"


def render(report: DreamReport) -> str:
    """The report as Markdown, for a person to read in the morning."""
    lines = ["# Dream report", "", f"{report.sessions} sessions read, ${report.cost_usd:.2f} of model use."]
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
            (project.count(Contest) + project.count(Withhold), "withheld pending your ruling"),
            (project.count(Question) + project.count(Review) + project.count(Demote), "questions for you"),
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
                case Withhold(slot, reason):
                    lines.append(f"- Withheld `{slot}`: {reason}")
                case Demote(slot, reason):
                    lines.append(f"- Left the index, `{slot}`: {reason}")
                case Review(slot, reason):
                    lines.append(f"- Question on `{slot}`: {reason}")
                case Alias(slot, alias):
                    lines.append(f"- `{slot}` is also called `{alias}`")
        for slot, ref in project.closed:
            lines.append(f"- Closed `{slot}`: finished by {ref}")
        for slot in project.reopened:
            lines.append(f"- Reopened `{slot}`: a session reports it unfinished")
        if project.unindexed:
            lines.append(
                f"- {len(project.unindexed)} entries left the index to keep it within what Claude Code loads "
                f"(still on file): {', '.join(project.unindexed)}"
            )
        for rejected in project.unsupported:
            lines.append(f"- Rejected `{rejected.slot}`: {rejected.reason}: {rejected.statement}")
    return "\n".join(lines) + "\n"
