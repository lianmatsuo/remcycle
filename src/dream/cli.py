"""Command line: `dream ingest`, `dream search`, `dream show`, `dream run`, `dream queue`."""

import argparse
import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows has no fcntl, and there commands do not take turns.
    fcntl = None

from dream import outside, review
from dream.archive import NO_PROJECT, Archive, Recap, project_of
from dream.dreaming import GLOBAL, dream, key, publish_project, render, review_project
from dream.extract import ClaudeCode
from dream.gate import JUDGE_SCHEMA, JUDGE_SYSTEM, judge_with
from dream.memory import MemoryStore
from dream.outside import witness_with
from dream.session import context, status
from dream.settings import Settings, load_settings


def main(argv: list[str] | None = None) -> int:
    try:
        settings = load_settings(os.environ, Path.home())
    except ValueError as e:
        print(f"dream: {e}", file=sys.stderr)
        return 2
    args = _parser(settings).parse_args(argv)
    with Archive(args.db) as archive:
        try:
            if args.run in _CHANGES_MEMORY:
                with _turn(args.memory):
                    return args.run(archive, args)
            return args.run(archive, args)
        except (LookupError, _Busy) as e:
            print(f"dream: {e}", file=sys.stderr)
            return 1


_TURN_WAIT = 5.0
"""Seconds a command that changes memory waits for another to finish before it gives up."""


class _Busy(Exception):
    """Another command is changing memory and did not finish in time."""


@contextmanager
def _turn(memory: Path) -> Iterator[None]:
    """Hold the one turn at changing memory, so that two commands never rewrite the same files at once."""
    if fcntl is None:
        yield
        return
    memory.mkdir(parents=True, exist_ok=True)
    with (memory / ".lock").open("w") as lock:
        deadline = time.monotonic() + _TURN_WAIT
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as e:
                if time.monotonic() >= deadline:
                    raise _Busy("another dream command is changing memory; try again when it has finished") from e
                time.sleep(0.05)
        yield


def _ingest(archive: Archive, args: argparse.Namespace) -> int:
    report = archive.ingest(args.root, exclude=args.exclude)
    print(f"{report.added} added, {report.updated} updated, {report.unchanged} unchanged, {report.excluded} excluded")
    print(f"{len(archive.awaiting_dream())} not yet read by the dream")
    if report.redacted:
        print(f"{report.redacted} secrets redacted")
    for session_id in report.kept_longer:
        print(f"kept the archived copy of {session_id}: its transcript now has fewer turns")
    return 0


def _search(archive: Archive, args: argparse.Namespace) -> int:
    if args.all_projects:
        project = None
    elif args.no_project:
        project = NO_PROJECT
    else:
        project = project_of(str((args.project or Path.cwd()).resolve()))
    asked = " ".join(args.words)
    remembered = []
    for copy in () if project is None else (project, GLOBAL):
        store = MemoryStore(args.memory / key(copy))
        found = store.find(asked)
        store.note_found([entry.slot for entry in found], _now())
        remembered += found
    for found in remembered:
        print(f"memory      {found.slot} ({found.matched})  {found.statement}")
    hits = archive.search(
        [asked, *args.also],
        project=project,
        since=args.since,
        until=args.until,
        tools=args.tools,
        reports=args.reports,
        limit=args.limit,
    )
    if not hits and not remembered:
        print("no matches" if project is None else f"no matches in {project} (--all-projects searches everywhere)")
    elif hits and not hits[0].matched_all:
        print("no turn holds every word; these hold some of them")
    for hit in hits:
        day = (hit.timestamp or "")[:10]
        print(f"{day}  {hit.author:<10} {hit.session_id[:8]}#{hit.seq}  {hit.title or ''}".rstrip())
        print(f"    {' '.join(hit.snippet.split())}")
    recaps = [archive.recap(session_id) for session_id in dict.fromkeys(hit.session_id for hit in hits)]
    if any(recap.summary for recap in recaps):
        print("\nWhat these sessions were (dream show SESSION --summary gives the whole of one):")
    for recap in recaps:
        if recap.summary:
            print(_heading(recap))
            print(f"    {_clipped(' '.join(recap.summary.split()))}")
    return 0


