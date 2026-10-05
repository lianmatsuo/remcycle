"""Command line for the archive: `dream ingest`, `dream search`, `dream show`."""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from dream.archive import NO_PROJECT, Archive, project_of
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
    hits = archive.search(
        " ".join(args.words),
        project=project,
        since=args.since,
        until=args.until,
        tools=args.tools,
        reports=args.reports,
        limit=args.limit,
    )
    if not hits:
        print("no matches" if project is None else f"no matches in {project} (--all-projects searches everywhere)")
    elif not hits[0].matched_all:
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


def _day(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def _parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dream", description="Archive of Claude Code sessions.")
    parser.set_defaults(exclude=settings.exclude)
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
    scope = search.add_mutually_exclusive_group()
    scope.add_argument("--project", type=Path, help="project folder to search (default: this repository)")
    scope.add_argument("--all-projects", action="store_true", help="search every project")
    scope.add_argument("--no-project", action="store_true", help="search sessions started without a folder")
    search.add_argument("--since", type=_day, metavar="YYYY-MM-DD")
    search.add_argument("--until", type=_day, metavar="YYYY-MM-DD")
    search.add_argument("--tools", action="store_true", help="also search tool calls (files touched, commands run)")
    search.add_argument("--reports", action="store_true", help="also search subagents' final reports")
    search.add_argument("--limit", type=int, default=10)

    show = command("show", _show, "print a session's turns as archived")
    show.add_argument("session", help="session id, or the start of one")
    show.add_argument("--first", type=int, default=0, metavar="N")
    show.add_argument("--last", type=int, metavar="N")

    return parser


if __name__ == "__main__":
    sys.exit(main())
