import json
import os
import subprocess
import sys
import threading
from dataclasses import replace

import dream.cli
import dream.daily
from dream.archive import Archive
from dream.claims import Claim, ClaimType, Evidence, Provenance, Scope
from dream.cli import main
from dream.dreaming import GLOBAL, DreamReport, key
from dream.extract import Thread
from dream.lock import Busy, held
from dream.memory import MemoryStore
from dream.mirror import Mirror
from dream.reconcile import Add, Review
from support import assistant_text, human, put_session


def test_ingest_search_and_show_from_the_command_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-settings"))
    root = tmp_path / "projects"
    put_session(
        root,
        "7f3a9c2e-1b4d-4e6f-8a90-123456789abc",
        [
            human("should refunds bypass the ledger", 0),
            assistant_text("No: every refund must post to the ledger first.", 1),
            {"type": "custom-title", "customTitle": "Refund path"},
        ],
    )
    db = str(tmp_path / "archive.db")

    assert main(["ingest", "--root", str(root), "--db", db]) == 0
    assert "1 added" in capsys.readouterr().out

    assert main(["search", "--db", db, "--all-projects", "must", "post"]) == 0
    listing = capsys.readouterr().out
    assert "7f3a9c2e#1" in listing
    assert "Refund path" in listing
    assert "every refund must post to the ledger first" in listing

    assert main(["show", "--db", db, "7f3a9c2e", "--first", "0", "--last", "0"]) == 0
    assert "should refunds bypass the ledger" in capsys.readouterr().out

    assert main(["show", "--db", db, "deadbeef"]) == 1
    assert "no archived session" in capsys.readouterr().err