_BRIEF = 300


def _clipped(text: str) -> str:
    """The text, cut at a word once it runs past what a search result has room for."""
    return text if len(text) <= _BRIEF else text[:_BRIEF].rsplit(" ", 1)[0].rstrip(".,;:") + "…"


def _heading(recap: Recap) -> str:
    """A session on one line: the start of its id, its day, its title and its length."""
    named = [recap.session_id[:8], (recap.ended_at or "")[:10], recap.title or "", f"{recap.turns} turns"]
    return "  ".join(part for part in named if part)


def _show(archive: Archive, args: argparse.Namespace) -> int:
    if args.summary:
        recap = archive.recap(args.session)
        print(_heading(recap))
        if recap.summary:
            print(recap.summary)
            return 0
        print("The dream has not read this session as it now stands, so it has no summary yet.")
        if recap.opening:
            print(f"It opened with: {_clipped(' '.join(recap.opening.split()))}")
        return 0
    for turn in archive.show(args.session, first=args.first, last=args.last):
        print(f"#{turn.seq} {turn.author} {turn.timestamp or ''}".rstrip())
        print(f"{turn.text}\n")
    return 0


def _run(archive: Archive, args: argparse.Namespace) -> int:
    _ingest(archive, args)
    report = dream(
        archive,
        memory_root=args.memory,
        live_root=args.root,
        runner=ClaudeCode(args.model, effort=args.effort),
        judge=judge_with(ClaudeCode(args.model, schema=JUDGE_SCHEMA, system=JUDGE_SYSTEM, effort=args.effort)),
        witness=witness_with(ClaudeCode(args.model, schema=outside.SCHEMA, system=outside.SYSTEM, effort=args.effort)),
        publish=args.publish,
        limit=args.limit,
        progress=lambda line: print(line, flush=True),
    )
    written = args.reports / f"{datetime.now().astimezone():%Y-%m-%d-%H%M%S}.md"
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text(render(report))
    print(render(report))
    print(f"report saved to {written}")
    return 1 if report.failures else 0


def _review(archive: Archive, args: argparse.Namespace) -> int:
    project = _project(args)
    report = review_project(
        project,
        memory_root=args.memory,
        live_root=args.root,
        runner=ClaudeCode(args.model, schema=review.SCHEMA, system=review.SYSTEM, effort=args.effort),
        judge=judge_with(ClaudeCode(args.model, schema=JUDGE_SCHEMA, system=JUDGE_SYSTEM, effort=args.effort)),
    )
    print(f"{project}: {report.reviewed} memories reviewed, ${report.cost_usd:.2f} of model use")
    print(f"{report.questions} questions for you (see `dream queue`), {report.unsupported} findings dropped")
    if report.kept_flat:
        print("the index stays one line per entry: listed by topic it led to the right memory for fewer questions")
    for failure in report.failures:
        print(f"a pass failed and its memories will be reviewed next time: {failure}")
    for problem in report.problems:
        print(f"not accepted: {problem}")
    return 0 if report.merged else 1


def _publish(archive: Archive, args: argparse.Namespace) -> int:
    plan = publish_project(
        _project(args), memory_root=args.memory, live_root=args.root, backups=args.memory.parent / "backups", write=args.yes
    )
    if not (plan.added or plan.replaced or plan.removed):
        print(f"{plan.project}: Claude Code's memory already matches what the dream holds")
        return 0
    print(f"{plan.project} -> {plan.live}")
    for label, names in (("add", plan.added), ("replace", plan.replaced)):
        if names:
            print(f"{label} {len(names)}:")
            for name in names:
                print(f"  {name}")
    if plan.removed:
        print(f"remove {len(plan.removed)}:")
        for name, why in plan.removed:
            print(f"  {name} ({why})")
    if plan.refused:
        print(f"dream: {plan.refused}", file=sys.stderr)
        return 1
    if plan.written:
        print(f"written. What was there before is kept in {plan.backup}")
    else:
        print("nothing was written. Run again with --yes to write it.")
    return 0


