"""Builders for transcript rows shaped like the ones Claude Code writes."""

import json
from pathlib import Path

SESSION_ID = "11111111-aaaa-4bbb-8ccc-000000000001"


def write_transcript(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def put_session(root: Path, session_id: str, rows: list[dict], cwd: str = "/repo") -> Path:
    """Write rows as one session's transcript, laid out under root as Claude Code does."""
    stamped = [{**row, "sessionId": session_id, **({"cwd": cwd} if "cwd" in row else {})} for row in rows]
    return write_transcript(root / cwd.strip("/").replace("/", "-") / f"{session_id}.jsonl", stamped)


def _row(type_: str, n: int, **fields) -> dict:
    return {
        "type": type_,
        "uuid": f"uuid-{n}",
        "timestamp": f"2026-10-01T09:00:{n:02d}.000Z",
        "sessionId": SESSION_ID,
        "cwd": "/repo",
        **fields,
    }


def _assistant(block: dict, n: int, **fields) -> dict:
    return _row("assistant", n, message={"role": "assistant", "content": [block]}, **fields)


def assistant_text(text: str, n: int = 0, **fields) -> dict:
    return _assistant({"type": "text", "text": text}, n, **fields)


def thinking(text: str, n: int = 0) -> dict:
    return _assistant({"type": "thinking", "thinking": text}, n)


def tool_use(name: str, tool_input: dict, n: int = 0) -> dict:
    return _assistant(
        {"type": "tool_use", "id": f"toolu_{n}", "name": name, "input": tool_input}, n
    )


def tool_result(output: str, n: int = 0, **fields) -> dict:
    block = {"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": output}
    return _row("user", n, message={"role": "user", "content": [block]}, **fields)


def task_notification(body: str, n: int = 0) -> dict:
    return user_row(
        f"<task-notification>\n<task-id>task-{n}</task-id>\n{body}\n</task-notification>",
        n,
        turnOrigin="task_notification",
        origin={"kind": "task-notification"},
        promptSource="system",
    )


def user_row(text: str, n: int = 0, **fields) -> dict:
    return _row("user", n, message={"role": "user", "content": text}, **fields)


def human(text: str, n: int = 0, **fields) -> dict:
    return user_row(text, n, turnOrigin="human", origin={"kind": "human"}, promptSource="sdk", **fields)
