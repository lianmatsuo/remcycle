"""The archive: verbatim session turns in SQLite, found by ranked full-text search."""

import json
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Self

from dream.redact import redact
from dream.transcript import Author, Kind, Session, Turn, parse_transcript

_RRF_K = 60

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
CREATE TABLE IF NOT EXISTS dreams (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    turn_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS digests (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    turn_count INTEGER NOT NULL,
    body       TEXT NOT NULL
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


# A path is matched with either slash between its parts, as on Windows it can be written with either.
_CLAUDE_WORKTREE = re.compile(r"[\\/]\.claude[\\/]worktrees[\\/].*")
_LINKED_WORKTREE = re.compile(r"gitdir:\s*(.*)[\\/]\.git[\\/]worktrees[\\/][^\\/]+\s*$")
_SCRATCH = re.compile(r"[\\/]scratch-workspaces[\\/]")
# How a Windows path starts: with a drive, or with the two slashes of a network share.
_WINDOWS = re.compile(r"[A-Za-z]:[\\/]|[\\/]{2}[^\\/]")


NO_PROJECT = "(no project)"
"""The project of every session started without a folder."""


def project_of(cwd: str | None) -> str:
    """The project a working directory belongs to: its git repository, or itself outside one.

    A linked worktree counts as the repository it was made from. That is read from the
    worktree while it still exists; once it is gone, only Claude's own worktree layout
    can still be recognised, from the path.
    """
    if not cwd or _SCRATCH.search(cwd):  # where the desktop app puts a session with no folder
        return NO_PROJECT
    for folder in (Path(cwd), *Path(cwd).parents):
        marker = folder / ".git"
        if marker.is_file() and (linked := _LINKED_WORKTREE.match(marker.read_text(encoding="utf-8"))):
            return _spelled(linked[1])
        if marker.exists():
            return _spelled(str(folder))
    return _spelled(_CLAUDE_WORKTREE.sub("", cwd))


def has_folder(project: str) -> bool:
    """Whether the project is a folder, and not the group of sessions started without one."""
    return PurePosixPath(project).is_absolute() or PureWindowsPath(project).is_absolute()


def _spelled(folder: str) -> str:
    """A folder's path spelled one way. Windows takes `c:/work` and `C:\\work` for the same folder."""
    if not _WINDOWS.match(folder):
        return folder
    spelled = str(PureWindowsPath(folder[0].upper() + folder[1:]))
    # A share's own top folder comes back with a closing slash, which is kept only where one was written.
    return spelled if folder.endswith(("/", "\\")) else spelled.rstrip("\\")


def _within(project: str | None, parents: Iterable[str]) -> bool:
    """Whether the project is one of `parents` or lies inside one."""
    here = _compared(project or "")
    for parent in map(_compared, parents):
        # A parent may be written with a closing slash, and names the same folder without it.
        inside = parent.rstrip("/\\")
        if here == (inside or parent) or here.startswith((inside + "/", inside + "\\")):
            return True
    return False


def _compared(folder: str) -> str:
    """A folder's path as it is set against another. Windows takes `C:\\Work` and `C:\\work` for the same folder."""
    spelled = _spelled(folder)
    return spelled.casefold() if _WINDOWS.match(spelled) else spelled


def _keep_to_owner(folder: Path) -> None:
    """On Windows, let only the folder's owner, the system and the machine's administrators into it and into whatever is put in it.

    The mode the folder is made with asks for the same, and Python before 3.12.4 ignores it on Windows.
    """
    # The owner of a file, the system and the administrators, under the numbers Windows knows them by in every language.
    let_in = [f"*{who}:(OI)(CI)F" for who in ("S-1-3-4", "S-1-5-18", "S-1-5-32-544")]
    try:
        subprocess.run(["icacls", str(folder), "/inheritance:r", "/grant:r", *let_in], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as e:
        # Taken away again, so that the next command makes it afresh and closes it, and none finds it left open.
        folder.rmdir()
        raise OSError(f"{folder} could not be kept to its owner, so no archive was made in it") from e


@dataclass(frozen=True)
class Kept:
    """An archived session, as much of it as deciding whether to keep it takes."""

    session_id: str
    project: str
    title: str | None
    turns: int


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


@dataclass(frozen=True)
class Recap:
    """One archived session in brief."""

    session_id: str
    title: str | None
    ended_at: str | None
    turns: int
    summary: str | None
    """What the dream made of the session. None until it has read the session as it now stands."""
    opening: str | None
    """The first thing the person typed."""


@dataclass(frozen=True)
class Undreamt:
    """A session the dream has not read, or has not read all of."""

    session_id: str
    project: str
    title: str | None
    ended_at: str | None


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
        is_new = not path.parent.exists()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if is_new and sys.platform == "win32":
            _keep_to_owner(path.parent)
        self._db = sqlite3.connect(path)
        path.chmod(0o600)
        self._db.executescript(_SCHEMA)
        # Search asks whether a project is within another by the rule that decides what is excluded.
        self._db.create_function("within", 2, lambda project, parent: _within(project, (parent,)), deterministic=True)

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
            if _within(project, exclude):
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

    def under(self, parents: Iterable[str]) -> list[Kept]:
        """Archived sessions belonging to one of these projects, or to a project inside one."""
        parents = list(parents)
        rows = self._db.execute(
            """
            SELECT s.session_id, s.project, s.title, (SELECT count(*) FROM turns t WHERE t.session_id = s.session_id)
            FROM sessions s ORDER BY s.started_at, s.session_id
            """
        )
        return [Kept(*row) for row in rows if _within(row[1], parents)]

    def remove(self, session_ids: Iterable[str]) -> int:
        """Delete these sessions and everything kept about them, and rewrite the file without their text."""
        removed = 0
        with self._db:
            for session_id in session_ids:
                for table in ("turns", "dreams", "digests"):
                    self._db.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
                removed += self._db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,)).rowcount
        if removed:
            # Deleted rows stay in the file, and deleted words in the search index, until both are rebuilt.
            self._db.execute("INSERT INTO turns_fts(turns_fts) VALUES ('rebuild')")
            self._db.commit()
            self._db.execute("VACUUM")
        return removed

    def awaiting_dream(self, since: str | None = None) -> list[Undreamt]:
        """Sessions with turns the dream has not read, oldest first.

        With `since`, an ISO time in UTC, only those that ended at or after it.
        """
        rows = self._db.execute(
            """
            SELECT s.session_id, s.project, s.title, s.ended_at
            FROM sessions s LEFT JOIN dreams d ON d.session_id = s.session_id
            WHERE (d.turn_count IS NULL
                   OR d.turn_count != (SELECT count(*) FROM turns t WHERE t.session_id = s.session_id))
              -- A session in which nothing was said has nothing to read.
              AND EXISTS (SELECT 1 FROM turns t WHERE t.session_id = s.session_id)
              -- Both sides are UTC, so the first 19 characters compare whatever each writes after the seconds.
              AND (:since IS NULL OR substr(s.ended_at, 1, 19) >= substr(:since, 1, 19))
            ORDER BY s.ended_at
            """,
            {"since": since},
        )
        return [Undreamt(*row) for row in rows]

    def record_dream(self, session_id: str) -> None:
        """Note that the dream has read the session as it now stands."""
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO dreams VALUES (?, ?)", (session_id, self._turn_count(session_id))
            )

    def keep_digest(self, session_id: str, digest: dict) -> None:
        """Keep what the model made of the session as it now stands, so it is never asked twice."""
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO digests VALUES (?, ?, ?)",
                (session_id, self._turn_count(session_id), json.dumps(digest)),
            )

    def digest(self, session_id: str) -> dict | None:
        """The session's digest, unless the session has grown since it was made."""
        row = self._db.execute(
            "SELECT body FROM digests WHERE session_id = ? AND turn_count = ?",
            (session_id, self._turn_count(session_id)),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _turn_count(self, session_id: str) -> int:
        (count,) = self._db.execute("SELECT count(*) FROM turns WHERE session_id = ?", (session_id,)).fetchone()
        return count

    def search(
        self,
        query: str | Sequence[str],
        *,
        project: str | None,
        since: str | None = None,
        until: str | None = None,
        tools: bool = False,
        reports: bool = False,
        limit: int = 10,
    ) -> list[Hit]:
        """Best-matching turns first. `project=None` searches every project.

        A query is read as plain words. Turns holding all of them win; if none
        does, turns holding any of them are returned instead. Several queries are
        several phrasings of one question: each is searched and the rankings are
        fused, so a turn that more phrasings find ranks higher. Among turns that
        match alike, what the person typed comes before what the assistant wrote.

        `since` and `until` are inclusive YYYY-MM-DD dates, compared against the
        turn's UTC timestamp. Tool calls outnumber prose and would crowd it out, so
        they are searched only when `tools` is set. Subagent reports are what an
        agent observed, not what was said in the session, and are searched only
        when `reports` is set.
        """
        phrasings = [query] if isinstance(query, str) else list(query)
        scope = {"project": project, "since": since, "until": until, "tools": tools, "reports": reports}
        fused: dict[tuple[str, int], float] = {}
        best: dict[tuple[str, int], Hit] = {}
        for phrasing in phrasings:
            terms = [f'"{term}"' for term in re.findall(r"\w+", phrasing)]
            if not terms:
                continue
            pool = {**scope, "limit": max(limit * 3, 30)}
            hits = self._matching(" ".join(terms), pool, True) or self._matching(" OR ".join(terms), pool, False)
            for rank, hit in enumerate(hits):
                key = (hit.session_id, hit.seq)
                # Reciprocal rank fusion: each phrasing votes by where it placed the turn.
                fused[key] = fused.get(key, 0.0) + 1.0 / (_RRF_K + rank)
                if key not in best or (hit.matched_all and not best[key].matched_all):
                    best[key] = hit
        if any(hit.matched_all for hit in best.values()):
            best = {key: hit for key, hit in best.items() if hit.matched_all}
        return [best[key] for key in sorted(best, key=lambda key: -fused[key])][:limit]

    def _matching(self, match: str, scope: dict[str, str | int | None], matched_all: bool) -> list[Hit]:
        rows = self._db.execute(
            """
            SELECT s.session_id, t.seq, t.author, t.kind, t.timestamp,
                   snippet(turns_fts, 0, '', '', '…', 32), s.title, s.project
            FROM turns_fts
            JOIN turns t ON t.id = turns_fts.rowid
            JOIN sessions s ON s.session_id = t.session_id
            WHERE turns_fts MATCH :match
              AND (:project IS NULL OR within(s.project, :project))
              AND (:since IS NULL OR substr(t.timestamp, 1, 10) >= :since)
              AND (:until IS NULL OR substr(t.timestamp, 1, 10) <= :until)
              AND (:tools OR t.kind != 'tool')
              AND (:reports OR t.kind != 'report')
            -- bm25 is negative and lower is better, so a weight above 1 moves a turn up.
            ORDER BY bm25(turns_fts) * CASE t.author WHEN 'human' THEN 1.3 WHEN 'assistant' THEN 1.0 ELSE 0.8 END
            LIMIT :limit
            """,
            {"match": match, **scope},
        )
        return [
            Hit(sid, seq, Author(author), Kind(kind), *rest, matched_all) for sid, seq, author, kind, *rest in rows
        ]

    def recap(self, session: str) -> Recap:
        """One session in brief. `session` is a session id or any prefix that picks out a single session."""
        session_id = self._one(session)
        title, ended_at = self._db.execute("SELECT title, ended_at FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        opening = self._db.execute(
            "SELECT text FROM turns WHERE session_id = ? AND author = 'human' ORDER BY seq LIMIT 1", (session_id,)
        ).fetchone()
        summary = (self.digest(session_id) or {}).get("summary") or None
        return Recap(session_id, title, ended_at, self._turn_count(session_id), summary, opening[0] if opening else None)

    def _one(self, session: str) -> str:
        matches = self._db.execute(
            "SELECT session_id FROM sessions WHERE substr(session_id, 1, length(:prefix)) = :prefix", {"prefix": session}
        ).fetchall()
        if not matches:
            raise LookupError(f"no archived session starts with {session!r}")
        if len(matches) > 1:
            raise LookupError(f"{len(matches)} archived sessions start with {session!r}; give more of the id")
        ((session_id,),) = matches
        return session_id

    def show(self, session: str, *, first: int = 0, last: int | None = None) -> list[Turn]:
        """One session's turns as archived, from `first` to `last` inclusive.

        `session` is a session id or any prefix of one that picks out a single session.
        """
        session_id = self._one(session)
        rows = self._db.execute(
            """
            SELECT seq, author, kind, text, uuid, timestamp FROM turns
            WHERE session_id = :session AND seq >= :first AND (:last IS NULL OR seq <= :last)
            ORDER BY seq
            """,
            {"session": session_id, "first": first, "last": last},
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
