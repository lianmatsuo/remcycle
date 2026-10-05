"""The gate: whether staged memory may replace live memory."""

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path

from dream.extract import Runner
from dream.memory import INDEX, INDEX_BYTE_LIMIT, INDEX_LINE_LIMIT, SIDE, TOPIC_PAGE, MemoryStore

_LINK = re.compile(r"\]\(([^)]+\.md)\)")


Judge = Callable[[str, Sequence[str]], Sequence[str]]
"""Given an index and questions, the file a reader of that index would open for each ("" for none)."""


def check(live: Path, staged: Path, judge: Judge | None = None) -> list[str]:
    """Why `staged` must not replace `live`. An empty list means it may.

    With a `judge`, the index is also tested on what it is for: every entry in use on
    both sides that has a probe question is asked of each index, and `staged` is refused
    if its index leads to the right memory less often than `live`'s does.
    """
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
    for listing in [index, *sorted(staged.glob(f"{TOPIC_PAGE}*.md"))]:
        for target in _LINK.findall(listing.read_text()) if listing.exists() else ():
            if not (staged / target).exists():
                problems.append(f"{listing.name} points at {target}, which is not a memory in this folder")
    if judge and not problems:
        problems += _index_still_leads(live, staged, judge)
    return problems


def _index_still_leads(live: Path, staged: Path, judge: Judge) -> list[str]:
    was, now = _index_text(live), _index_text(staged)
    then, probes = MemoryStore(live).probes(), MemoryStore(staged).probes()
    slots = sorted(set(then) & set(probes))
    if was == now or not slots:
        return []
    questions = [probes[slot] for slot in slots]

    def right(folder: Path, index: str) -> int:
        """How many questions lead to their entry, or to the topic page that lists it."""
        listed = {page.name: page.read_text() for page in folder.glob(f"{TOPIC_PAGE}*.md")}
        picked = judge(index, questions)
        return sum(
            Path(file).stem == slot or f"]({slot}.md)" in listed.get(Path(file).name, "")
            for file, slot in zip(picked, slots, strict=True)
        )

    before, after = right(live, was), right(staged, now)
    if after < before:
        return [f"the index leads to the right memory for {after} of {len(slots)} questions, down from {before}"]
    return []


def _index_text(folder: Path) -> str:
    index = folder / INDEX
    return index.read_text() if index.exists() else ""


JUDGE_SYSTEM = "You pick which file to open. You answer only through the structured output."
JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answers"],
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question", "file"],
                "properties": {"question": {"type": "integer"}, "file": {"type": "string"}},
            },
        }
    },
}
_JUDGE_PROMPT = """\
Below is the index of a folder of notes. It is all you can see of the folder.

For each numbered question, name the one file from the index you would open to answer it. \
If no line of the index suggests a file for a question, answer "none" for it.

## Index

{index}

## Questions

{questions}
"""


def judge_with(runner: Runner) -> Judge:
    """A judge that puts the index and the questions to the model in one call."""

    def judge(index: str, questions: Sequence[str]) -> list[str]:
        numbered = "\n".join(f"{n}. {question}" for n, question in enumerate(questions, 1))
        answers = runner(_JUDGE_PROMPT.format(index=index, questions=numbered)).data.get("answers", [])
        picked = {a.get("question"): str(a.get("file", "")) for a in answers if isinstance(a, dict)}
        return ["" if picked.get(n, "none") == "none" else picked[n] for n in range(1, len(questions) + 1)]

    return judge


def _records(folder: Path) -> dict:
    file = folder / SIDE / "entries.json"
    return json.loads(file.read_text()) if file.exists() else {}
