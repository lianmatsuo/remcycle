"""The gate: whether staged memory may replace live memory."""

import json
import re
from pathlib import Path

from dream.memory import INDEX, SIDE, MemoryStore

# Claude Code loads this much of MEMORY.md at session start and silently drops the rest.
INDEX_LINE_LIMIT = 200
INDEX_BYTE_LIMIT = 25_000

_LINK = re.compile(r"\]\(([^)]+\.md)\)")


def check(live: Path, staged: Path) -> list[str]:
    """Why `staged` must not replace `live`. An empty list means it may."""
    before, after = MemoryStore(live).entries(), MemoryStore(staged).entries()
    problems: list[str] = []

    gone = sorted(set(before) - set(after))
    if gone:
        noun = "entry" if len(gone) == 1 else "entries"
        problems.append(f"{len(gone)} {noun} disappeared without a record: {', '.join(gone)}")

    recorded = _records(staged)
    for slot in sorted(set(before) & set(after)):
        was, now = live / f"{slot}.md", staged / f"{slot}.md"
        untouched = recorded.get(slot) == _records(live).get(slot)
        if untouched and now.exists() and was.read_bytes() != now.read_bytes():
            problems.append(f"{slot}.md changed without a recorded operation")

    index = staged / INDEX
    if index.exists():
        text = index.read_text()
        lines = text.count("\n")
        if lines > INDEX_LINE_LIMIT:
            problems.append(f"{INDEX} has {lines} lines; Claude Code loads the first {INDEX_LINE_LIMIT}")
        if len(text.encode()) > INDEX_BYTE_LIMIT:
            problems.append(
                f"{INDEX} is {len(text.encode())} bytes; Claude Code loads the first {INDEX_BYTE_LIMIT}"
            )
        for target in _LINK.findall(text):
            if not (staged / target).exists():
                problems.append(f"{INDEX} points at {target}, which is not a memory in this folder")
    return problems


def _records(folder: Path) -> dict:
    file = folder / SIDE / "entries.json"
    return json.loads(file.read_text()) if file.exists() else {}
