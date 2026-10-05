"""Command line: `dream ingest`, `dream search`, `dream show`, `dream run`, `dream queue`."""

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from dream.archive import NO_PROJECT, Archive, project_of
from dream import outside, review
from dream.dreaming import GLOBAL, dream, key, render, review_project
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
            return args.run(archive, args)
        except LookupError as e:
            print(f"dream: {e}", file=sys.stderr)
            return 1


def _ingest(archive: Archive, args: argparse.Namespace) -> int:
    report = archive.ingest(args.root, exclude=args.exclude)
    print(f"{report.added} added, {report.updated} updated, {report.unchanged} unchanged, {report.excluded} excluded")
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
    remembered = (
        []
        if project is None
        else [found for copy in (project, GLOBAL) for found in MemoryStore(args.memory / key(copy)).find(asked)]
    )
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
    return 0


def _show(archive: Archive, args: argparse.Namespace) -> int:
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
    written = args.reports / f"{datetime.now():%Y-%m-%d-%H%M%S}.md"
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
    print(json.dumps(context(args.memory, _project(args), _now())))
    return 0


def _status(archive: Archive, args: argparse.Namespace) -> int:
    print(json.dumps(status(args.memory, _project(args), _now())))
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
    copy = args.memory / file.parent.parent.name
    if file.stem in MemoryStore(copy).entries():
        print(MemoryStore(copy).note_read(file.stem, Path(project_of(str(Path.cwd()))), _now()) or "")
    return 0


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

    queue = command("queue", _queue, "list disagreements waiting for your ruling")
    queue.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)

    for name, run_, help_ in [
        ("context", _context, "what a new session in this project is given, as JSON"),
        ("status", _status, "what the dream holds for this project, as JSON"),
    ]:
        sub = command(name, run_, help_)
        sub.add_argument("--project", type=Path, help="project folder (default: this repository)")
        sub.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)

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

    return parser


if __name__ == "__main__":
    sys.exit(main())
