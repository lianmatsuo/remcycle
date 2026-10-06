"""Command line: `dream ingest`, `dream search`, `dream show`, `dream run`, `dream queue`."""

import argparse
import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows has no fcntl, and there commands do not take turns.
    fcntl = None

from dream import daily, outside, review
from dream.archive import NO_PROJECT, Archive, Recap, project_of
from dream.dreaming import GLOBAL, DreamReport, dream, key, publish_project, render, review_project
from dream.extract import ClaudeCode
from dream.gate import JUDGE_SCHEMA, JUDGE_SYSTEM, judge_with
from dream.memory import MemoryStore
from dream.outside import witness_with
from dream.session import context, status
from dream.settings import HISTORIES, Settings, load_settings, put_setting


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
        try:
            found = store.find(asked)
            store.note_found([entry.slot for entry in found], _now())
        except (OSError, ValueError):
            # The dream replaces a copy's files whole when it accepts a change. A search that lands
            # in that moment finds them missing or half there, and answers from the archive alone.
            continue
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
    return 1 if _dreamt(archive, args).failures else 0


def _dreamt(archive: Archive, args: argparse.Namespace) -> DreamReport:
    """One dream run: the archive brought up to date, the dream, and its report printed and saved."""
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
        since=args.since,
        progress=lambda line: print(line, flush=True),
    )
    written = args.reports / f"{datetime.now().astimezone():%Y-%m-%d-%H%M%S}.md"
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text(render(report))
    print(render(report))
    print(f"report saved to {written}")
    return report


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
    # A copy of memory is a folder with its own history. The folder the dream works in beside it has none.
    folders = sorted(path for path in args.memory.iterdir() if (path / ".git").is_dir()) if args.memory.is_dir() else []
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
    held = status(args.memory, _project(args), _now(), live_root=args.root)
    print(json.dumps({**held, "daily": _daily_status(archive, args)}))
    return 0


def _daily_status(archive: Archive, args: argparse.Namespace) -> dict:
    """How the daily dream stands, and how many sessions each history would have it read."""
    state = daily.load(args.state)
    began = state.began or _now()
    return {
        "on": args.daily_on,
        "history": args.daily_history,
        "limit": args.daily_limit,
        "waiting": {history: len(archive.awaiting_dream(daily.cutoff(history, began))) for history in HISTORIES},
        "started": state.started,
        "finished": state.finished,
        "failed": state.failed,
    }


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
    try:
        if file.stem in MemoryStore(copy).entries():
            print(MemoryStore(copy).note_read(file.stem, Path(project_of(str(Path.cwd()))), _now()) or "")
    except (OSError, ValueError):
        # The dream is replacing the copy's files at this moment, so this read goes uncounted.
        pass
    return 0


def _daily(archive: Archive, args: argparse.Namespace) -> int:
    if args.change:
        return _daily_change(args.change)
    if args.background:
        return _daily_run(archive, args)
    if not args.daily_on:
        print("the daily dream is off: `dream daily on` turns it back on")
        return 0
    with _serialised(args.state):
        state = daily.begin(daily.load(args.state), _now())
        daily.save(args.state, state)
        if not daily.is_due(state, _now()):
            print(f"the daily dream is not due: the last one started at {state.started}")
            return 0
        waiting = archive.awaiting_dream(daily.cutoff(args.daily_history, state.began))
        if not waiting:
            print("nothing new for the daily dream to read")
            return 0
        daily.save(args.state, replace(state, started=_now(), finished=None, failed=None, log=str(args.log)))
    daily.start([sys.executable, "-m", "dream.cli", "daily", "--background", "--db", str(args.db), "--root", str(args.root)], args.log)
    print(f"the daily dream started in the background: {len(waiting)} session{'' if len(waiting) == 1 else 's'} to read")
    return 0


def _daily_run(archive: Archive, args: argparse.Namespace) -> int:
    """The background half of `dream daily`: one dream over what is due, and a record of how it ended."""
    state = daily.load(args.state)
    run = argparse.Namespace(
        **vars(args), publish=False, limit=args.daily_limit, since=daily.cutoff(args.daily_history, state.began or _now())
    )
    failed: str | None = "it stopped before it finished"
    code = 1
    try:
        with _turn(args.memory):
            report = _dreamt(archive, run)
        code = 1 if report.failures else 0
        failed = _failed(report.failures)
    except _Busy as e:
        failed = str(e)
    finally:
        daily.save(args.state, replace(daily.load(args.state), finished=_now(), failed=failed))
    return code


def _failed(failures: list[tuple[str, str]]) -> str | None:
    """What the panel says went wrong, from the sessions a run could not read: how many, and the first reason."""
    if not failures:
        return None
    count = len(failures)
    return f"{count} session{'' if count == 1 else 's'} could not be read, because: {failures[0][1]}"


def _daily_change(words: list[str]) -> int:
    """Change how the daily dream runs, in the settings file, where the change can also be made by hand."""
    match words:
        case ["on" | "off" as switch]:
            put_setting(os.environ, Path.home(), "daily_dream", "true" if switch == "on" else "false")
            print(f"the daily dream is {switch}")
        case ["history", choice] if choice in HISTORIES:
            put_setting(os.environ, Path.home(), "daily_history", json.dumps(choice))
            print(f"the daily dream reads {_HISTORY_SAID[choice]}")
        case ["limit", "none"]:
            put_setting(os.environ, Path.home(), "daily_limit", None)
            print("the daily dream reads every session that is due")
        case ["limit", count] if count.isdigit() and int(count) > 0:
            put_setting(os.environ, Path.home(), "daily_limit", str(int(count)))
            print(f"the daily dream reads at most {int(count)} sessions a day")
        case _:
            print("usage: dream daily [on | off | history new|week|all | limit N|none]", file=sys.stderr)
            return 2
    return 0


_HISTORY_SAID = {
    "new": "only sessions from after it began",
    "week": "sessions from the week before it began, and every one since",
    "all": "every session, however old",
}


@contextmanager
def _serialised(file: Path) -> Iterator[None]:
    """Hold a lock beside `file` for a moment, so two sessions starting together do not both start a dream."""
    if fcntl is None:
        yield
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.with_name(f"{file.name}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


_CHANGES_MEMORY = (_run, _review, _publish, _resolve, _close, _reopen)


def _project(args: argparse.Namespace) -> str:
    return project_of(str((args.project or Path.cwd()).resolve()))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _day(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def _parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dream", description="Archive of Claude Code sessions.")
    parser.set_defaults(
        exclude=settings.exclude,
        effort=settings.effort,
        daily_on=settings.daily,
        daily_history=settings.daily_history,
        daily_limit=settings.daily_limit,
        state=settings.memory.parent / "daily.json",
    )
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
    run.add_argument("--since", help=argparse.SUPPRESS)
    run.add_argument(
        "--publish", action="store_true", help="write accepted memory to the folders Claude Code loads"
    )

    daily_ = command(
        "daily",
        _daily,
        "start the daily dream if one is due, or change it: on, off, history new|week|all, limit N|none",
    )
    daily_.add_argument("change", nargs="*", metavar="CHANGE")
    daily_.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    daily_.add_argument("--root", type=Path, default=settings.transcripts, help=argparse.SUPPRESS)
    daily_.add_argument("--memory", type=Path, default=settings.memory, help=argparse.SUPPRESS)
    daily_.add_argument("--reports", type=Path, default=settings.reports, help=argparse.SUPPRESS)
    daily_.set_defaults(model=settings.model, log=settings.memory.parent / "daily.log")

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
