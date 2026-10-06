import json
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


def test_a_release_is_one_version_everywhere_it_is_written():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = next(package["version"] for package in lock["package"] if package["name"] == "remcycle")
    mod = json.loads((ROOT / "mod/.claude-plugin/plugin.json").read_text())["version"]
    listing = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())["plugins"][0]["source"]

    assert {"uv.lock": locked, "mod/.claude-plugin/plugin.json": mod, ".claude-plugin/marketplace.json": listing["ref"]} == {
        "uv.lock": _released(),
        "mod/.claude-plugin/plugin.json": _released(),
        ".claude-plugin/marketplace.json": f"v{_released()}",
    }
