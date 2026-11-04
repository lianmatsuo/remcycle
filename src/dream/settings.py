"""Where remcycle finds Claude Code's transcripts and keeps its own files."""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dream import disk

APP = "remcycle"
HISTORIES = ("new", "week", "all")
"""How much of the time before the daily dream began it reads: none, the week before, or everything."""


@dataclass(frozen=True)
class Settings:
    transcripts: Path
    archive: Path
    exclude: tuple[str, ...]
    """Projects whose sessions are never archived."""
    memory: Path
    """Where the dream keeps its own copy of each project's memory."""
    reports: Path
    model: str
    """The model the dream asks Claude Code to use for extraction."""
    effort: str | None
    """How hard the model should think (`low`, `medium`, `high`). None leaves it to Claude Code."""
    daily: bool = True
    """Whether a session starts the daily dream when one is due."""
    daily_history: str | None = None
    """One of HISTORIES, once the person has chosen. Until then the daily dream reads only new sessions."""
    daily_limit: int | None = None
    """The most sessions one daily dream reads. None reads every one that is due."""
    model_config_dir: Path | None = None
    """The Claude Code folder the model steps sign in through.

    None leaves it to the environment they run in, so the daily dream signs in through the folder of
    the session that starts it. It moves nothing else: Claude Code's sessions are still found through
    CLAUDE_CONFIG_DIR.
    """


def load_settings(env: Mapping[str, str], home: Path) -> Settings:
    """Settings for one user.

    Claude Code's files are found through CLAUDE_CONFIG_DIR, as Claude Code finds them;
    remcycle's own follow the XDG variables.
    """
    file = settings_file(env, home)
    return _settings(_read(file), file, env, home)


def settings_file(env: Mapping[str, str], home: Path) -> Path:
    return _folder(env, "XDG_CONFIG_HOME", home / ".config") / APP / "config.toml"


def put_setting(env: Mapping[str, str], home: Path, name: str, value: str | None) -> None:
    """Set one top-level setting to a TOML value, or take it out with None, leaving every other line as it was.

    The new file is checked before it replaces the old one, so a value the settings would refuse is never written.
    """
    file = settings_file(env, home)
    lines = file.read_text(encoding="utf-8").splitlines(keepends=True) if file.exists() else []
    own = re.compile(rf"^\s*{re.escape(name)}\s*=")
    kept = [line for line in lines if not own.match(line)]
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    if value is not None:
        # A top-level setting has to come before the first table, or TOML reads it as part of that table.
        at = next((n for n, line in enumerate(kept) if line.lstrip().startswith("[")), len(kept))
        while at > 0 and kept[at - 1].strip() == "":
            at -= 1
        kept.insert(at, f"{name} = {value}\n")
    text = "".join(kept)
    try:
        chosen = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{file} cannot take this change, as it would not be valid TOML: {e}") from e
    _settings(chosen, file, env, home)
    if chosen.get(name) != (None if value is None else tomllib.loads(f"v = {value}")["v"]):
        raise ValueError(f"{file}: {name} could not be set; change it there by hand")
    file.parent.mkdir(parents=True, exist_ok=True)
    disk.put(file, text)


def _settings(chosen: dict, file: Path, env: Mapping[str, str], home: Path) -> Settings:
    data = _folder(env, "XDG_DATA_HOME", home / ".local" / "share") / APP
    exclude = chosen.get("exclude", [])
    if not isinstance(exclude, list) or not all(isinstance(entry, str) for entry in exclude):
        raise ValueError(f"{file}: exclude must be a list of project folders")
    model = chosen.get("model", "sonnet")
    if not isinstance(model, str):
        raise ValueError(f"{file}: model must be a model name")  # noqa: TRY004 the command line reports every settings fault as a ValueError
    daily = chosen.get("daily_dream", True)
    if not isinstance(daily, bool):
        raise ValueError(f"{file}: daily_dream must be true or false")  # noqa: TRY004 the command line reports every settings fault as a ValueError
    history = chosen.get("daily_history")
    if history is not None and history not in HISTORIES:
        raise ValueError(f"{file}: daily_history must be one of {', '.join(HISTORIES)}")
    limit = chosen.get("daily_limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValueError(f"{file}: daily_limit must be a whole number of sessions, 1 or more")
    config_dir = chosen.get("model_config_dir")
    # A relative path would be read from whichever folder the dream runs in.
    if config_dir is not None and (not isinstance(config_dir, str) or not Path(expand_home(config_dir, home)).is_absolute()):
        raise ValueError(f"{file}: model_config_dir must be the full path of a Claude Code folder, or start with ~/")
    return Settings(
        transcripts=_folder(env, "CLAUDE_CONFIG_DIR", home / ".claude") / "projects",
        archive=data / "archive.db",
        exclude=tuple(expand_home(entry, home) for entry in exclude),
        memory=data / "memory",
        reports=data / "reports",
        model=model,
        effort=chosen.get("effort"),
        daily=daily,
        daily_history=history,
        daily_limit=limit,
        model_config_dir=None if config_dir is None else Path(expand_home(config_dir, home)),
    )


def expand_home(entry: str, home: Path) -> str:
    """A path as the settings file gives it, with a leading `~` read as the home folder."""
    return str(home / entry[2:]) if entry.startswith(("~/", "~\\")) else entry


def as_written(folder: Path, home: Path) -> str:
    """A folder as a person would write it in the settings file: from `~/` when it is in the home folder."""
    try:
        return f"~/{folder.relative_to(home).as_posix()}"
    except ValueError:
        return str(folder)


def is_claude_code_folder(folder: Path, home: Path) -> bool:
    """Whether Claude Code has used `folder` as its folder: `~/.claude`, its own, or a folder that holds a `.claude.json`.

    Claude Code keeps the settings of its own folder beside it, in `~/.claude.json`, and those of a
    folder CLAUDE_CONFIG_DIR names inside that folder, once it has used it.
    """
    return (folder == home / ".claude" and folder.is_dir()) or (folder / ".claude.json").is_file()


def claude_code_folders(home: Path) -> list[Path]:
    """The Claude Code folders in the home folder: `~/.claude`, and every `~/.claude-*` folder Claude Code has used."""
    own = [home / ".claude"] if is_claude_code_folder(home / ".claude", home) else []
    return own + sorted(folder for folder in home.glob(".claude-*") if is_claude_code_folder(folder, home))


def _read(file: Path) -> dict:
    if not file.exists():
        return {}
    try:
        return tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{file} is not valid TOML: {e}") from e


def _folder(env: Mapping[str, str], variable: str, default: Path) -> Path:
    return Path(env[variable]) if env.get(variable) else default
