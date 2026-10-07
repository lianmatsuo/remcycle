import stat
import subprocess
import sys

import pytest

import dream.archive
from dream.archive import NO_PROJECT, Archive, project_of
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


def test_a_folder_excluded_with_a_closing_slash_is_left_out_like_any_other(root, archive):
    put_session(root, "s-root", [human("rotate the vault unseal keys", 0)], cwd="/work/private-client")
    put_session(root, "s-inside", [human("rotate the vault unseal keys again", 0)], cwd="/work/private-client/api")
    put_session(root, "s-beside", [human("rotate the vault unseal keys in the demo", 0)], cwd="/work/private-client-demo")

    report = archive.ingest(root, exclude=["/work/private-client/"])

    assert report.excluded == 2
    assert found(archive.search("vault unseal", project=None)) == [("s-beside", 0)]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows keeps who may read a file in a list of its own, not in these bits")
def test_the_archive_file_is_readable_only_by_its_owner(tmp_path):
    path = tmp_path / "data" / "archive.db"

    with Archive(path):
        pass

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


@pytest.mark.skipif(sys.platform != "win32", reason="the list Windows keeps of who may read a file")
def test_on_windows_the_archive_is_kept_from_the_other_users_of_the_machine(tmp_path):
    def open_to_all_users(path):
        return "BUILTIN\\Users" in subprocess.run(["icacls", str(path)], check=True, capture_output=True, text=True, encoding="oem").stdout

    shared = tmp_path / "shared"
    shared.mkdir()
    # S-1-5-32-545 is the group every user of the machine belongs to.
    subprocess.run(["icacls", str(shared), "/grant", "*S-1-5-32-545:(OI)(CI)RX"], check=True, capture_output=True)
    path = shared / "data" / "archive.db"

    with Archive(path):
        pass

    assert open_to_all_users(shared)
    assert not open_to_all_users(path.parent)
    assert not open_to_all_users(path)


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
    (worktree / ".git").write_text(f"gitdir: {repo}/.git/worktrees/retry-v2\n", encoding="utf-8", newline="\n")
    put_session(root, "s-worktree", [human("bump the http client", 0)], cwd=str(worktree / "src"))
    archive.ingest(root)

    assert found(archive.search("http client", project=str(repo))) == [("s-worktree", 0)]


def test_a_project_is_matched_by_its_path_as_this_system_writes_it(root, archive, tmp_path):
    client = tmp_path / "work" / "private-client"
    put_session(root, "s-private", [human("rotate the vault unseal keys", 0)], cwd=str(client / "api"))
    put_session(root, "s-shop", [human("rotate the vault unseal keys in the demo", 0)], cwd=str(tmp_path / "work" / "shop" / "apps"))
    put_session(root, "s-gone", [human("the vault unseal keys moved", 0)], cwd=str(tmp_path / "work" / "shop" / ".claude" / "worktrees" / "fix"))

    report = archive.ingest(root, exclude=[str(client)])

    assert report.excluded == 1
    assert {h.session_id for h in archive.search("vault unseal", project=str(tmp_path / "work"))} == {"s-shop", "s-gone"}
    assert {h.session_id for h in archive.search("vault unseal", project=str(tmp_path / "work" / "shop"))} == {"s-shop", "s-gone"}
    assert [h.project for h in archive.search("keys moved", project=None)] == [str(tmp_path / "work" / "shop")]


@pytest.mark.skipif(sys.platform != "win32", reason="drive letters, and a path written with either slash")
def test_on_windows_a_folder_is_one_project_however_its_path_is_written(root, archive, tmp_path):
    client = tmp_path / "work" / "private-client"
    as_typed = str(client)[0].lower() + str(client)[1:].replace("\\", "/")
    put_session(root, "s-private", [human("rotate the vault unseal keys", 0)], cwd=str(client / "api"))
    put_session(root, "s-shop", [human("rotate the vault unseal keys in the demo", 0)], cwd=str(tmp_path / "work" / "shop"))

    assert project_of(as_typed) == str(client)
    assert archive.ingest(root, exclude=[as_typed.upper()]).excluded == 1
    assert found(archive.search("vault unseal", project=str(tmp_path / "work").upper().replace("\\", "/"))) == [("s-shop", 0)]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows would go looking for the machine these paths name")
