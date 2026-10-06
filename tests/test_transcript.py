import json

from dream.transcript import Author, Kind, parse_transcript
from support import (
    SESSION_ID,
    assistant_text,
    human,
    task_notification,
    thinking,
    tool_result,
    tool_use,
    user_row,
    write_transcript,
)


def turns(session):
    return [(t.seq, t.author, t.kind, t.text) for t in session.turns]


def test_human_prompt_is_kept_with_injected_reminders_stripped(tmp_path):
    path = write_transcript(
        tmp_path / "s.jsonl",
        [
            human(
                "<system-reminder>\nToday's date is 2026-10-01.\n</system-reminder>\n\n"
                "ship the retention fix"
            )
        ],
    )

    session = parse_transcript(path)

    assert turns(session) == [(0, Author.HUMAN, Kind.PROMPT, "ship the retention fix")]


def test_assistant_prose_is_kept_and_tool_calls_shrink_to_name_and_target(tmp_path):
    path = write_transcript(
        tmp_path / "s.jsonl",
        [
            human("fix the sweep", 0),
            thinking("the bug is probably in the cutoff", 1),
            assistant_text("I'll patch the sweep.", 2),
            tool_use("Edit", {"file_path": "/repo/sweep.py", "old_string": "a", "new_string": "b"}, 3),
            tool_result("The file /repo/sweep.py has been updated", 3),
            tool_use("Bash", {"command": "uv run pytest -q\necho done", "description": "Run tests"}, 4),
            tool_result("14 passed in 0.31s", 4),
        ],
    )

    session = parse_transcript(path)

    assert turns(session) == [
        (0, Author.HUMAN, Kind.PROMPT, "fix the sweep"),
        (1, Author.ASSISTANT, Kind.REPLY, "I'll patch the sweep."),
        (2, Author.ASSISTANT, Kind.TOOL, "Edit /repo/sweep.py"),
        (3, Author.ASSISTANT, Kind.TOOL, "Bash uv run pytest -q"),
    ]


def test_user_role_rows_are_attributed_to_who_actually_wrote_them(tmp_path):
    path = write_transcript(
        tmp_path / "s.jsonl",
        [
            user_row(
                "<task-notification>\n<task-id>a1</task-id>\n</task-notification>",
                0,
                turnOrigin="task_notification",
                origin={"kind": "task-notification"},
                promptSource="system",
            ),
            user_row("Base directory for this skill: /skills/tdd", 1, isMeta=True),
            user_row(
                "This session is being continued from a previous conversation",
                2,
                turnOrigin="human",
                isCompactSummary=True,
            ),
            user_row("Finish the second half of the ticket", 3, turnOrigin="sdk", promptSource="sdk"),
            user_row(
                "Another Claude session sent a message: rebase first",
                4,
                isMeta=True,
                turnOrigin="peer",
                origin={"kind": "peer"},
                promptSource="system",
            ),
            user_row("[Request interrupted by user]", 5),
        ],
    )

    session = parse_transcript(path)

    assert turns(session) == [
        (0, Author.AUTOMATION, Kind.PROMPT, "Finish the second half of the ticket"),
        (1, Author.PEER, Kind.PROMPT, "Another Claude session sent a message: rebase first"),
        (2, Author.UNKNOWN, Kind.PROMPT, "[Request interrupted by user]"),
    ]


def test_a_background_subagents_final_report_is_kept_as_a_report(tmp_path):
    path = write_transcript(
        tmp_path / "s.jsonl",
        [
            task_notification("<status>running</status>\n<summary>Build started</summary>", 0),
            task_notification(
                "<status>completed</status>\n"
                '<summary>Agent "Audit billing queries" finished</summary>\n'
                "<result>Found three unbounded queries in billing/report.py.</result>\n"
                "<usage><subagent_tokens>1200</subagent_tokens></usage>",
                1,
            ),
        ],
    )

    session = parse_transcript(path)

    assert turns(session) == [
        (
            0,
            Author.SUBAGENT,
            Kind.REPORT,
            'Agent "Audit billing queries" finished\n\nFound three unbounded queries in billing/report.py.',
        )
    ]