def _queue(archive: Archive, args: argparse.Namespace) -> int:
    folders = sorted(path for path in args.memory.iterdir() if path.is_dir()) if args.memory.is_dir() else []
    waiting = [(folder.name, item) for folder in folders for item in MemoryStore(folder).queue()]
    if not waiting:
        print("nothing is waiting for you")
    for folder, item in waiting:
        state = "withheld" if item.withheld else "still in use"
        print(f"{folder}  {item.slot}  ({state})")
        if item.claim is None:
            print(f"    retire it? {item.reason}")
            continue
        print(f"    a session suggests ({item.claim.provenance}): {item.claim.statement}")
        print(f"    {item.claim.evidence.command}")
    return 0


def _context(archive: Archive, args: argparse.Namespace) -> int:
    print(json.dumps(context(args.memory, _project(args), _now(), live_root=args.root)))
    return 0


def _status(archive: Archive, args: argparse.Namespace) -> int:
    print(json.dumps(status(args.memory, _project(args), _now(), live_root=args.root)))
    return 0


def _resolve(archive: Archive, args: argparse.Namespace) -> int:
    MemoryStore(args.memory / key(_project(args))).resolve(args.slot, accept=args.accept)
    print(f"{args.slot}: {'accepted' if args.accept else 'kept the existing entry'}")
    return 0


def _purge(archive: Archive, args: argparse.Namespace) -> int:
    kept = archive.under(args.exclude)
    if not kept:
        print("no archived session belongs to an excluded project")
        return 0
    if not args.yes:
        print(f"{len(kept)} archived sessions belong to excluded projects:")
        for session in kept:
            print(f"  {session.session_id[:8]}  {session.project}  {session.title or ''}  ({session.turns} turns)")
        print("run again with --yes to remove them from the archive; this cannot be undone")
        return 0
    print(f"removed {archive.remove(session.session_id for session in kept)} sessions from the archive")
    return 0


def _close(archive: Archive, args: argparse.Namespace) -> int:
    MemoryStore(args.memory / key(_project(args))).close_thread(
        args.slot, by="session" if args.session else "you", session_id=args.session, why=args.why, at=_now()
    )
    print(f"{args.slot}: closed")
    return 0


def _reopen(archive: Archive, args: argparse.Namespace) -> int:
    MemoryStore(args.memory / key(_project(args))).reopen(args.slot)
    print(f"{args.slot}: open again")
    return 0


def _note_read(archive: Archive, args: argparse.Namespace) -> int:
    """For the mod: count a session reading a memory file and print any warning about it."""
    file = args.file.resolve()
    # Claude Code keeps a memory at <project>/memory/<name>.md. The dream's own copy holds it at <project>/<name>.md.
    is_own_copy = file.parent.parent == args.memory.resolve()
    copy = file.parent if is_own_copy else args.memory / file.parent.parent.name
    if file.stem in MemoryStore(copy).entries():
        print(MemoryStore(copy).note_read(file.stem, Path(project_of(str(Path.cwd()))), _now()) or "")
    return 0


_CHANGES_MEMORY = (_run, _review, _publish, _resolve, _close, _reopen)