def test_a_project_on_a_network_share_is_one_folder_however_its_path_is_written(root, archive, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    put_session(root, "s-private", [human("rotate the vault unseal keys", 0)], cwd="\\\\nas\\share\\private-client\\api")
    put_session(root, "s-shop", [human("rotate the vault unseal keys in the demo", 0)], cwd="\\\\nas\\share\\shop")

    assert archive.ingest(root, exclude=["//NAS/share/private-client"]).excluded == 1
    assert found(archive.search("vault unseal", project="//nas/SHARE")) == [("s-shop", 0)]
    assert project_of("//nas/share/shop/.claude/worktrees/fix") == "\\\\nas\\share\\shop"
    assert project_of("//nas/share/.claude/worktrees/fix") == "\\\\nas\\share"


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


def test_a_session_is_recalled_in_brief_by_what_the_dream_made_of_it(root, archive):
    put_ledger_session(root)
    archive.ingest(root)

    unread = archive.recap("7f3a9c2e")
    assert (unread.session_id, unread.turns, unread.summary) == (LEDGER_SESSION, 4, None)

    archive.keep_digest(LEDGER_SESSION, {"summary": "Agreed that every refund posts to the ledger first."})

    assert archive.recap("7f3a9c2e").summary == "Agreed that every refund posts to the ledger first."
    with pytest.raises(LookupError, match="no archived session"):
        archive.recap("deadbeef")

    # The summary describes the session as it was read. Once the session has grown it no longer stands.
    put_session(
        root,
        LEDGER_SESSION,
        [
            human("should refunds bypass the ledger", 0),
            assistant_text("No: every refund must post to the ledger first.", 1),
            human("ok, keep it that way", 2),
            assistant_text("Left the ledger path unchanged.", 3),
            human("actually, let partial refunds skip it", 4),
        ],
    )
    archive.ingest(root)
    grown = archive.recap("7f3a9c2e")
    assert (grown.turns, grown.summary) == (5, None)


def test_a_session_waits_for_its_dream_until_one_is_recorded_and_again_once_it_grows(root, archive):
    rows = [human("pin the base image version", 0)]
    put_session(root, "s-1", rows, cwd="/work/shop")
    archive.ingest(root)
    assert [(s.session_id, s.project) for s in archive.awaiting_dream()] == [("s-1", "/work/shop")]

    archive.record_dream("s-1")
    assert archive.awaiting_dream() == []

    put_session(root, "s-1", [*rows, assistant_text("Pinned python:3.13.9-slim.", 1)], cwd="/work/shop")
    archive.ingest(root)
    assert [s.session_id for s in archive.awaiting_dream()] == ["s-1"]


def test_a_session_in_which_nothing_was_said_does_not_wait_for_a_dream_until_something_is(root, archive):
    opened = [{"type": "custom-title", "customTitle": "Opened and left"}]
    put_session(root, "s-1", opened, cwd="/work/shop")
    archive.ingest(root)
    assert archive.awaiting_dream() == []

    put_session(root, "s-1", [*opened, human("pin the base image version", 0)], cwd="/work/shop")
    archive.ingest(root)
    assert [s.session_id for s in archive.awaiting_dream()] == ["s-1"]


def test_what_the_person_typed_ranks_above_the_assistants_words_when_both_match_alike(root, archive):
    put_session(
        root,
        "s-1",
        [assistant_text("use pnpm for the workspace", 0), human("use pnpm for the workspace", 1)],
    )
    archive.ingest(root)

    assert found(archive.search("pnpm workspace", project=None)) == [("s-1", 1), ("s-1", 0)]


def test_several_phrasings_are_searched_together_and_a_turn_more_of_them_find_ranks_higher(root, archive):
    put_session(
        root,
        "s-1",
        [
            assistant_text("The retention sweep deletes transcripts.", 0),
            assistant_text("Old sessions get cleaned up after thirty days by the retention sweep.", 1),
            assistant_text("Nothing to do with it.", 2),
        ],
    )
    archive.ingest(root)

    hits = archive.search(["retention sweep", "sessions cleaned up", "zebra crossing"], project=None)

    assert found(hits) == [("s-1", 1), ("s-1", 0)]
    assert all(hit.matched_all for hit in hits)


def test_sessions_archived_before_their_project_was_excluded_can_be_found_and_removed_for_good(root, tmp_path):
    put_session(
        root,
        "s-private",
        [human("the zebrafinch contract is confidential", 0), {"type": "custom-title", "customTitle": "Contract terms"}],
        cwd="/work/private-client/api",
    )
    put_session(root, "s-open", [human("always use pnpm here", 0)], cwd="/work/demo")
    file = tmp_path / "archive.db"

    with Archive(file) as archive:
        archive.ingest(root)
        archive.record_dream("s-private")
        archive.keep_digest("s-private", {"summary": "zebrafinch contract terms"})

        kept = archive.under(["/work/private-client"])
        assert [(k.session_id, k.project, k.title, k.turns) for k in kept] == [
            ("s-private", "/work/private-client/api", "Contract terms", 1)
        ]

        assert archive.remove(["s-private"]) == 1

        assert archive.under(["/work/private-client"]) == []
        assert archive.search("zebrafinch", project=None) == []
        assert archive.digest("s-private") is None
        assert found(archive.search("pnpm", project=None)) == [("s-open", 0)]

    assert all(b"zebrafinch" not in left.read_bytes() for left in tmp_path.glob("archive.db*"))


def test_the_sessions_waiting_can_be_kept_to_those_that_ended_after_a_moment(root, archive):
    put_session(root, "s-old", [human("use pnpm here", 0, timestamp="2026-09-20T09:00:00.000Z")], cwd="/work/shop")
    put_session(root, "s-new", [human("use bun here", 0, timestamp="2026-10-03T09:00:00.000Z")], cwd="/work/shop")
    archive.ingest(root)

    assert [s.session_id for s in archive.awaiting_dream(since="2026-10-01T00:00:00+00:00")] == ["s-new"]
    assert [s.session_id for s in archive.awaiting_dream()] == ["s-old", "s-new"]
