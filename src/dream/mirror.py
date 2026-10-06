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

from dream import disk
from dream.memory import INDEX, SIDE, STAGED, MemoryStore


class LiveChanged(Exception):
    """Live memory changed while the dream was running, so nothing was published."""


class Mirror:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.staging = folder.with_name(folder.name + STAGED)
        self._retired = folder.with_name(folder.name + ".retired")
        self._synced_file = folder / SIDE / "synced.json"

    def sync(self, live: Path | None, project: str | None = None) -> None:
        """Take in what sessions wrote to live memory since the last sync. Their version wins.

        A memory a session edited or deleted loses what remcycle recorded about it,
        because that record described text which is no longer there.
        """
        self._finish_accept()
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
                    store.absorb(mine.read_text(encoding="utf-8") if mine.exists() else "", path.read_text(encoding="utf-8"))
                else:
                    shutil.copyfile(path, mine)
                    store.forget(path.stem)
                synced[name] = _digest(path)
        for name in set(synced) - set(current):
            disk.remove(self.folder / name)
            store.forget(Path(name).stem)
            del synced[name]
        store.ensure_indexed()
        if project:
            (self.folder / SIDE / "project.json").write_text(json.dumps({"project": project}) + "\n", encoding="utf-8", newline="\n")
        self._save_synced(synced)
        self._commit("sync from live memory")

    def stage(self) -> Path:
        """A fresh copy to change. The mirror itself stays as it is until `accept`."""
        self._finish_accept()
        if self.staging.exists():
            disk.clear(self.staging)
        # A writer that was killed leaves the file it was still writing. It is not part of the memory.
        shutil.copytree(self.folder, self.staging, ignore=shutil.ignore_patterns(".git", "*.new"))
        return self.staging

    def accept(self, message: str) -> None:
        """Make the staged copy the mirror's content and record it in history.

        The copy takes the mirror's place whole, by renaming folders, so a reader finds the old
        content or the new and never part of one. An accept that is cut short is finished by the
        next command that works on the mirror.

        Where the system refuses to move the folders, the mirror is put back as it was before the
        refusal is raised, so that what is done to it afterwards is not lost to a late accept.
        """
        disk.rename(self.folder / ".git", self.staging / ".git")
        try:
            self._finish_accept()
        except PermissionError:
            if not self.folder.exists():
                disk.rename(self._retired, self.folder)
            disk.rename(self.staging / ".git", self.folder / ".git")
            raise
        self._commit(message)

    def _finish_accept(self) -> None:
        """Put the staged copy in the mirror's place, if an accept got as far as giving it the history."""
        if (self.staging / ".git").exists():
            if self.folder.exists():
                if self._retired.exists():
                    disk.clear(self._retired)
                disk.rename(self.folder, self._retired)
            disk.rename(self.staging, self.folder)
        if self._retired.exists():
            disk.clear(self._retired)

    def publish(self, live: Path, backup: Path | None = None) -> None:
        """Write the mirror's memory files to live memory, unless live changed since the sync.

        With `backup`, the files live held are copied there first.
        """
        current = {path.name: _digest(path) for path in live.glob("*.md")} if live.is_dir() else {}
        if current != self._synced():
            raise LiveChanged("live memory changed during the dream, so nothing was published")
        if backup and current:
            backup.mkdir(parents=True, exist_ok=True)
            for name in current:
                shutil.copyfile(live / name, backup / name)
        live.mkdir(parents=True, exist_ok=True)
        ours = {path.name: path for path in self.folder.glob("*.md")}
        for name, path in ours.items():
            shutil.copyfile(path, live / name)
        for name in set(current) - set(ours):
            disk.remove(live / name)
        self._save_synced({name: _digest(path) for name, path in ours.items()})
        self._commit("published to live memory")

    def last(self, kind: str) -> str | None:
        """When the mirror last recorded a change of this kind, such as `dream`. None if it never has."""
        if not (self.folder / ".git").exists():
            return None
        return self._git("log", "-1", "--format=%cI", f"--grep=^{kind}") or None

    def _synced(self) -> dict[str, str]:
        """What each live file held when it was last taken in or written, by content hash."""
        return json.loads(self._synced_file.read_text(encoding="utf-8")) if self._synced_file.exists() else {}

    def _save_synced(self, synced: dict[str, str]) -> None:
        self._synced_file.parent.mkdir(exist_ok=True)
        self._synced_file.write_text(json.dumps(synced, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

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
        # Git for Windows keeps to paths of 260 characters unless told otherwise, and the copy's
        # folder carries the project's whole path in its name. The files are recorded with the
        # line ends they have, whatever the person's own git would make of them.
        command = ["git", "-C", str(self.folder), "-c", "core.longpaths=true", "-c", "core.autocrlf=false", *args]
        try:
            done = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
        except (OSError, subprocess.CalledProcessError) as e:
            detail = getattr(e, "stderr", "") or str(e)
            raise RuntimeError(f"git {args[0]} failed in {self.folder}: {detail.strip()}") from e
        return done.stdout.strip()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
