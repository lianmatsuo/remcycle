import stat

import pytest

import dream.archive

from dream.archive import NO_PROJECT, Archive
from dream.transcript import Author
from support import assistant_text, human, put_session, task_notification, tool_use, write_transcript


@pytest.fixture
def root(tmp_path):
    return tmp_path / "projects"


@pytest.fixture
def archive(tmp_path):
    with Archive(tmp_path / "archive.db") as archive:
        yield archive


def found(hits):
    return [(h.session_id, h.seq) for h in hits]


def test_a_turn_can_be_found_after_ingest_and_points_back_to_its_session(root, archive):
    put_session(
        root,
        "s-retention",
        [
            human("why are old transcripts disappearing", 0),
            assistant_text("Claude Code sweeps transcripts after the retention period.", 1),
            {"type": "custom-title", "customTitle": "Retention sweep"},
        ],
    )

    archive.ingest(root)
    hits = archive.search("retention period", project=None)

    assert [(h.session_id, h.seq, h.author, h.title) for h in hits] == [
        ("s-retention", 1, Author.ASSISTANT, "Retention sweep")
    ]
    assert "retention period" in hits[0].snippet


def counts(report):
    return (report.added, report.updated, report.unchanged)


def test_ingesting_again_with_nothing_new_changes_nothing(root, archive):
    put_session(root, "s-1", [human("pin the base image version", 0)])

    first = archive.ingest(root)
    second = archive.ingest(root)

    assert counts(first) == (1, 0, 0)
    assert counts(second) == (0, 0, 1)
    assert found(archive.search("base image", project=None)) == [("s-1", 0)]


def test_a_session_that_kept_going_is_brought_up_to_date(root, archive):
    rows = [human("pin the base image version", 0)]
    put_session(root, "s-1", rows)
    archive.ingest(root)

    put_session(root, "s-1", [*rows, assistant_text("Pinned python:3.13.9-slim in the Dockerfile.", 1)])
    report = archive.ingest(root)

    assert counts(report) == (0, 1, 0)
    assert found(archive.search("Dockerfile", project=None)) == [("s-1", 1)]
    assert found(archive.search("base image", project=None)) == [("s-1", 0)]


def test_a_transcript_that_lost_turns_does_not_overwrite_what_was_archived(root, archive):
    rows = [
        human("which queue do refunds go through", 0),
        assistant_text("Refunds go through the ledger queue.", 1),
    ]
    put_session(root, "s-1", rows)
    archive.ingest(root)

    put_session(root, "s-1", rows[:1])
    report = archive.ingest(root)

    assert report.kept_longer == ["s-1"]
    assert counts(report) == (0, 0, 0)
    assert found(archive.search("ledger queue", project=None)) == [("s-1", 1)]


def test_secrets_pasted_into_a_prompt_are_redacted_before_they_are_stored(root, archive):
    # Assembled at run time so the repository never contains a string that looks like a live key.
    github = "ghp_" + "a1B2" * 9
    anthropic = "sk-ant-api03-" + "Zx9" * 12
    put_session(
        root,
        "s-1",
        [
            human(f"deploy with GITHUB_TOKEN={github} and my key {anthropic} please", 0),
            human("then connect to postgres://admin:hunter2pass@db.internal:5432/app", 1),
        ],
    )

    report = archive.ingest(root)
    stored = [turn.text for turn in archive.show("s-1")]

    assert stored == [
        "deploy with GITHUB_TOKEN=[redacted] and my key [redacted] please",
        "then connect to postgres://admin:[redacted]@db.internal:5432/app",
    ]
    assert report.redacted == 3


def test_ordinary_talk_about_tokens_and_keys_is_left_alone(root, archive):
    said = "set MAX_TOKENS = 4096, keep the token bucket, and read the key from os.environ"
    put_session(root, "s-1", [human(said, 0)])

    report = archive.ingest(root)

    assert [turn.text for turn in archive.show("s-1")] == [said]
    assert report.redacted == 0


def test_sessions_from_an_excluded_project_are_never_archived(root, archive):
    put_session(root, "s-private", [human("rotate the vault unseal keys", 0)], cwd="/work/private-client/api")
    put_session(root, "s-open", [human("rotate the vault unseal keys in the demo", 0)], cwd="/work/demo")

    report = archive.ingest(root, exclude=["/work/private-client"])

    assert report.excluded == 1
    assert found(archive.search("vault unseal", project=None)) == [("s-open", 0)]