def test_a_foreground_subagents_final_report_is_kept_as_a_report(tmp_path):
    finished = {
        "status": "completed",
        "agentId": "a1",
        "content": [{"type": "text", "text": "Found three unbounded queries in billing/report.py."}],
    }
    path = write_transcript(
        tmp_path / "s.jsonl",
        [
            tool_use("Agent", {"description": "Audit billing queries", "prompt": "Look for unbounded queries"}, 0),
            tool_result("The agent finished.", 0, toolUseResult=finished),
            tool_use("Agent", {"description": "Rebuild the index", "prompt": "Rebuild it"}, 1),
            tool_result("Async agent launched.", 1, toolUseResult={"status": "async_launched", "agentId": "a2"}),
        ],
    )

    session = parse_transcript(path)

    assert turns(session) == [
        (0, Author.ASSISTANT, Kind.TOOL, "Agent Audit billing queries"),
        (1, Author.SUBAGENT, Kind.REPORT, "Found three unbounded queries in billing/report.py."),
        (2, Author.ASSISTANT, Kind.TOOL, "Agent Rebuild the index"),
    ]


def test_session_records_its_identity_span_and_where_it_ended_up(tmp_path):
    path = write_transcript(
        tmp_path / "s.jsonl",
        [
            human("start here", 0, cwd="/tmp/scratch", gitBranch="HEAD", entrypoint="claude-desktop"),
            {"type": "custom-title", "sessionId": SESSION_ID, "customTitle": "Untitled"},
            assistant_text("moving to the repo", 1),
            human("carry on", 2, cwd="/repo", gitBranch="main", entrypoint="claude-desktop", version="2.1.283"),
            {"type": "custom-title", "sessionId": SESSION_ID, "customTitle": "Retention sweep fix"},
        ],
    )

    session = parse_transcript(path)

    assert session.session_id == SESSION_ID
    assert session.title == "Retention sweep fix"
    assert session.claude_code_version == "2.1.283"
    assert (session.cwd, session.git_branch, session.entrypoint) == ("/repo", "main", "claude-desktop")
    assert (session.started_at, session.ended_at) == (
        "2026-10-01T09:00:00.000Z",
        "2026-10-01T09:00:02.000Z",
    )
    assert [(t.uuid, t.timestamp) for t in session.turns] == [
        ("uuid-0", "2026-10-01T09:00:00.000Z"),
        ("uuid-1", "2026-10-01T09:00:01.000Z"),
        ("uuid-2", "2026-10-01T09:00:02.000Z"),
    ]


def test_a_session_nobody_named_takes_the_generated_title(tmp_path):
    path = write_transcript(
        tmp_path / "s.jsonl",
        [
            human("why do errors vanish here", 0),
            {"type": "ai-title", "sessionId": SESSION_ID, "aiTitle": "Swallowed exception cleanup"},
        ],
    )

    assert parse_transcript(path).title == "Swallowed exception cleanup"


def test_a_transcript_still_being_written_parses_up_to_its_last_complete_row(tmp_path):
    path = write_transcript(
        tmp_path / "s.jsonl",
        [
            {"type": "file-history-snapshot", "messageId": "m", "snapshot": {}},
            human("first prompt", 0),
            {"type": "some-future-row", "payload": [1, 2, 3]},
            {"type": "assistant", "uuid": "uuid-1", "message": {}},
        ],
    )
    with path.open("a", encoding="utf-8", newline="\n") as transcript:
        transcript.write('{"type": "assistant", "uuid": "uuid-2", "message": {"role": "assis')

    session = parse_transcript(path)

    assert turns(session) == [(0, Author.HUMAN, Kind.PROMPT, "first prompt")]
    assert session.unreadable_rows == 1


def test_a_transcript_is_read_as_utf8_whatever_the_system_would_choose(tmp_path):
    said = "naïve café → 日本語 🙂"
    path = tmp_path / "s.jsonl"
    path.write_bytes((json.dumps(human(said, 0), ensure_ascii=False) + "\n").encode())

    assert turns(parse_transcript(path)) == [(0, Author.HUMAN, Kind.PROMPT, said)]
