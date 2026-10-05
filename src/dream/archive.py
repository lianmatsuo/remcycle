"""The archive: verbatim session turns in SQLite, found by ranked full-text search."""

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from dream.redact import redact
from dream.transcript import Author, Kind, Session, Turn, parse_transcript

RULES_VERSION = 1
"""Raise this whenever ingest would derive something different from the same transcript
(what the parser keeps, what is redacted, how a project is identified). Sessions archived
under an older value are derived again, for as long as their transcripts still exist."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    project    TEXT,
    title      TEXT,
    cwd        TEXT,
    git_branch TEXT,
    entrypoint TEXT,
    started_at TEXT,
    ended_at   TEXT,
    source_size     INTEGER NOT NULL,
    source_mtime_ns INTEGER NOT NULL,
    rules           INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    seq        INTEGER NOT NULL,
    author     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL,
    uuid       TEXT,
    timestamp  TEXT,
    UNIQUE (session_id, seq)
);
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    text, content='turns', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""


_CLAUDE_WORKTREE = re.compile(r"/\.claude/worktrees/.*")
_LINKED_WORKTREE = re.compile(r"gitdir:\s*(.*)/\.git/worktrees/[^/]+\s*$")


NO_PROJECT = "(no project)"
"""The project of every session started without a folder."""


def project_of(cwd: str | None) -> str:
    """The project a working directory belongs to: its git repository, or itself outside one.

    A linked worktree counts as the repository it was made from. That is read from the
    worktree while it still exists; once it is gone, only Claude's own worktree layout
    can still be recognised, from the path.
    """
    if not cwd or "/scratch-workspaces/" in cwd:  # where the desktop app puts a session with no folder
        return NO_PROJECT
    for folder in (Path(cwd), *Path(cwd).parents):
        marker = folder / ".git"
        if marker.is_file() and (linked := _LINKED_WORKTREE.match(marker.read_text())):
            return linked[1]
        if marker.exists():
            return str(folder)
    return _CLAUDE_WORKTREE.sub("", cwd)


@dataclass(frozen=True)
class Hit:
    session_id: str
    seq: int
    author: Author
    kind: Kind
    timestamp: str | None
    snippet: str
    title: str | None
    project: str | None
    matched_all: bool
    """False when no turn held every word and this one holds only some of them."""


@dataclass
class IngestReport:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    kept_longer: list[str] = field(default_factory=list)
    redacted: int = 0
    """Secrets replaced in the sessions stored by this run."""
    excluded: int = 0


class Archive:
    def __init__(self, path: Path) -> None:
        # The archive holds what was typed into every session, so only its owner may read it.
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        path.chmod(0o600)
        self._db.executescript(_SCHEMA)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._db.close()

    def ingest(self, root: Path, exclude: Iterable[str] = ()) -> IngestReport:
        """Bring the archive up to date with the transcripts under root.

        Sessions belonging to a project in `exclude`, or to one inside it, are not archived.
        """
        report = IngestReport()
        # One level down only: deeper .jsonl files are subagent transcripts, not sessions.
        for path in sorted(root.glob("*/*.jsonl")):
            stat = path.stat()
            source = (stat.st_size, stat.st_mtime_ns)
            stored = self._db.execute(
                "SELECT source_size, source_mtime_ns, rules FROM sessions WHERE session_id = ?", (path.stem,)
            ).fetchone()
            if stored == (*source, RULES_VERSION):
                report.unchanged += 1
                continue
            session = parse_transcript(path)
            project = project_of(session.cwd)
            if any(project == parent or project.startswith(parent.rstrip("/") + "/") for parent in exclude):
                report.excluded += 1
                continue
            if len(session.turns) < self._turn_count(session.session_id):
                # The archive outlives its sources: a transcript that shrank never replaces a fuller copy.
                report.kept_longer.append(session.session_id)
                continue
            report.redacted += self._store(session, project, source)
            if stored:
                report.updated += 1
            else:
                report.added += 1
        return report

    def _turn_count(self, session_id: str) -> int:
        (count,) = self._db.execute("SELECT count(*) FROM turns WHERE session_id = ?", (session_id,)).fetchone()
        return count

    def search(
        self,
        query: str,
        *,
        project: str | None,
        since: str | None = None,
        until: str | None = None,
        tools: bool = False,
        reports: bool = False,
        limit: int = 10,
    ) -> list[Hit]:
        """Best-matching turns first. `project=None` searches every project.

        The query is read as plain words. Turns holding all of them win; if none
        does, turns holding any of them are returned instead. `since` and `until`
        are inclusive YYYY-MM-DD dates, compared against the turn's UTC timestamp.
        Tool calls outnumber prose and would crowd it out, so they are searched
        only when `tools` is set. Subagent reports are what an agent observed, not
        what was said in the session, and are searched only when `reports` is set.
        """
        terms = [f'"{term}"' for term in re.findall(r"\w+", query)]
        if not terms:
            return []
        scope = {"project": project, "since": since, "until": until, "tools": tools, "reports": reports, "limit": limit}
        return self._matching(" ".join(terms), scope, True) or self._matching(" OR ".join(terms), scope, False)

    def _matching(self, match: str, scope: dict[str, str | int | None], matched_all: bool) -> list[Hit]:
        rows = self._db.execute(
            """
            SELECT s.session_id, t.seq, t.author, t.kind, t.timestamp,
                   snippet(turns_fts, 0, '', '', '…', 32), s.title, s.project
            FROM turns_fts
            JOIN turns t ON t.id = turns_fts.rowid
            JOIN sessions s ON s.session_id = t.session_id
            WHERE turns_fts MATCH :match
              AND (:project IS NULL OR s.project = :project
                   OR substr(s.project, 1, length(:project) + 1) = :project || '/')
              AND (:since IS NULL OR substr(t.timestamp, 1, 10) >= :since)
              AND (:until IS NULL OR substr(t.timestamp, 1, 10) <= :until)
              AND (:tools OR t.kind != 'tool')
              AND (:reports OR t.kind != 'report')
            ORDER BY rank
            LIMIT :limit
            """,
            {"match": match, **scope},
        )
        return [
            Hit(sid, seq, Author(author), Kind(kind), *rest, matched_all) for sid, seq, author, kind, *rest in rows
        ]

    def show(self, session: str, *, first: int = 0, last: int | None = None) -> list[Turn]:
        """One session's turns as archived, from `first` to `last` inclusive.

        `session` is a session id or any prefix of one that picks out a single session.
        """
        matches = self._db.execute(
            "SELECT session_id FROM sessions WHERE substr(session_id, 1, length(?1)) = ?1", (session,)
        ).fetchall()
        if not matches:
            raise LookupError(f"no archived session starts with {session!r}")
        if len(matches) > 1:
            raise LookupError(f"{len(matches)} archived sessions start with {session!r}; give more of the id")
        ((session_id,),) = matches
        rows = self._db.execute(
            """
            SELECT seq, author, kind, text, uuid, timestamp FROM turns
            WHERE session_id = ?1 AND seq >= ?2 AND (?3 IS NULL OR seq <= ?3)
            ORDER BY seq
            """,
            (session_id, first, last),
        )
        return [Turn(seq, Author(author), Kind(kind), *rest) for seq, author, kind, *rest in rows]

    def _store(self, session: Session, project: str, source: tuple[int, int]) -> int:
        """Replace the session's archived copy; returns how many secrets were redacted from it."""
        cleaned = [(turn, *redact(turn.text)) for turn in session.turns]
        with self._db:
            self._db.execute("DELETE FROM turns WHERE session_id = ?", (session.session_id,))
            self._db.execute(
                "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.session_id,
                    project,
                    session.title,
                    session.cwd,
                    session.git_branch,
                    session.entrypoint,
                    session.started_at,
                    session.ended_at,
                    *source,
                    RULES_VERSION,
                ),
            )
            self._db.executemany(
                "INSERT INTO turns (session_id, seq, author, kind, text, uuid, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(session.session_id, t.seq, t.author, t.kind, text, t.uuid, t.timestamp) for t, text, _ in cleaned],
            )
        return sum(count for _, _, count in cleaned)