def _project(args: argparse.Namespace) -> str:
    return project_of(str((args.project or Path.cwd()).resolve()))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _day(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def _parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dream", description="Archive of Claude Code sessions.")
    parser.set_defaults(exclude=settings.exclude, effort=settings.effort)
    commands = parser.add_subparsers(required=True)

    def command(name: str, run, help: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help)
        sub.add_argument("--db", type=Path, default=settings.archive, help="archive file (default: %(default)s)")
        sub.set_defaults(run=run)
        return sub

    ingest = command("ingest", _ingest, "bring the archive up to date with the transcripts on disk")
    ingest.add_argument(
        "--root", type=Path, default=settings.transcripts, help="transcripts folder (default: %(default)s)"
    )

    search = command("search", _search, "find turns, best match first")
    search.add_argument("words", nargs="+")
    search.add_argument(
        "--also", action="append", default=[], metavar="PHRASING", help="another way of asking the same thing"
    )
    search.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)
    scope = search.add_mutually_exclusive_group()
    scope.add_argument("--project", type=Path, help="project folder to search (default: this repository)")
    scope.add_argument("--all-projects", action="store_true", help="search every project")
    scope.add_argument("--no-project", action="store_true", help="search sessions started without a folder")
    search.add_argument("--since", type=_day, metavar="YYYY-MM-DD")
    search.add_argument("--until", type=_day, metavar="YYYY-MM-DD")
    search.add_argument("--tools", action="store_true", help="also search tool calls (files touched, commands run)")
    search.add_argument("--reports", action="store_true", help="also search subagents' final reports")
    search.add_argument("--limit", type=int, default=10)

    run = command("run", _run, "read unread sessions and bring memory up to date")
    run.add_argument("--root", type=Path, default=settings.transcripts, help=argparse.SUPPRESS)
    run.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)
    run.add_argument("--reports", type=Path, default=settings.reports, help=argparse.SUPPRESS)
    run.add_argument("--model", default=settings.model, help="model for extraction (default: %(default)s)")
    run.add_argument("--limit", type=int, metavar="N", help="read at most N sessions")
    run.add_argument(
        "--publish", action="store_true", help="write accepted memory to the folders Claude Code loads"
    )

    reviewing = command("review", _review, "review the memories this project already has")
    reviewing.add_argument("--project", type=Path, help="project folder (default: this repository)")
    reviewing.add_argument("--root", type=Path, default=settings.transcripts, help=argparse.SUPPRESS)
    reviewing.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)
    reviewing.add_argument("--model", default=settings.model, help="model for the review (default: %(default)s)")

    publish = command("publish", _publish, "write a project's memory, as the dream holds it, into the folder Claude Code loads")
    publish.add_argument("--project", type=Path, help="project folder (default: this repository)")
    publish.add_argument("--yes", action="store_true", help="write it; without this the change is only listed")
    publish.add_argument("--root", type=Path, default=settings.transcripts, help=argparse.SUPPRESS)
    publish.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)

    queue = command("queue", _queue, "list disagreements waiting for your ruling")
    queue.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)

    for name, run_, help_ in [
        ("context", _context, "what a new session in this project is given, as JSON"),
        ("status", _status, "what the dream holds for this project, as JSON"),
    ]:
        sub = command(name, run_, help_)
        sub.add_argument("--project", type=Path, help="project folder (default: this repository)")
        sub.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)
        sub.add_argument("--root", type=Path, default=settings.transcripts, help=argparse.SUPPRESS)

    resolve = command("resolve", _resolve, "rule on a disagreement listed by `dream queue`")
    resolve.add_argument("slot")
    ruling = resolve.add_mutually_exclusive_group(required=True)
    ruling.add_argument("--accept", action="store_true", help="take the new claim, or retire the entry if that was the question")
    ruling.add_argument("--keep", dest="accept", action="store_false", help="keep the existing entry")
    resolve.add_argument("--project", type=Path, help="project folder (default: this repository)")
    resolve.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)

    purge = command("purge", _purge, "remove archived sessions of projects that are now excluded")
    purge.add_argument("--yes", action="store_true", help="remove them; without it they are only listed")

    close = command("close", _close, "close a thread a session left open")
    close.add_argument("slot")
    close.add_argument("--why", default="", help="what finished it")
    close.add_argument("--session", help=argparse.SUPPRESS)
    reopen = command("reopen", _reopen, "put a closed thread back")
    reopen.add_argument("slot")
    for scoped in (close, reopen):
        scoped.add_argument("--project", type=Path, help="project folder (default: this repository)")
        scoped.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)

    note = command("note-read", _note_read, argparse.SUPPRESS)
    note.add_argument("file", type=Path)
    note.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)

    show = command("show", _show, "print a session's turns as archived")
    show.add_argument("session", help="session id, or the start of one")
    show.add_argument("--first", type=int, default=0, metavar="N")
    show.add_argument("--last", type=int, metavar="N")
    show.add_argument("--summary", action="store_true", help="what the dream made of the session, in place of its turns")

    return parser


if __name__ == "__main__":
    sys.exit(main())
