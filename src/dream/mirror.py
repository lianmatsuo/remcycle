"""remcycle's own copy of one memory folder, where the dream works.

Live memory is the folder Claude Code loads. The dream never edits it in place:
it takes in what sessions wrote there, stages its changes in a throwaway copy,
and writes back only when asked to publish. The copy is a git repository, so
every accepted change is in its history.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from dream.memory import INDEX, SIDE, MemoryStore


class LiveChanged(Exception):
    """Live memory changed while the dream was running, so nothing was published."""


class Mirror:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.staging = folder.with_name(folder.name + ".staging")
        self._synced_file = folder / SIDE / "synced.json"

    def sync(self, live: Path | None, project: str | None = None) -> None:
        """Take in what sessions wrote to live memory since the last sync. Their version wins.

        A memory a session edited or deleted loses what remcycle recorded about it,
        because that record described text which is no longer there.
        """
        (self.folder / SIDE).mkdir(parents=True, exist_ok=True)
        if not (self.folder / ".git").exists():
            self._git("init", "-q")
        synced = self._synced()
        store = MemoryStore(self.folder)
        current = {path.name: path for path in live.glob("*.md")} if live and live.is_dir() else {}
        for name, path in current.items():
            if synced.get(name) != _digest(path):
                mine = self.folder / name
                if name == INDEX:
                    store.absorb(mine.read_text() if mine.exists() else "", path.read_text())
                else:
                    shutil.copyfile(path, mine)
                    store.forget(path.stem)
                synced[name] = _digest(path)
        for name in set(synced) - set(current):
            (self.folder / name).unlink(missing_ok=True)
            store.forget(Path(name).stem)
            del synced[name]
        store.ensure_indexed()
        if project:
            (self.folder / SIDE / "project.json").write_text(json.dumps({"project": project}) + "\n")
        self._save_synced(synced)
        self._commit("sync from live memory")

    def stage(self) -> Path:
        """A fresh copy to change. The mirror itself stays as it is until `accept`."""
        if self.staging.exists():
            shutil.rmtree(self.staging)
        shutil.copytree(self.folder, self.staging, ignore=shutil.ignore_patterns(".git"))
        return self.staging

    def accept(self, message: str) -> None:
        """Make the staged copy the mirror's content and record it in history."""
        for item in self.folder.iterdir():
            if item.name == ".git":
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        shutil.copytree(self.staging, self.folder, dirs_exist_ok=True)
        self._commit(message)

    def publish(self, live: Path) -> None:
        """Write the mirror's memory files to live memory, unless live changed since the sync."""
        current = {path.name: _digest(path) for path in live.glob("*.md")} if live.is_dir() else {}
        if current != self._synced():
            raise LiveChanged("live memory changed during the dream, so nothing was published")
        live.mkdir(parents=True, exist_ok=True)
        ours = {path.name: path for path in self.folder.glob("*.md")}
        for name, path in ours.items():
            shutil.copyfile(path, live / name)
        for name in set(current) - set(ours):
            (live / name).unlink()
        self._save_synced({name: _digest(path) for name, path in ours.items()})
        self._commit("published to live memory")

    def last(self, kind: str) -> str | None:
        """When the mirror last recorded a change of this kind, such as `dream`. None if it never has."""
        if not (self.folder / ".git").exists():
            return None
        return self._git("log", "-1", "--format=%cI", f"--grep=^{kind}") or None

    def _synced(self) -> dict[str, str]:
        """What each live file held when it was last taken in or written, by content hash."""
        return json.loads(self._synced_file.read_text()) if self._synced_file.exists() else {}

    def _save_synced(self, synced: dict[str, str]) -> None:
        self._synced_file.parent.mkdir(exist_ok=True)
        self._synced_file.write_text(json.dumps(synced, indent=2, sort_keys=True) + "\n")

    def _commit(self, message: str) -> None:
        self._git("add", "-A")
        if self._git("status", "--porcelain"):
            # The mirror is remcycle's own record, written unattended: it commits as itself
            # and never waits on the person's signing key.
            self._git(
                "-c", "user.name=remcycle", "-c", "user.email=remcycle@localhost", "-c", "commit.gpgsign=false",
                "commit", "-q", "-m", message,
            )  # fmt: skip

    def _git(self, *args: str) -> str:
        try:
            done = subprocess.run(
                ["git", "-C", str(self.folder), *args], check=True, capture_output=True, text=True
            )
        except (OSError, subprocess.CalledProcessError) as e:
            detail = getattr(e, "stderr", "") or str(e)
            raise RuntimeError(f"git {args[0]} failed in {self.folder}: {detail.strip()}") from e
        return done.stdout.strip()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