def test_a_session_is_shown_in_brief_once_the_dream_has_read_it(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-settings"))
    root = tmp_path / "projects"
    session_id = "7f3a9c2e-1b4d-4e6f-8a90-123456789abc"
    put_session(
        root,
        session_id,
        [
            human("should refunds bypass the ledger", 0),
            assistant_text("No: every refund must post to the ledger first.", 1),
            {"type": "custom-title", "customTitle": "Refund path"},
        ],
    )
    db = tmp_path / "archive.db"
    main(["ingest", "--root", str(root), "--db", str(db)])
    capsys.readouterr()

    assert main(["show", "--db", str(db), "7f3a9c2e", "--summary"]) == 0
    unread = capsys.readouterr().out
    assert unread == (
        "7f3a9c2e  2026-10-01  Refund path  2 turns\n"
        "The dream has not read this session as it now stands, so it has no summary yet.\n"
        "It opened with: should refunds bypass the ledger\n"
    )

    Archive(db).keep_digest(session_id, {"summary": "Agreed that every refund posts to the ledger first."})

    assert main(["show", "--db", str(db), "7f3a9c2e", "--summary"]) == 0
    assert capsys.readouterr().out == (
        "7f3a9c2e  2026-10-01  Refund path  2 turns\n"
        "Agreed that every refund posts to the ledger first.\n"
    )


def test_search_says_in_brief_what_each_session_it_found_was(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-settings"))
    root = tmp_path / "projects"
    read, unread = "7f3a9c2e-1b4d-4e6f-8a90-123456789abc", "8a4b0d3f-2c5e-4f70-9ba1-23456789abcd"
    put_session(root, read, [human("should refunds bypass the ledger", 0), assistant_text("No, the ledger comes first.", 1)])
    put_session(root, unread, [human("where is the ledger schema kept", 0)])
    db = tmp_path / "archive.db"
    main(["ingest", "--root", str(root), "--db", str(db)])
    long = "Agreed that every refund posts to the ledger first. " + "Then went through the reconciliation job line by line. " * 8
    Archive(db).keep_digest(read, {"summary": long})
    capsys.readouterr()

    assert main(["search", "--db", str(db), "--all-projects", "ledger"]) == 0
    hits, _, sessions = capsys.readouterr().out.partition("\nWhat these sessions were")

    assert "7f3a9c2e#0" in hits and "8a4b0d3f#0" in hits
    header, named, brief = sessions.splitlines()
    assert header == " (dream show SESSION --summary gives the whole of one):"
    assert named == "7f3a9c2e  2026-10-01  2 turns"
    assert brief.startswith("    Agreed that every refund posts to the ledger first. Then went through")
    # 300 characters end inside the fifth "reconciliation"; the cut falls back to the word before it.
    assert brief.endswith("line by line. Then went through the…")
    assert brief.count("reconciliation") == 4


def test_a_memory_that_a_search_brings_up_is_counted_as_looked_up(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-settings"))
    project = tmp_path / "shop"
    project.mkdir()
    memory = tmp_path / "memory"
    copy = MemoryStore(memory / key(str(project.resolve())))
    copy.folder.mkdir(parents=True)
    said = Claim(
        slot="package-manager",
        type=ClaimType.PREFERENCE,
        scope=Scope.PROJECT,
        statement="Use pnpm for JS projects.",
        why="",
        provenance=Provenance.HUMAN,
        evidence=Evidence("7f3a9c2e-1b4d-4e6f-8a90-123456789abc", 3, 4),
        said_at="2026-10-02T09:00:00Z",
    )
    copy.apply([Add(said)])
    everywhere = MemoryStore(memory / key(GLOBAL))
    everywhere.folder.mkdir(parents=True)
    everywhere.apply([Add(replace(said, slot="lockfile", statement="Commit the pnpm lockfile."))])
    scope = ["--project", str(project), "--memory", str(memory), "--db", str(tmp_path / "archive.db")]

    assert main(["search", *scope, "pnpm"]) == 0
    assert "memory      package-manager (words)  Use pnpm for JS projects." in capsys.readouterr().out
    assert main(["search", *scope, "yarn"]) == 0

    assert (copy.reads("package-manager"), everywhere.reads("lockfile")) == (1, 1)

    # A session reading the memory's file in the dream's own copy is a look-up too.
    assert main(["note-read", str(copy.folder / "package-manager.md"), "--memory", str(memory), "--db", str(tmp_path / "archive.db")]) == 0
    assert copy.reads("package-manager") == 2

    # So is reading it in Claude Code's own folder, which keeps a project's memories one level down.
    theirs = tmp_path / "claude" / "projects" / copy.folder.name / "memory" / "package-manager.md"
    theirs.parent.mkdir(parents=True)
    theirs.write_text((copy.folder / "package-manager.md").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    assert main(["note-read", str(theirs), "--memory", str(memory), "--db", str(tmp_path / "archive.db")]) == 0
    assert copy.reads("package-manager") == 3

    # A copy the dream is in the middle of replacing is passed over: the search still answers.
    (copy.folder / ".remcycle" / "entries.json").write_text('{"package-manager": {"evid', encoding="utf-8", newline="\n")
    capsys.readouterr()
    assert main(["search", *scope, "pnpm"]) == 0
    answered = capsys.readouterr().out
    assert "package-manager" not in answered and "lockfile (words)" in answered
    assert main(["note-read", str(theirs), "--memory", str(memory), "--db", str(tmp_path / "archive.db")]) == 0


def test_the_queue_lists_each_question_once_while_the_dream_is_at_work_on_a_copy(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-settings"))
    memory = tmp_path / "memory"
    said = Claim("package-manager", ClaimType.PREFERENCE, Scope.PROJECT, "Use pnpm for JS projects.", "", Provenance.HUMAN,
                 Evidence("7f3a9c2e-1b4d-4e6f-8a90-123456789abc", 3, 4), "2026-10-02T09:00:00Z")
    mirror = Mirror(memory / "-work-shop")
    mirror.sync(None, "/work/shop")
    MemoryStore(mirror.stage()).apply([Add(said), Review("package-manager", "it looks dated: the repo moved to bun")])
    mirror.accept("dream: 1 sessions")
    mirror.stage()

    assert main(["queue", "--memory", str(memory), "--db", str(tmp_path / "archive.db")]) == 0
    assert capsys.readouterr().out.count("package-manager") == 1


def test_ingest_says_how_many_sessions_the_dream_has_not_read(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-settings"))
    root = tmp_path / "projects"
    read, unread = "7f3a9c2e-1b4d-4e6f-8a90-123456789abc", "8a4b0d3f-2c5e-4f70-9ba1-23456789abcd"
    for session_id in (read, unread):
        put_session(root, session_id, [human("use pnpm here", 0), assistant_text("Noted.", 1)])
    db = tmp_path / "archive.db"

    assert main(["ingest", "--root", str(root), "--db", str(db)]) == 0
    assert "2 not yet read by the dream" in capsys.readouterr().out

    Archive(db).record_dream(read)

    assert main(["ingest", "--root", str(root), "--db", str(db)]) == 0
    assert "1 not yet read by the dream" in capsys.readouterr().out


def test_a_thread_is_closed_from_the_command_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-settings"))
    project = tmp_path / "shop"
    project.mkdir()
    memory = tmp_path / "memory"
    copy = MemoryStore(memory / key(str(project.resolve())))
    copy.folder.mkdir(parents=True)
    copy.note_threads([Thread("ci-cache", "CI cache for pnpm is not set up.", True, 4)], "session-a", "2026-10-01T09:00:00Z")
    scope = ["--project", str(project), "--memory", str(memory), "--db", str(tmp_path / "archive.db")]

    assert main(["close", "--why=The cache step is in the workflow now.", "--session=7f3a9c2e", *scope, "--", "ci-cache"]) == 0
    assert "ci-cache" in capsys.readouterr().out
    assert copy.threads(now="2026-10-02T09:00:00Z") == {}

    assert main(["close", "ci-cache", *scope]) == 1
    assert "ci-cache" in capsys.readouterr().err

    assert main(["status", *scope]) == 0
    (closed,) = json.loads(capsys.readouterr().out)["closed_lately"]
    assert (closed["slot"], closed["by"], closed["why"]) == ("ci-cache", "session", "The cache step is in the workflow now.")

    assert main(["reopen", "ci-cache", *scope]) == 0
    assert "ci-cache" in capsys.readouterr().out
    assert list(copy.threads(now="2026-10-02T09:00:00Z")) == ["ci-cache"]


def test_purge_lists_what_an_exclusion_would_remove_and_removes_it_only_when_told_to(tmp_path, capsys, monkeypatch):
    settings = tmp_path / "settings" / "remcycle"
    settings.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "settings"))
    root = tmp_path / "projects"
    put_session(root, "7f3a9c2e-1b4d-4e6f-8a90-123456789abc", [human("the contract is confidential", 0)], cwd="/work/private-client")
    put_session(root, "1c2d3e4f-1b4d-4e6f-8a90-123456789abc", [human("always use pnpm here", 0)], cwd="/work/demo")
    db = str(tmp_path / "archive.db")
    assert main(["ingest", "--root", str(root), "--db", db]) == 0
    capsys.readouterr()
    (settings / "config.toml").write_text('exclude = ["/work/private-client"]\n', encoding="utf-8", newline="\n")

    assert main(["purge", "--db", db]) == 0
    listed = capsys.readouterr().out
    assert "7f3a9c2e" in listed and "/work/private-client" in listed and "1c2d3e4f" not in listed
    assert "--yes" in listed
    assert main(["search", "--db", db, "--all-projects", "confidential"]) == 0
    assert "7f3a9c2e#0" in capsys.readouterr().out

    assert main(["purge", "--db", db, "--yes"]) == 0
    assert "removed 1" in capsys.readouterr().out
    assert main(["search", "--db", db, "--all-projects", "confidential"]) == 0
    assert "7f3a9c2e" not in capsys.readouterr().out

    assert main(["purge", "--db", db]) == 0
    assert "no archived session" in capsys.readouterr().out


def test_commands_that_change_memory_take_turns_and_say_so_when_they_cannot(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-settings"))
    monkeypatch.setattr(dream.cli, "_TURN_WAIT", 0.2)
    project = tmp_path / "shop"
    project.mkdir()
    memory = tmp_path / "memory"
    copy = MemoryStore(memory / key(str(project.resolve())))
    copy.folder.mkdir(parents=True)
    copy.note_threads([Thread("ci-cache", "CI cache for pnpm is not set up.", True, 4)], "session-a", "2026-10-01T09:00:00Z")
    scope = ["--project", str(project), "--memory", str(memory), "--db", str(tmp_path / "archive.db")]

    with held(memory / ".lock"):  # as another command would, while it changes memory
        assert main(["close", "ci-cache", *scope]) == 1
        assert "another dream command is changing memory" in capsys.readouterr().err
        assert list(copy.threads(now="2026-10-02T09:00:00Z")) == ["ci-cache"]
        assert main(["status", *scope]) == 0
        capsys.readouterr()

    assert main(["close", "ci-cache", *scope]) == 0
    assert copy.threads(now="2026-10-02T09:00:00Z") == {}


def test_publish_shows_what_it_would_write_and_writes_only_with_yes(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-settings"))
    project = tmp_path / "shop"
    project.mkdir()
    name = key(str(project.resolve()))
    live = tmp_path / "projects" / name / "memory"
    live.mkdir(parents=True)
    (live / "deploy-target.md").write_text("---\nname: deploy-target\ndescription: staging first\n---\n\nStaging first.\n", encoding="utf-8", newline="\n")
    (live / "MEMORY.md").write_text("- [Deploy target](deploy-target.md) — staging first\n", encoding="utf-8", newline="\n")
    memory = tmp_path / "data" / "memory"
    Mirror(memory / name).sync(live, str(project.resolve()))
    (memory / name / "ci-runner.md").write_text("---\nname: ci-runner\ndescription: self-hosted\n---\n\nCI is self-hosted.\n", encoding="utf-8", newline="\n")
    scope = ["--project", str(project), "--memory", str(memory), "--root", str(tmp_path / "projects"), "--db", str(tmp_path / "a.db")]

    assert main(["publish", *scope]) == 0
    listed = capsys.readouterr().out
    assert "add 1" in listed and "ci-runner.md" in listed
    assert "nothing was written" in listed and "--yes" in listed
    assert not (live / "ci-runner.md").exists()

    assert main(["publish", "--yes", *scope]) == 0
    written = capsys.readouterr().out
    assert "written" in written and str(tmp_path / "data" / "backups") in written
    assert (live / "ci-runner.md").exists()

    assert main(["publish", *scope]) == 0
    assert "already matches" in capsys.readouterr().out


def test_a_session_starts_the_daily_dream_in_the_background_once_a_day_when_there_is_something_new(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    started = []
    monkeypatch.setattr(dream.daily, "start", lambda argv, log: started.append(argv) or 999_999)
    clock = {"now": "2026-10-01T08:00:00+00:00"}
    monkeypatch.setattr(dream.cli, "_now", lambda: clock["now"])
    root, db = tmp_path / "projects", str(tmp_path / "archive.db")
    scope = ["--db", db, "--root", str(root)]

    def session(session_id, at):
        put_session(root, session_id, [human("use bun here", 0, timestamp=at)], cwd="/work/shop")
        assert main(["ingest", *scope]) == 0

    def daily(at):
        clock["now"] = at
        assert main(["daily", *scope]) == 0
        return capsys.readouterr().out

    session("s-before", "2026-09-30T09:00:00.000Z")
    capsys.readouterr()
    assert "nothing new" in daily("2026-10-01T08:00:00+00:00")

    session("s-after", "2026-10-01T10:00:00.000Z")
    assert "started in the background: 1 session to read" in daily("2026-10-01T12:00:00+00:00")
    assert "not due" in daily("2026-10-01T13:00:00+00:00")

    session("s-next", "2026-10-01T15:00:00.000Z")
    assert "started in the background" in daily("2026-10-02T10:00:00+00:00")
    assert started == [[sys.executable, "-P", "-m", "dream.cli", "daily", "--background", *scope]] * 2


def test_a_daily_dream_started_before_a_history_is_chosen_says_how_many_earlier_sessions_wait(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(dream.daily, "start", lambda argv, log: 999_999)
    clock = {"now": "2026-10-01T08:00:00+00:00"}
    monkeypatch.setattr(dream.cli, "_now", lambda: clock["now"])
    root, db = tmp_path / "projects", str(tmp_path / "archive.db")
    scope = ["--db", db, "--root", str(root)]
    choose = "choose how far back the daily dream reads, with /remcycle or `dream daily history`"

    def session(session_id, at):
        put_session(root, session_id, [human("use bun here", 0, timestamp=at)], cwd="/work/shop")
        assert main(["ingest", *scope]) == 0

    def daily(at):
        clock["now"] = at
        capsys.readouterr()
        assert main(["daily", *scope]) == 0
        return capsys.readouterr().out

    session("s-old", "2026-09-28T09:00:00.000Z")
    assert daily("2026-10-01T08:00:00+00:00") == "nothing new for the daily dream to read\n"

    session("s-after", "2026-10-01T10:00:00.000Z")
    # A transcript in which nothing was said has no time it ended, and is not an earlier session.
    put_session(root, "s-untouched", [{"type": "custom-title", "customTitle": "Opened and left"}], cwd="/work/shop")
    assert main(["ingest", *scope]) == 0
    assert daily("2026-10-01T12:00:00+00:00") == (
        f"the daily dream started in the background: 1 session to read\n1 earlier session waits unread: {choose}\n"
    )

    session("s-older", "2026-09-20T09:00:00.000Z")
    assert daily("2026-10-02T12:00:00+00:00") == (
        f"the daily dream started in the background: 1 session to read\n2 earlier sessions wait unread: {choose}\n"
    )

    assert main(["daily", "history", "new"]) == 0
    assert daily("2026-10-03T12:00:00+00:00") == "the daily dream started in the background: 1 session to read\n"


def test_the_daily_dream_is_changed_in_the_settings_file_and_does_nothing_while_it_is_off(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    started = []
    monkeypatch.setattr(dream.daily, "start", lambda argv, log: started.append(argv))
    file = tmp_path / "config" / "remcycle" / "config.toml"
    file.parent.mkdir(parents=True)
    file.write_text('# Leave the client out.\nexclude = ["~/work/client"]\n', encoding="utf-8", newline="\n")
    db = ["--db", str(tmp_path / "archive.db")]

    for change in (["off"], ["history", "week"], ["limit", "5"]):
        assert main(["daily", *change, *db]) == 0
    assert file.read_text(encoding="utf-8") == (
        '# Leave the client out.\nexclude = ["~/work/client"]\ndaily_dream = false\ndaily_history = "week"\ndaily_limit = 5\n'
    )
    capsys.readouterr()
    assert main(["daily", *db]) == 0
    assert "the daily dream is off" in capsys.readouterr().out
    assert started == []

    assert main(["daily", "on", *db]) == 0
    assert main(["daily", "limit", "none", *db]) == 0
    assert file.read_text(encoding="utf-8") == '# Leave the client out.\nexclude = ["~/work/client"]\ndaily_history = "week"\ndaily_dream = true\n'
    capsys.readouterr()

    assert main(["daily", "history", "month", *db]) == 2
    assert "usage: dream daily" in capsys.readouterr().err


def test_the_background_daily_dream_reads_from_where_the_chosen_history_begins_and_records_how_it_ended(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    file = tmp_path / "config" / "remcycle" / "config.toml"
    file.parent.mkdir(parents=True)
    file.write_text('daily_history = "week"\ndaily_limit = 5\n', encoding="utf-8", newline="\n")
    state = tmp_path / "data" / "remcycle" / "daily.json"
    dream.daily.save(state, dream.daily.State(began="2026-10-08T08:00:00+00:00", started="2026-10-08T12:00:00+00:00"))
    monkeypatch.setattr(dream.cli, "_now", lambda: "2026-10-08T12:05:00+00:00")
    asked, answers = [], iter([DreamReport(), DreamReport(failures=[("s-1", "the model gave no answer")])])

    def stand_in(archive, **options):
        asked.append((options["since"], options["limit"], options["publish"]))
        assert dream.daily.is_running(state, dream.daily.load(state), "2026-10-08T14:00:00+00:00")
        return next(answers)

    monkeypatch.setattr(dream.cli, "dream", stand_in)
    background = ["daily", "--background", "--db", str(tmp_path / "archive.db"), "--root", str(tmp_path / "projects")]

    assert main(background) == 0
    assert asked == [("2026-10-01T08:00:00+00:00", 5, False)]
    assert (dream.daily.load(state).finished, dream.daily.load(state).failed) == ("2026-10-08T12:05:00+00:00", None)

    assert main(background) == 1
    assert dream.daily.load(state).failed == "1 session could not be read, because: the model gave no answer"
    assert not dream.daily.is_running(state, dream.daily.load(state), "2026-10-08T14:00:00+00:00")

    monkeypatch.setattr(dream.daily, "LOCK_WAIT", 0.2)
    capsys.readouterr()
    with dream.daily.running(state):  # another daily dream, still going
        assert main(background) == 1
    assert "another daily dream is still running" in capsys.readouterr().err
    assert len(asked) == 2


def test_how_a_daily_dream_ended_is_recorded_only_when_no_session_is_part_way_through_reading_and_writing_its_state(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    state = tmp_path / "data" / "remcycle" / "daily.json"
    dream.daily.save(state, dream.daily.State(began="2026-10-08T08:00:00+00:00", started="2026-10-08T12:00:00+00:00"))
    monkeypatch.setattr(dream.cli, "dream", lambda archive, **options: DreamReport())
    background = ["daily", "--background", "--db", str(tmp_path / "archive.db"), "--root", str(tmp_path / "projects")]
    run = threading.Thread(target=main, args=(background,))

    with held(state.with_name("daily.json.lock")):  # as a starting session holds it, between reading the state and writing it
        run.start()
        run.join(timeout=1.0)
        assert run.is_alive() and dream.daily.load(state).finished is None
    run.join()

    assert dream.daily.load(state).finished is not None


def test_the_status_says_how_the_daily_dream_stands_and_how_much_each_history_would_read(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(dream.cli, "_now", lambda: "2026-10-08T08:00:00+00:00")
    root, db = tmp_path / "projects", str(tmp_path / "archive.db")
    for session_id, at in (("s-old", "2026-09-01T09"), ("s-week", "2026-10-03T09"), ("s-new", "2026-10-07T20")):
        put_session(root, session_id, [human("use bun here", 0, timestamp=f"{at}:00:00.000Z")], cwd="/work/shop")
    assert main(["ingest", "--root", str(root), "--db", db]) == 0
    state = tmp_path / "data" / "remcycle" / "daily.json"
    dream.daily.save(state, dream.daily.State(began="2026-10-07T08:00:00+00:00", started="2026-10-07T08:00:00+00:00"))
    capsys.readouterr()

    assert main(["status", "--db", db, "--project", str(tmp_path)]) == 0
    daily = json.loads(capsys.readouterr().out)["daily"]

    assert daily == {
        "on": True,
        "history": None,
        "limit": None,
        "waiting": {"new": 1, "week": 2, "all": 3},
        "started": "2026-10-07T08:00:00+00:00",
        "finished": None,
        "failed": None,
    }


def test_turning_the_daily_dream_off_works_when_the_settings_file_has_tables(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    file = tmp_path / "config" / "remcycle" / "config.toml"
    file.parent.mkdir(parents=True)
    file.write_text('model = "sonnet"\n\n[extra]\nnote = "x"\n', encoding="utf-8", newline="\n")
    db = ["--db", str(tmp_path / "archive.db")]

    assert main(["daily", "off", *db]) == 0
    capsys.readouterr()

    assert file.read_text(encoding="utf-8") == 'model = "sonnet"\ndaily_dream = false\n\n[extra]\nnote = "x"\n'
    assert main(["daily", *db]) == 0
    assert "the daily dream is off" in capsys.readouterr().out


def test_a_settings_file_the_change_cannot_be_made_in_is_reported_not_raised(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    file = tmp_path / "config" / "remcycle" / "config.toml"
    file.parent.mkdir(parents=True)
    file.write_text('"daily_dream" = true\n', encoding="utf-8", newline="\n")

    assert main(["daily", "off", "--db", str(tmp_path / "archive.db")]) == 2
    assert "config.toml" in capsys.readouterr().err
    assert file.read_text(encoding="utf-8") == '"daily_dream" = true\n'


def test_a_daily_dream_that_died_is_reported_and_one_still_running_is_left_alone(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(dream.cli, "_now", lambda: "2026-10-08T08:00:00+00:00")
    started = []
    monkeypatch.setattr(dream.daily, "start", lambda argv, log: started.append(argv) or 999_999)
    root, db = tmp_path / "projects", str(tmp_path / "archive.db")
    put_session(root, "s-new", [human("use bun here", 0, timestamp="2026-10-07T20:00:00.000Z")], cwd="/work/shop")
    assert main(["ingest", "--root", str(root), "--db", db]) == 0
    state = tmp_path / "data" / "remcycle" / "daily.json"
    yesterday, just_now = "2026-10-07T08:00:00+00:00", "2026-10-08T07:59:50+00:00"

    def said_to_have_failed():
        capsys.readouterr()
        assert main(["status", "--db", db, "--project", str(tmp_path)]) == 0
        return json.loads(capsys.readouterr().out)["daily"]["failed"]

    dream.daily.save(state, dream.daily.State(began=yesterday, started=yesterday, pid=999_999))
    with dream.daily.running(state):  # as the background run does, for as long as it lives
        capsys.readouterr()
        assert main(["daily", "--db", db, "--root", str(root)]) == 0
        assert "still running" in capsys.readouterr().out
        assert said_to_have_failed() is None
    assert started == []

    # A run started a moment ago has not taken its lock yet, and is known by its process until it has.
    dream.daily.save(state, dream.daily.State(began=yesterday, started=just_now, pid=os.getpid()))
    assert said_to_have_failed() is None

    # The run is long gone, and the system has given its process number to something else that is running.
    dream.daily.save(state, dream.daily.State(began=yesterday, started=yesterday, pid=os.getpid()))
    assert said_to_have_failed() == "it stopped before it finished"
    assert main(["daily", "--db", db, "--root", str(root)]) == 0
    assert "started in the background" in capsys.readouterr().out
    assert len(started) == 1


def test_the_model_reads_without_holding_the_turn_at_changing_memory_and_the_dream_after_takes_it(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    memory = tmp_path / "memory"
    seen = []

    def turn_is_free():
        try:
            with held(memory / ".lock", wait=0):
                return True
        except Busy:
            return False

    def reading(archive, **options):
        seen.append(("reading", turn_is_free()))
        return dream.dreaming.ReadAhead(failures=[("s-1", "the model gave no answer")], cost_usd=0.5)

    def dreaming(archive, **options):
        seen.append(("dreaming", turn_is_free(), options["ask"]))
        return DreamReport(sessions=2, cost_usd=0.25)

    monkeypatch.setattr(dream.cli, "read_ahead", reading)
    monkeypatch.setattr(dream.cli, "dream", dreaming)
    scope = ["--db", str(tmp_path / "archive.db"), "--root", str(tmp_path / "projects"), "--memory", str(memory)]
    scope += ["--reports", str(tmp_path / "reports")]

    assert main(["run", *scope]) == 1
    assert seen == [("reading", True), ("dreaming", False, False)]
    report = capsys.readouterr().out
    assert "2 sessions read, $0.75 of model use" in report
    assert "Could not read s-1: the model gave no answer" in report


def test_what_a_command_prints_is_utf8_wherever_it_is_sent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-settings"))
    root, db = tmp_path / "projects", str(tmp_path / "archive.db")
    said = "naïve café → 日本語 🙂"
    put_session(root, "7f3a9c2e-1b4d-4e6f-8a90-123456789abc", [human(said, 0)])
    assert main(["ingest", "--root", str(root), "--db", db]) == 0

    shown = subprocess.run([sys.executable, "-m", "dream.cli", "show", "--db", db, "7f3a9c2e"], capture_output=True, check=True)

    assert said in shown.stdout.decode("utf-8")
