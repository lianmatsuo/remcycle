"""Builders for transcript rows shaped like the ones Claude Code writes, and a stand-in for the `claude` command."""

import json
import os
import sys
from pathlib import Path

from dream.dreaming import key

SESSION_ID = "11111111-aaaa-4bbb-8ccc-000000000001"


def write_transcript(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8", newline="\n")
    return path


def put_session(root: Path, session_id: str, rows: list[dict], cwd: str = "/repo") -> Path:
    """Write rows as one session's transcript, laid out under root as Claude Code does."""
    stamped = [{**row, "sessionId": session_id, **({"cwd": cwd} if "cwd" in row else {})} for row in rows]
    return write_transcript(root / key(cwd) / f"{session_id}.jsonl", stamped)


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


SAYS_WHERE_IT_SIGNS_IN = """\
import json, os, sys

sys.stdin.read()
print(json.dumps({"structured_output": {"folder": os.environ.get("CLAUDE_CONFIG_DIR", "unset")}, "total_cost_usd": 0.0}))
"""
"""A stand-in program that answers with the Claude Code folder it was started to sign in through."""


def stand_in_for_claude(tmp_path, monkeypatch, does):
    """Put a `claude` command first on the path that runs the Python program `does`."""
    program = tmp_path / "claude.py"
    program.write_text(does, encoding="utf-8", newline="\n")
    if sys.platform == "win32":
        # A batch file starts it, the way npm has Windows start a command it installs.
        (tmp_path / "claude.cmd").write_text(f'@"{sys.executable}" "{program}" %*\n', encoding="utf-8", newline="\r\n")
    else:
        launcher = tmp_path / "claude"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{program}" "$@"\n', encoding="utf-8", newline="\n")
        launcher.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
