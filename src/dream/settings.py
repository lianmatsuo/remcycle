"""Where remcycle finds Claude Code's transcripts and keeps its own files."""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APP = "remcycle"


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


def load_settings(env: Mapping[str, str], home: Path) -> Settings:
    """Settings for one user.

    Claude Code's files are found through CLAUDE_CONFIG_DIR, as Claude Code finds them;
    remcycle's own follow the XDG variables.
    """
    data = _folder(env, "XDG_DATA_HOME", home / ".local" / "share") / APP
    file = _folder(env, "XDG_CONFIG_HOME", home / ".config") / APP / "config.toml"
    chosen = _read(file)
    exclude = chosen.get("exclude", [])
    if not isinstance(exclude, list) or not all(isinstance(entry, str) for entry in exclude):
        raise ValueError(f"{file}: exclude must be a list of project folders")
    model = chosen.get("model", "sonnet")
    if not isinstance(model, str):
        raise ValueError(f"{file}: model must be a model name")  # noqa: TRY004 the command line reports every settings fault as a ValueError
    return Settings(
        transcripts=_folder(env, "CLAUDE_CONFIG_DIR", home / ".claude") / "projects",
        archive=data / "archive.db",
        exclude=tuple(str(home / entry[2:]) if entry.startswith("~/") else entry for entry in exclude),
        memory=data / "memory",
        reports=data / "reports",
        model=model,
        effort=chosen.get("effort"),
    )


def _read(file: Path) -> dict:
    if not file.exists():
        return {}
    try:
        return tomllib.loads(file.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{file} is not valid TOML: {e}") from e


def _folder(env: Mapping[str, str], variable: str, default: Path) -> Path:
    return Path(env[variable]) if env.get(variable) else default
