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


def load_settings(env: Mapping[str, str], home: Path) -> Settings:
    """Settings for one user.

    Claude Code's files are found through CLAUDE_CONFIG_DIR, as Claude Code finds them;
    remcycle's own follow the XDG variables.
    """
    return Settings(
        transcripts=_folder(env, "CLAUDE_CONFIG_DIR", home / ".claude") / "projects",
        archive=_folder(env, "XDG_DATA_HOME", home / ".local" / "share") / APP / "archive.db",
        exclude=_excluded(_folder(env, "XDG_CONFIG_HOME", home / ".config") / APP / "config.toml", home),
    )


def _excluded(file: Path, home: Path) -> tuple[str, ...]:
    if not file.exists():
        return ()
    try:
        exclude = tomllib.loads(file.read_text()).get("exclude", [])
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{file} is not valid TOML: {e}") from e
    if not isinstance(exclude, list) or not all(isinstance(entry, str) for entry in exclude):
        raise ValueError(f"{file}: exclude must be a list of project folders")
    return tuple(str(home / entry[2:]) if entry.startswith("~/") else entry for entry in exclude)


def _folder(env: Mapping[str, str], variable: str, default: Path) -> Path:
    return Path(env[variable]) if env.get(variable) else default
