"""Read a Claude Code session transcript into the turns worth keeping verbatim."""

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_SUMMARY = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
_RESULT = re.compile(r"<result>(.*?)</result>", re.DOTALL)
_TASK_NOTIFICATION = {"task-notification", "task_notification"}
_AGENT_TOOLS = {"Agent", "Task"}

# The input field that says what a tool call acted on, most specific first.
_TARGET_KEYS = ("file_path", "path", "notebook_path", "command", "pattern", "query", "url", "description", "skill")
_TARGET_LIMIT = 200


class Author(StrEnum):
    HUMAN = "human"
    ASSISTANT = "assistant"
    AUTOMATION = "automation"
    PEER = "peer"
    SUBAGENT = "subagent"
    UNKNOWN = "unknown"


class Kind(StrEnum):
    PROMPT = "prompt"
    REPLY = "reply"
    TOOL = "tool"
    REPORT = "report"


@dataclass(frozen=True)
class Turn:
    seq: int
    author: Author
    kind: Kind
    text: str
    uuid: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True)
class Session:
    session_id: str
    turns: tuple[Turn, ...]
    title: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    entrypoint: str | None = None
    claude_code_version: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    unreadable_rows: int = 0


def parse_transcript(path: Path) -> Session:
    turns: list[Turn] = []
    # Later rows win: a session that moved folders belongs where it ended up.
    latest: dict[str, str] = {}
    agent_calls: set[str] = set()
    unreadable = 0

    def keep(row: dict, author: Author, kind: Kind, text: str) -> None:
        if text:
            turns.append(Turn(len(turns), author, kind, text, row.get("uuid"), row.get("timestamp")))

    with path.open(encoding="utf-8") as lines:
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                unreadable += 1
                continue
            for field in ("sessionId", "customTitle", "aiTitle", "cwd", "gitBranch", "entrypoint", "version"):
                if row.get(field):
                    latest[field] = row[field]

            content = (row.get("message") or {}).get("content")
            if row.get("type") == "user":
                if report := _background_report(row, content) or _foreground_report(row, content, agent_calls):
                    keep(row, Author.SUBAGENT, Kind.REPORT, report)
                elif author := _prompt_author(row):
                    keep(row, author, Kind.PROMPT, _prompt_text(content))
            elif row.get("type") == "assistant":
                for block in content or ():
                    if block.get("type") == "text":
                        keep(row, Author.ASSISTANT, Kind.REPLY, block["text"].strip())
                    elif block.get("type") == "tool_use":
                        keep(row, Author.ASSISTANT, Kind.TOOL, _tool_call(block))
                        if block.get("name") in _AGENT_TOOLS:
                            agent_calls.add(block.get("id"))

    return Session(
        session_id=latest.get("sessionId", path.stem),
        turns=tuple(turns),
        title=latest.get("customTitle") or latest.get("aiTitle"),
        cwd=latest.get("cwd"),
        git_branch=latest.get("gitBranch"),
        entrypoint=latest.get("entrypoint"),
        claude_code_version=latest.get("version"),
        started_at=turns[0].timestamp if turns else None,
        ended_at=turns[-1].timestamp if turns else None,
        unreadable_rows=unreadable,
    )


def _prompt_author(row: dict) -> Author | None:
    """Who wrote a user-role row, or None for rows the harness injected.

    Only rows the transcript positively marks as human get HUMAN; a row with no
    marking at all is UNKNOWN so it never carries a person's authority.
    """
    origins = _origins(row)
    if row.get("isCompactSummary") or origins & _TASK_NOTIFICATION:
        return None
    if "peer" in origins:
        return Author.PEER
    if row.get("isMeta"):
        return None
    if "human" in origins:
        return Author.HUMAN
    if "sdk" in origins:
        return Author.AUTOMATION
    return Author.UNKNOWN


def _origins(row: dict) -> set[str | None]:
    return {(row.get("origin") or {}).get("kind"), row.get("turnOrigin")}


def _background_report(row: dict, content: str | list[dict]) -> str:
    """A background subagent's final report, carried by the task notification that announces it."""
    if not _origins(row) & _TASK_NOTIFICATION:
        return ""
    text = _text_of(content)
    result = _RESULT.search(text)
    if not result:
        return ""
    summary = _SUMMARY.search(text)
    return "\n\n".join(part.strip() for part in (summary and summary[1], result[1]) if part)


def _foreground_report(row: dict, content: str | list[dict], agent_calls: set[str]) -> str:
    """A subagent's final report when the session waited for it: the result of its Agent call."""
    if not isinstance(content, list):
        return ""
    answered = {block.get("tool_use_id") for block in content if block.get("type") == "tool_result"}
    result = row.get("toolUseResult")
    if not answered & agent_calls or not isinstance(result, dict) or result.get("status") != "completed":
        return ""
    return _text_of(result.get("content") or []).strip()


def _prompt_text(content: str | list[dict]) -> str:
    if isinstance(content, list) and any(block.get("type") == "tool_result" for block in content):
        return ""
    return _REMINDER.sub("", _text_of(content)).strip()


def _text_of(content: str | list[dict]) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(block["text"] for block in content if block.get("type") == "text")


def _tool_call(block: dict) -> str:
    tool_input = block.get("input") or {}
    for key in _TARGET_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            target = value.strip().splitlines()[0][:_TARGET_LIMIT]
            return f"{block['name']} {target}"
    return block["name"]
