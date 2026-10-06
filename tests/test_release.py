import tomllib
from pathlib import Path

import pytest

from dream.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _released() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_the_command_says_which_release_it_is(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-settings"))

    with pytest.raises(SystemExit) as stopped:
        main(["--version"])

    assert stopped.value.code == 0
    assert capsys.readouterr().out == f"dream {_released()}\n"

