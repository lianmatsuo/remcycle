from dream.cli import main
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
