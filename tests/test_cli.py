import fcntl
import json
from dataclasses import replace

import dream.cli
from dream.archive import Archive
from dream.claims import Claim, ClaimType, Evidence, Provenance, Scope
from dream.cli import main
from dream.dreaming import GLOBAL, key
from dream.extract import Thread
from dream.memory import MemoryStore
from dream.mirror import Mirror
from dream.reconcile import Add
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
    theirs.write_text((copy.folder / "package-manager.md").read_text())
    assert main(["note-read", str(theirs), "--memory", str(memory), "--db", str(tmp_path / "archive.db")]) == 0
    assert copy.reads("package-manager") == 3

    # A copy the dream is in the middle of replacing is passed over: the search still answers.
    (copy.folder / ".remcycle" / "entries.json").write_text('{"package-manager": {"evid')
    capsys.readouterr()
    assert main(["search", *scope, "pnpm"]) == 0
    answered = capsys.readouterr().out
    assert "package-manager" not in answered and "lockfile (words)" in answered
    assert main(["note-read", str(theirs), "--memory", str(memory), "--db", str(tmp_path / "archive.db")]) == 0


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
    (settings / "config.toml").write_text('exclude = ["/work/private-client"]\n')

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

    with (memory / ".lock").open("w") as other_command:
        fcntl.flock(other_command, fcntl.LOCK_EX)

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
    (live / "deploy-target.md").write_text("---\nname: deploy-target\ndescription: staging first\n---\n\nStaging first.\n")
    (live / "MEMORY.md").write_text("- [Deploy target](deploy-target.md) — staging first\n")
    memory = tmp_path / "data" / "memory"
    Mirror(memory / name).sync(live, str(project.resolve()))
    (memory / name / "ci-runner.md").write_text("---\nname: ci-runner\ndescription: self-hosted\n---\n\nCI is self-hosted.\n")
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
