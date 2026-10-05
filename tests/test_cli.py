import fcntl
import json

import dream.cli
from dream.cli import main
from dream.dreaming import key
from dream.extract import Thread
from dream.memory import MemoryStore
from dream.mirror import Mirror
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
