from pathlib import Path

import pytest

from dream.settings import load_settings


def test_everything_lives_in_the_usual_places_by_default(tmp_path):
    settings = load_settings({}, tmp_path)

    assert settings.transcripts == tmp_path / ".claude" / "projects"
    assert settings.archive == tmp_path / ".local" / "share" / "remcycle" / "archive.db"
    assert settings.exclude == ()


def test_locations_follow_the_environment(tmp_path):
    env = {"CLAUDE_CONFIG_DIR": "/elsewhere/claude", "XDG_DATA_HOME": "/elsewhere/data"}

    settings = load_settings(env, tmp_path)

    assert settings.transcripts == Path("/elsewhere/claude/projects")
    assert settings.archive == Path("/elsewhere/data/remcycle/archive.db")


def test_excluded_projects_come_from_the_settings_file(tmp_path):
    file = tmp_path / "xdg" / "remcycle" / "config.toml"
    file.parent.mkdir(parents=True)
    file.write_text('exclude = ["~/work/private-client", "/srv/other"]\n')

    settings = load_settings({"XDG_CONFIG_HOME": str(tmp_path / "xdg")}, tmp_path)

    assert settings.exclude == (str(tmp_path / "work" / "private-client"), "/srv/other")


@pytest.mark.parametrize("contents", ["exclude = [", 'exclude = "~/work/private-client"\n'])
def test_a_settings_file_that_cannot_be_used_is_an_error_naming_the_file(tmp_path, contents):
    file = tmp_path / ".config" / "remcycle" / "config.toml"
    file.parent.mkdir(parents=True)
    file.write_text(contents)

    with pytest.raises(ValueError, match="config.toml"):
        load_settings({}, tmp_path)