def test_the_archive_file_is_readable_only_by_its_owner(tmp_path):
    path = tmp_path / "data" / "archive.db"

    with Archive(path):
        pass

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_session_stays_recallable_after_its_transcript_is_deleted(root, archive):
    path = put_session(root, "s-1", [human("the invoice rounding rule is half-even", 0)])
    archive.ingest(root)

    path.unlink()
    report = archive.ingest(root)

    assert counts(report) == (0, 0, 0)
    assert found(archive.search("invoice rounding", project=None)) == [("s-1", 0)]


def test_new_extraction_rules_rederive_every_session_that_still_has_its_transcript(root, archive, monkeypatch):
    put_session(root, "s-kept", [human("pin the base image version", 0)])
    gone = put_session(root, "s-gone", [human("the invoice rounding rule is half-even", 0)], cwd="/other")
    archive.ingest(root)
    gone.unlink()

    monkeypatch.setattr(dream.archive, "RULES_VERSION", dream.archive.RULES_VERSION + 1)
    report = archive.ingest(root)

    assert counts(report) == (0, 1, 0)
    assert found(archive.search("invoice rounding", project=None)) == [("s-gone", 0)]


def test_subagent_transcripts_are_not_archived_as_sessions(root, archive):
    path = put_session(root, "s-1", [human("audit the billing module", 0)])
    write_transcript(
        path.parent / "s-1" / "subagents" / "agent-a1.jsonl",
        [assistant_text("Found three unbounded queries in billing.", 1)],
    )

    report = archive.ingest(root)

    assert counts(report) == (1, 0, 0)
    assert archive.search("unbounded queries", project=None) == []


def test_search_stays_inside_one_project_unless_asked_to_cross(root, archive):
    prompts = {
        "s-root": "/work/shop",
        "s-worktree": "/work/shop/.claude/worktrees/fix-printer",
        "s-subdir": "/work/shop/apps/api",
        "s-other-client": "/work/blog",
        "s-similar-name": "/work/shop-admin",
    }
    for session_id, cwd in prompts.items():
        put_session(root, session_id, [human("add a retry to the label printer", 0)], cwd=cwd)
    archive.ingest(root)

    inside = archive.search("label printer retry", project="/work/shop")
    everywhere = archive.search("label printer retry", project=None)

    assert {h.session_id for h in inside} == {"s-root", "s-worktree", "s-subdir"}
    assert {h.session_id for h in everywhere} == set(prompts)


def test_a_session_run_in_a_git_worktree_belongs_to_its_repository(root, archive, tmp_path):
    repo = tmp_path / "code" / "billing-app"
    (repo / ".git" / "worktrees" / "retry-v2").mkdir(parents=True)
    worktree = tmp_path / "elsewhere" / "worktrees" / "retry-v2"
    (worktree / "src").mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {repo}/.git/worktrees/retry-v2\n")
    put_session(root, "s-worktree", [human("bump the http client", 0)], cwd=str(worktree / "src"))
    archive.ingest(root)

    assert found(archive.search("http client", project=str(repo))) == [("s-worktree", 0)]


def test_sessions_started_without_a_folder_share_one_group(root, archive):
    scratch = "/Users/someone/Library/Application Support/Claude/scratch-workspaces/aa11/bb22/scratch-2026-10-0{}"
    put_session(root, "s-a", [human("sketch a logo idea", 0)], cwd=scratch.format(1))
    put_session(root, "s-b", [human("another logo idea", 0)], cwd=scratch.format(2))
    put_session(root, "s-repo", [human("logo idea for the readme", 0)], cwd="/work/shop")
    archive.ingest(root)

    assert {h.session_id for h in archive.search("logo idea", project=NO_PROJECT)} == {"s-a", "s-b"}
    assert {h.session_id for h in archive.search("logo idea", project="/work/shop")} == {"s-repo"}


def test_when_no_turn_has_every_word_the_closest_partial_matches_come_back(root, archive):
    put_session(
        root,
        "s-1",
        [
            human("the staging database needs a migration", 0),
            assistant_text("Added the migration and ran it against staging.", 1),
            human("now update the changelog", 2),
        ],
    )
    archive.ingest(root)

    hits = archive.search("why did the staging migration deadlock", project=None)

    assert {h.seq for h in hits[:2]} == {0, 1}


def test_a_hit_says_whether_it_holds_every_word_asked_for(root, archive):
    put_session(root, "s-1", [human("the staging database needs a migration", 0)])
    archive.ingest(root)

    (full,) = archive.search("staging migration", project=None)
    (partial,) = archive.search("staging migration deadlock", project=None)

    assert full.matched_all is True
    assert partial.matched_all is False


def test_a_query_with_punctuation_is_read_as_plain_words(root, archive):
    put_session(root, "s-1", [assistant_text('Set "autoDreamEnabled": false in ~/.claude/settings.json.', 0)])
    archive.ingest(root)

    hits = archive.search('"autoDreamEnabled": false (settings.json)?', project=None)

    assert found(hits) == [("s-1", 0)]


def test_the_turn_most_about_the_query_comes_first(root, archive):
    put_session(
        root,
        "s-1",
        [
            assistant_text(
                "The deploy also touched logging, metrics, the cron schedule, three dashboards "
                "and, in passing, the cache.",
                0,
            ),
            assistant_text("Cache keys now include the tenant, and the cache is flushed on deploy.", 1),
        ],
    )
    archive.ingest(root)

    assert found(archive.search("cache", project=None)) == [("s-1", 1), ("s-1", 0)]


def test_tool_calls_stay_out_of_search_unless_asked_for(root, archive):
    put_session(
        root,
        "s-1",
        [
            tool_use("Bash", {"command": "grep -rn autoDreamEnabled ~/.claude/settings.json"}, 0),
            assistant_text("autoDreamEnabled is now false in your user settings.", 1),
        ],
    )
    archive.ingest(root)

    assert found(archive.search("autoDreamEnabled", project=None)) == [("s-1", 1)]
    assert set(found(archive.search("autoDreamEnabled", project=None, tools=True))) == {("s-1", 0), ("s-1", 1)}


def test_subagent_reports_stay_out_of_search_unless_asked_for(root, archive):
    put_session(
        root,
        "s-1",
        [
            task_notification("<summary>Audit finished</summary>\n<result>The ledger export leaks tenant ids.</result>", 0),
            assistant_text("The audit found that the ledger export leaks tenant ids.", 1),
        ],
    )
    archive.ingest(root)

    assert found(archive.search("ledger export", project=None)) == [("s-1", 1)]
    assert set(found(archive.search("ledger export", project=None, reports=True))) == {("s-1", 0), ("s-1", 1)}


def test_search_can_be_limited_to_a_date_range(root, archive):
    put_session(root, "s-sept", [human("rotate the signing key", 0, timestamp="2026-09-20T10:00:00.000Z")])
    put_session(root, "s-oct", [human("rotate the signing key again", 0, timestamp="2026-10-03T10:00:00.000Z")])
    archive.ingest(root)

    def within(**dates):
        return found(archive.search("signing key", project=None, **dates))

    assert within(since="2026-10-01") == [("s-oct", 0)]
    assert within(until="2026-09-30") == [("s-sept", 0)]
    assert within(since="2026-10-03", until="2026-10-03") == [("s-oct", 0)]


LEDGER_SESSION = "7f3a9c2e-1b4d-4e6f-8a90-123456789abc"


def put_ledger_session(root):
    put_session(
        root,
        LEDGER_SESSION,
        [
            human("should refunds bypass the ledger", 0),
            assistant_text("No: every refund must post to the ledger first.", 1),
            human("ok, keep it that way", 2),
            assistant_text("Left the ledger path unchanged.", 3),
        ],
    )


def test_a_hit_can_be_read_back_verbatim_with_its_surroundings(root, archive):
    put_ledger_session(root)
    archive.ingest(root)

    span = archive.show("7f3a9c2e", first=1, last=2)

    assert [(t.seq, t.author, t.text) for t in span] == [
        (1, Author.ASSISTANT, "No: every refund must post to the ledger first."),
        (2, Author.HUMAN, "ok, keep it that way"),
    ]


def test_reading_back_a_session_that_cannot_be_pinned_down_is_an_error(root, archive):
    put_ledger_session(root)
    put_session(root, "7f3a9c2e-ffff-4e6f-8a90-123456789abc", [human("unrelated", 0)])
    archive.ingest(root)

    with pytest.raises(LookupError, match="no archived session"):
        archive.show("deadbeef")
    with pytest.raises(LookupError, match="2 archived sessions"):
        archive.show("7f3a9c2e")
