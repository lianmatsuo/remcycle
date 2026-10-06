import os
import threading

import pytest

import dream.disk
from dream.claims import Claim, ClaimType, Evidence, Provenance, Scope, Status
from dream.extract import Thread
from dream.memory import Closed, MemoryStore
from dream.reconcile import Add, Alias, Confirm, Contest, Demote, Question, Review, Supersede, Withhold

LEGACY_FILE = """---
name: deploy-target
description: "Deploys go to the staging cluster first: never straight to production."
metadata:
  node_type: memory
  type: project
  modified: 2026-09-18T10:13:32.806Z
---

Deploys go to the staging cluster first.

**Why:** a bad migration reached production in August.
"""

LEGACY_INDEX = "- [Deploy target](deploy_target.md) — staging first, then production\n"


@pytest.fixture
def folder(tmp_path):
    """A memory folder as Claude Code leaves it: one memory and its index line."""
    folder = tmp_path / "memory"
    folder.mkdir()
    (folder / "deploy_target.md").write_text(LEGACY_FILE, encoding="utf-8", newline="\n")
    (folder / "MEMORY.md").write_text(LEGACY_INDEX, encoding="utf-8", newline="\n")
    return folder


def claim(slot, statement, *, by=Provenance.HUMAN, type=ClaimType.PREFERENCE, why=""):
    return Claim(
        slot=slot,
        type=type,
        scope=Scope.PROJECT,
        statement=statement,
        why=why,
        provenance=by,
        evidence=Evidence("7f3a9c2e-1b4d-4e6f-8a90-123456789abc", 3, 4),
        said_at="2026-10-02T09:00:00Z",
    )


def test_memories_claude_code_wrote_are_read_as_entries_without_provenance(folder):
    entries = MemoryStore(folder).entries()

    assert list(entries) == ["deploy_target"]
    legacy = entries["deploy_target"]
    assert legacy.statement == "Deploys go to the staging cluster first: never straight to production."
    assert (legacy.provenance, legacy.status, legacy.evidence) == (None, Status.ACTIVE, ())


def test_an_added_claim_becomes_a_memory_file_claude_code_can_read_and_an_index_line(folder):
    store = MemoryStore(folder)

    store.apply([Add(claim("package-manager", "Use pnpm for JS projects.", why="npm lockfiles drifted."))])

    assert (folder / "package-manager.md").read_text(encoding="utf-8") == (
        "---\n"
        "name: package-manager\n"
        'description: "Use pnpm for JS projects."\n'
        "metadata:\n"
        "  type: feedback\n"
        "---\n"
        "\n"
        "Use pnpm for JS projects.\n"
        "\n"
        "**Why:** npm lockfiles drifted.\n"
        "\n"
        "Evidence: `dream show 7f3a9c2e --first 3 --last 4`\n"
    )
    assert (folder / "MEMORY.md").read_text(encoding="utf-8") == (
        LEGACY_INDEX + "- [Package manager](package-manager.md) — Use pnpm for JS projects.\n"
    )
    added = MemoryStore(folder).entries()["package-manager"]
    assert (added.provenance, added.type, added.said_at) == (
        Provenance.HUMAN,
        ClaimType.PREFERENCE,
        "2026-10-02T09:00:00Z",
    )
    assert added.evidence == (Evidence("7f3a9c2e-1b4d-4e6f-8a90-123456789abc", 3, 4),)


def test_hearing_something_again_records_evidence_without_touching_any_memory_file(folder):
    store = MemoryStore(folder)
    store.apply([Add(claim("package-manager", "Use pnpm for JS projects."))])
    before = {path.name: path.read_bytes() for path in folder.glob("*.md")}

    store.apply([Confirm("package-manager", Evidence("another-session", 10, 11), "2026-10-04T09:00:00Z")])

    assert {path.name: path.read_bytes() for path in folder.glob("*.md")} == before
    assert MemoryStore(folder).entries()["package-manager"].evidence == (
        Evidence("7f3a9c2e-1b4d-4e6f-8a90-123456789abc", 3, 4),
        Evidence("another-session", 10, 11),
    )


def test_a_superseding_claim_replaces_the_file_and_its_index_line_and_the_old_statement_is_kept_as_history(folder):
    store = MemoryStore(folder)
    newer = claim("deploy_target", "Deploys go straight to production.", type=ClaimType.DECISION)

    store.apply([Supersede("deploy_target", newer)])

    assert "Deploys go straight to production." in (folder / "deploy_target.md").read_text(encoding="utf-8")
    assert "staging" not in (folder / "deploy_target.md").read_text(encoding="utf-8")
    assert (folder / "MEMORY.md").read_text(encoding="utf-8") == "- [Deploy target](deploy_target.md) — Deploys go straight to production.\n"
    assert store.entries()["deploy_target"].provenance == Provenance.HUMAN
    assert [past.statement for past in store.history("deploy_target")] == [
        "Deploys go to the staging cluster first: never straight to production."
    ]


def test_a_contested_entry_is_withheld_from_sessions_and_put_to_the_person(folder):
    store = MemoryStore(folder)
    seen = claim("deploy_target", "Deploys go straight to production.", by=Provenance.OBSERVED, type=ClaimType.FACT)

    store.apply([Contest("deploy_target", seen)])

    assert not (folder / "deploy_target.md").exists()
    assert (folder / "MEMORY.md").read_text(encoding="utf-8") == ""
    assert store.entries()["deploy_target"].status == Status.CONTESTED
    assert [(open_.slot, open_.claim.statement, open_.withheld) for open_ in store.queue()] == [
        ("deploy_target", "Deploys go straight to production.", True)
    ]


def test_a_question_leaves_memory_as_it_is_and_waits_for_the_person(folder):
    store = MemoryStore(folder)
    before = {path.name: path.read_bytes() for path in folder.glob("*.md")}
    guess = claim("deploy_target", "Deploys go straight to production.", by=Provenance.INFERRED)

    store.apply([Question("deploy_target", guess)])
    store.apply([Question("deploy_target", guess)])

    assert {path.name: path.read_bytes() for path in folder.glob("*.md")} == before
    assert [(open_.slot, open_.withheld) for open_ in store.queue()] == [("deploy_target", False)]


def test_a_lesson_nobody_endorsed_is_kept_on_file_but_out_of_the_index_sessions_always_load(folder):
    store = MemoryStore(folder)
    lesson = claim("flaky-e2e", "Retrying the e2e suite hid a real race.", by=Provenance.INFERRED, type=ClaimType.LESSON)

    store.apply([Add(lesson)])
    store.ensure_indexed()

    assert (folder / "flaky-e2e.md").exists()
    assert (folder / "MEMORY.md").read_text(encoding="utf-8") == LEGACY_INDEX


def test_open_threads_are_kept_until_finished_or_two_weeks_stale(folder):
    store = MemoryStore(folder)
    store.note_threads(
        [Thread("ci-cache", "CI cache for pnpm is not set up yet.", True, 4), Thread("old-idea", "Try bun.", True, 9)],
        "session-a",
        "2026-10-01T09:00:00Z",
    )
    store.note_threads([Thread("ci-cache", "CI cache is half done.", True, 7)], "session-b", "2026-10-10T09:00:00Z")

    assert store.threads(now="2026-10-12T09:00:00Z") == {"ci-cache": "CI cache is half done.", "old-idea": "Try bun."}
    assert store.threads(now="2026-10-16T09:00:00Z") == {"ci-cache": "CI cache is half done."}

    store.note_threads([Thread("ci-cache", "CI cache works.", False, 3)], "session-c", "2026-10-11T09:00:00Z")
    assert store.threads(now="2026-10-12T09:00:00Z") == {"old-idea": "Try bun."}


def test_the_person_can_close_a_thread_themselves_and_it_stays_closed_until_a_session_reopens_it(folder):
    store = MemoryStore(folder)
    store.note_threads(
        [Thread("ci-cache", "CI cache for pnpm is not set up yet.", True, 4), Thread("old-idea", "Try bun.", True, 9)],
        "session-a",
        "2026-10-01T09:00:00Z",
    )

    store.close_thread("ci-cache")

    assert store.threads(now="2026-10-02T09:00:00Z") == {"old-idea": "Try bun."}
    with pytest.raises(LookupError, match="ci-cache"):
        store.close_thread("ci-cache")

    store.note_threads([Thread("ci-cache", "CI cache is still missing.", True, 2)], "session-b", "2026-10-03T09:00:00Z")
    assert store.threads(now="2026-10-04T09:00:00Z") == {"ci-cache": "CI cache is still missing.", "old-idea": "Try bun."}


def test_ruling_for_the_existing_entry_puts_a_withheld_one_back_as_it_was(folder):
    store = MemoryStore(folder)
    before = (folder / "deploy_target.md").read_bytes()
    store.apply([Contest("deploy_target", claim("deploy_target", "Deploys go straight to production.", by=Provenance.OBSERVED, type=ClaimType.FACT))])

    store.resolve("deploy_target", accept=False)

    assert (folder / "deploy_target.md").read_bytes() == before
    assert "](deploy_target.md)" in (folder / "MEMORY.md").read_text(encoding="utf-8")
    assert store.entries()["deploy_target"].status == Status.ACTIVE
    assert store.queue() == []


def test_ruling_for_the_new_claim_makes_it_the_entry_on_the_persons_authority(folder):
    store = MemoryStore(folder)
    store.apply([Question("deploy_target", claim("deploy_target", "Deploys go straight to production.", by=Provenance.INFERRED))])

    store.resolve("deploy_target", accept=True)

    ruled = store.entries()["deploy_target"]
    assert (ruled.statement, ruled.provenance) == ("Deploys go straight to production.", Provenance.HUMAN)
    assert store.queue() == []


def test_reading_a_memory_is_counted_and_a_fact_whose_file_is_gone_carries_a_warning(folder, tmp_path):
    repo = tmp_path / "repo"
    (repo / "infra").mkdir(parents=True)
    (repo / "infra" / "deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8", newline="\n")
    store = MemoryStore(folder)
    fact = Claim(**{**claim("deploy-script", "Deploys run through infra/deploy.sh.", type=ClaimType.FACT).__dict__, "anchor": "infra/deploy.sh"})
    store.apply([Add(fact)])

    assert store.note_read("deploy-script", repo, "2026-10-03T09:00:00Z") is None
    (repo / "infra" / "deploy.sh").unlink()
    warning = store.note_read("deploy-script", repo, "2026-10-04T09:00:00Z")

    assert warning == "This memory is about infra/deploy.sh, which no longer exists in the repository. Check before relying on it."
    assert store.reads("deploy-script") == 2


def test_how_much_a_memory_is_needed_counts_each_time_it_was_said_and_each_time_it_was_looked_up(folder):
    store = MemoryStore(folder)
    store.apply([Add(claim("package-manager", "Use pnpm for JS projects."))])
    assert store.needed("package-manager") == 1

    # More turns of the session it was first said in are the same occasion, not another one.
    store.apply([Confirm("package-manager", Evidence("7f3a9c2e-1b4d-4e6f-8a90-123456789abc", 10, 11), "2026-10-03T09:00:00Z")])
    assert store.needed("package-manager") == 1

    store.apply([Confirm("package-manager", Evidence("another-session", 10, 11), "2026-10-04T09:00:00Z")])
    store.note_read("package-manager", folder, "2026-10-05T09:00:00Z")
    store.note_found(["package-manager", "no-such-memory"], "2026-10-06T09:00:00Z")

    assert store.needed("package-manager") == 4
    assert store.needed("deploy_target") == 0
    assert store.needed("no-such-memory") == 0


def test_among_memories_that_match_a_search_alike_the_one_needed_most_comes_first(folder):
    store = MemoryStore(folder)
    store.apply([Add(claim("lint-js", "Lint JS with biome.")), Add(claim("format-js", "Format JS with biome."))])
    first, second = [found.slot for found in store.find("biome")]

    store.note_found([second], "2026-10-06T09:00:00Z")

    assert [found.slot for found in store.find("biome")] == [second, first]
    assert [found.slot for found in store.find("lint js")] == ["lint-js"]

    # How a memory matched still comes first: one found by its name leads however much the others are needed.
    store.apply([Add(claim("biome", "Biome is pinned to one version across the repo."))])
    assert [found.slot for found in store.find("biome")] == ["biome", second, first]


def test_counts_written_at_once_are_all_kept(folder):
    store = MemoryStore(folder)
    store.apply([Add(claim("package-manager", "Use pnpm for JS projects."))])

    def look_up_many_times():
        for n in range(25):
            MemoryStore(folder).note_found(["package-manager"], f"2026-10-06T09:00:{n:02d}Z")

    writers = [threading.Thread(target=look_up_many_times) for _ in range(4)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()

    assert store.reads("package-manager") == 100


def test_a_look_up_is_counted_beside_the_folder_and_changes_nothing_inside_it(folder):
    store = MemoryStore(folder)
    store.apply([Add(claim("package-manager", "Use pnpm for JS projects."))])
    inside = sorted(path.relative_to(folder) for path in folder.rglob("*"))

    store.note_found(["package-manager"], "2026-10-06T09:00:00Z")
    store.note_read("package-manager", folder, "2026-10-06T09:01:00Z")

    assert sorted(path.relative_to(folder) for path in folder.rglob("*")) == inside
    assert store.reads("package-manager") == 2


def test_an_index_over_its_budget_sheds_the_oldest_unread_legacy_lines_and_keeps_every_file(folder):
    for n, modified in enumerate(["2026-05-01", "2026-09-01", "2026-07-01"]):
        (folder / f"note-{n}.md").write_text(
            f'---\nname: note-{n}\ndescription: "Note {n}."\nmetadata:\n  type: project\n  modified: {modified}T00:00:00Z\n---\n\nNote {n}.\n',
            encoding="utf-8",
            newline="\n",
        )
    (folder / "MEMORY.md").write_text(
        "- [Note 0](note-0.md) — n0\n- [Note 1](note-1.md) — n1\n- [Note 2](note-2.md) — n2\n",
        encoding="utf-8",
        newline="\n",
    )
    store = MemoryStore(folder)
    store.apply([Add(claim("package-manager", "Use pnpm for JS projects."))])
    store.note_read("note-0", folder, "2026-10-01T09:00:00Z")

    shed = store.fit_index(max_lines=2, max_bytes=10_000)

    assert shed == ["note-2", "note-1"]
    assert (folder / "MEMORY.md").read_text(encoding="utf-8") == (
        "- [Note 0](note-0.md) — n0\n- [Package manager](package-manager.md) — Use pnpm for JS projects.\n"
    )
    assert sorted(p.name for p in folder.glob("note-*.md")) == ["note-0.md", "note-1.md", "note-2.md"]


def test_an_index_over_its_budget_sheds_what_was_said_once_before_what_was_said_again(folder):
    store = MemoryStore(folder)
    store.apply([Add(claim("package-manager", "Use pnpm for JS projects.")), Add(claim("ci-runner", "CI is self-hosted."))])
    store.apply([Confirm("package-manager", Evidence("another-session", 10, 11), "2026-10-04T09:00:00Z")])

    shed = store.fit_index(max_lines=1, max_bytes=10_000)

    assert shed == ["deploy_target", "ci-runner"]
    assert (folder / "MEMORY.md").read_text(encoding="utf-8") == "- [Package manager](package-manager.md) — Use pnpm for JS projects.\n"


def test_an_entry_withheld_for_a_reason_waits_for_the_person_and_returns_if_they_keep_it(folder):
    store = MemoryStore(folder)
    before = (folder / "deploy_target.md").read_bytes()

    store.apply([Withhold("deploy_target", "infra/deploy.sh, which it is about, no longer exists")])

    assert not (folder / "deploy_target.md").exists()
    assert store.entries()["deploy_target"].status == Status.STALE
    assert [(item.slot, item.claim, item.withheld, item.reason) for item in store.queue()] == [
        ("deploy_target", None, True, "infra/deploy.sh, which it is about, no longer exists")
    ]

    store.resolve("deploy_target", accept=False)

    assert (folder / "deploy_target.md").read_bytes() == before
    assert (store.entries()["deploy_target"].status, store.queue()) == (Status.ACTIVE, [])


def test_agreeing_with_a_review_retires_the_entry_from_sessions_without_destroying_it(folder):
    store = MemoryStore(folder)
    before = (folder / "deploy_target.md").read_bytes()

    store.apply([Review("deploy_target", "it describes a migration that finished in August")])
    assert (folder / "deploy_target.md").exists()

    store.resolve("deploy_target", accept=True)

    assert not (folder / "deploy_target.md").exists()
    assert (folder / "MEMORY.md").read_text(encoding="utf-8") == ""
    assert store.entries()["deploy_target"].status == Status.RETIRED
    assert (folder / ".remcycle" / "withheld" / "deploy_target.md").read_bytes() == before
    assert store.queue() == []


def test_an_entry_is_found_by_its_name_an_alias_or_its_words_in_that_order(folder):
    store = MemoryStore(folder)
    store.apply(
        [
            Add(claim("package-manager", "Use pnpm for JS projects.")),
            Alias("package-manager", "js-tooling-choice"),
            Add(claim("ci-runner", "The package manager cache lives on the self-hosted CI runner.")),
        ]
    )

    def found(query):
        return [(hit.slot, hit.matched) for hit in store.find(query)]

    assert found("package manager") == [("package-manager", "name"), ("ci-runner", "words")]
    assert found("JS tooling choice") == [("package-manager", "alias")]
    assert found("pnpm projects") == [("package-manager", "words")]
    assert found("staging cluster") == [("deploy_target", "words")]
    assert found("kubernetes") == []


def test_a_demoted_entry_leaves_the_index_but_stays_on_file_until_the_person_rules(folder):
    store = MemoryStore(folder)

    store.apply([Demote("deploy_target", "a session was corrected after relying on it")])
    store.ensure_indexed()

    assert (folder / "deploy_target.md").exists()
    assert (folder / "MEMORY.md").read_text(encoding="utf-8") == ""
    assert [(item.slot, item.withheld, item.reason) for item in store.queue()] == [
        ("deploy_target", False, "a session was corrected after relying on it")
    ]

    store.resolve("deploy_target", accept=False)

    assert "](deploy_target.md)" in (folder / "MEMORY.md").read_text(encoding="utf-8")


def topical(slot, statement, topic):
    return Claim(**{**claim(slot, statement).__dict__, "topic": topic})


@pytest.fixture
def busy(folder):
    """A store with more entries than a session should have to pick from at once."""
    store = MemoryStore(folder, topics_after=3)
    store.apply(
        [
            Add(topical("package-manager", "Use pnpm for JS projects.", "Tooling")),
            Add(topical("formatter", "Format with ruff.", "Tooling")),
            Add(topical("ci-runner", "CI is self-hosted.", "Deploys and CI")),
        ]
    )
    store.set_topic("deploy_target", "Deploys and CI")
    return store


def test_past_a_threshold_the_index_lists_topics_and_each_topic_page_holds_its_lines_unchanged(folder, busy):
    busy.fit_index(max_lines=190, max_bytes=23_000)

    assert (folder / "MEMORY.md").read_text(encoding="utf-8") == (
        "- [Deploys and CI](_topic-deploys-and-ci.md) — 2 memories: Deploy target, Ci runner\n"
        "- [Tooling](_topic-tooling.md) — 2 memories: Package manager, Formatter\n"
    )
    assert (folder / "_topic-deploys-and-ci.md").read_text(encoding="utf-8") == (
        "# Deploys and CI\n\n" + LEGACY_INDEX + "- [Ci runner](ci-runner.md) — CI is self-hosted.\n"
    )
    assert sorted(busy.entries()) == ["ci-runner", "deploy_target", "formatter", "package-manager"]


def test_once_the_index_is_by_topic_a_new_entry_goes_onto_its_topics_page(folder, busy):
    busy.fit_index(max_lines=190, max_bytes=23_000)

    busy.apply([Add(topical("linter", "Lint with ruff too.", "Tooling"))])

    assert "- [Linter](linter.md) — Lint with ruff too.\n" in (folder / "_topic-tooling.md").read_text(encoding="utf-8")
    assert "3 memories: Package manager, Formatter, Linter" in (folder / "MEMORY.md").read_text(encoding="utf-8")


def test_a_line_a_session_adds_to_the_topic_index_is_kept_as_that_entrys_line(folder, busy):
    busy.fit_index(max_lines=190, max_bytes=23_000)
    before = (folder / "MEMORY.md").read_text(encoding="utf-8")

    busy.absorb(before, before + "- [Release notes](release_notes.md) — written by hand each Friday\n")

    assert "- [Release notes](release_notes.md) — written by hand each Friday\n" in (folder / "_topic-other.md").read_text(encoding="utf-8")
    assert "[Other](_topic-other.md) — 1 memory: Release notes" in (folder / "MEMORY.md").read_text(encoding="utf-8")


@pytest.fixture
def written_by_claude(tmp_path):
    """A folder only Claude Code has written to: four memories, one index line each, nothing of remcycle's."""
    folder = tmp_path / "theirs"
    folder.mkdir()
    lines = []
    for slot, hook in [("deploy_target", "staging first"), ("ci_runner", "self-hosted"), ("formatter", "ruff"), ("linter", "ruff too")]:
        (folder / f"{slot}.md").write_text(LEGACY_FILE.replace("deploy-target", slot), encoding="utf-8", newline="\n")
        lines.append(f"- [{slot}]({slot}.md) — {hook}\n")
    (folder / "MEMORY.md").write_text("".join(lines), encoding="utf-8", newline="\n")
    return folder


def test_a_folder_claude_code_wrote_keeps_every_index_line_when_its_index_turns_into_topics(written_by_claude):
    store = MemoryStore(written_by_claude, topics_after=3)

    store.set_topic("deploy_target", "Deploys and CI")
    store.set_topic("ci_runner", "Deploys and CI")
    store.set_topic("formatter", "Tooling")
    store.set_topic("linter", "Tooling")
    store.fit_index(200, 25_000)

    assert (written_by_claude / "MEMORY.md").read_text(encoding="utf-8") == (
        "- [Deploys and CI](_topic-deploys-and-ci.md) — 2 memories: deploy_target, ci_runner\n"
        "- [Tooling](_topic-tooling.md) — 2 memories: formatter, linter\n"
    )
    assert (written_by_claude / "_topic-deploys-and-ci.md").read_text(encoding="utf-8") == (
        "# Deploys and CI\n\n- [deploy_target](deploy_target.md) — staging first\n- [ci_runner](ci_runner.md) — self-hosted\n"
    )
    assert (written_by_claude / "_topic-tooling.md").read_text(encoding="utf-8") == (
        "# Tooling\n\n- [formatter](formatter.md) — ruff\n- [linter](linter.md) — ruff too\n"
    )


def test_a_line_left_out_of_a_folder_claude_code_wrote_comes_back_when_there_is_room(written_by_claude):
    store = MemoryStore(written_by_claude)
    whole = (written_by_claude / "MEMORY.md").read_text(encoding="utf-8")

    left_out = store.fit_index(3, 25_000)
    assert len(left_out) == 1
    assert len((written_by_claude / "MEMORY.md").read_text(encoding="utf-8").splitlines()) == 3

    assert store.fit_index(200, 25_000) == []
    assert (written_by_claude / "MEMORY.md").read_text(encoding="utf-8") == whole


def test_a_closed_thread_is_remembered_with_who_closed_it_and_why_and_can_be_reopened(folder):
    store = MemoryStore(folder)
    store.note_threads([Thread("ci-cache", "CI cache for pnpm is not set up yet.", True, 4)], "session-a", "2026-10-01T09:00:00Z")

    store.close_thread(
        "ci-cache", by="session", session_id="session-b", why="The cache step is in the workflow now.", at="2026-10-02T09:00:00Z"
    )

    assert store.threads(now="2026-10-03T09:00:00Z") == {}
    assert store.closed(now="2026-10-03T09:00:00Z") == [
        Closed(
            "ci-cache",
            "CI cache for pnpm is not set up yet.",
            "2026-10-02T09:00:00Z",
            "session",
            "session-b",
            "The cache step is in the workflow now.",
        )
    ]
    assert store.closed(now="2026-10-20T09:00:00Z") == []

    store.reopen("ci-cache")

    assert store.threads(now="2026-10-03T09:00:00Z") == {"ci-cache": "CI cache for pnpm is not set up yet."}
    assert store.closed(now="2026-10-03T09:00:00Z") == []
    with pytest.raises(LookupError, match="ci-cache"):
        store.reopen("ci-cache")


def test_a_session_that_finishes_a_thread_closes_it_and_one_that_reports_it_open_again_reopens_it(folder):
    store = MemoryStore(folder)
    store.note_threads([Thread("ci-cache", "CI cache for pnpm is not set up yet.", True, 4)], "session-a", "2026-10-01T09:00:00Z")

    assert store.note_threads([Thread("ci-cache", "CI cache works.", False, 3)], "session-b", "2026-10-02T09:00:00Z") == []
    assert [(c.slot, c.by, c.session_id) for c in store.closed(now="2026-10-03T09:00:00Z")] == [("ci-cache", "dream", "session-b")]

    reopened = store.note_threads([Thread("ci-cache", "The cache misses on main.", True, 1)], "session-c", "2026-10-03T09:00:00Z")

    assert reopened == ["ci-cache"]
    assert store.threads(now="2026-10-04T09:00:00Z") == {"ci-cache": "The cache misses on main."}
    assert store.closed(now="2026-10-04T09:00:00Z") == []


@pytest.fixture
def crowded(tmp_path):
    """A folder only Claude Code has written to, with nine memories whose titles are long."""
    folder = tmp_path / "crowded"
    folder.mkdir()
    lines = []
    for n in range(8):
        slot = f"reference_pool_error_{n}"
        (folder / f"{slot}.md").write_text(LEGACY_FILE.replace("deploy-target", slot), encoding="utf-8", newline="\n")
        lines.append(f"- [Postgres pool error 57P01 handling, part {n}]({slot}.md) — retry once\n")
    (folder / "formatter.md").write_text(LEGACY_FILE.replace("deploy-target", "formatter"), encoding="utf-8", newline="\n")
    lines.append("- [Formatter](formatter.md) — ruff\n")
    (folder / "MEMORY.md").write_text("".join(lines), encoding="utf-8", newline="\n")
    store = MemoryStore(folder, topics_after=3)
    for n in range(8):
        store.set_topic(f"reference_pool_error_{n}", "Database")
    store.set_topic("formatter", "Tooling")
    return store


def test_a_topic_line_names_every_memory_while_the_index_has_room_and_fewer_when_it_does_not(crowded):
    index = crowded.folder / "MEMORY.md"

    crowded.fit_index(200, 25_000)
    (database,) = [line for line in index.read_text(encoding="utf-8").splitlines() if "Database" in line]
    assert all(f"Postgres pool error 57P01 handling, part {n}" in database for n in range(8))
    assert "…" not in database

    crowded.fit_index(200, 300)
    (database,) = [line for line in index.read_text(encoding="utf-8").splitlines() if "Database" in line]
    assert len(index.read_text(encoding="utf-8").encode()) <= 300
    assert "8 memories: Postgres pool error 57P01 handling, part 0" in database and database.endswith("…")
    assert len((crowded.folder / "_topic-database.md").read_text(encoding="utf-8").splitlines()) == 2 + 8


def test_an_index_kept_one_line_per_entry_stays_that_way_until_topics_are_allowed_again(crowded):
    index = crowded.folder / "MEMORY.md"
    crowded.fit_index(200, 25_000)
    assert crowded.by_topic()

    crowded.keep_flat()
    crowded.set_topic("formatter", "Formatting")

    assert not crowded.by_topic()
    assert len(index.read_text(encoding="utf-8").splitlines()) == 9 and "_topic-" not in index.read_text(encoding="utf-8")
    assert list(crowded.folder.glob("_topic-*.md")) == []

    crowded.allow_topics()
    crowded.fit_index(200, 25_000)

    assert crowded.by_topic()


def test_a_write_that_fails_part_way_leaves_the_file_as_it_was(folder, monkeypatch):
    store = MemoryStore(folder)
    store.note_threads([Thread("ci-cache", "CI cache is not set up.", True, 4)], "session-a", "2026-10-01T09:00:00Z")
    threads = folder / ".remcycle" / "threads.json"
    before = threads.read_text(encoding="utf-8")

    def fails(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fails)
    with pytest.raises(OSError, match="disk full"):
        store.note_threads([Thread("lint-rules", "Lint rules are not agreed.", True, 1)], "session-b", "2026-10-02T09:00:00Z")

    assert threads.read_text(encoding="utf-8") == before


def test_what_the_store_writes_is_utf8_with_unix_line_ends_on_every_system(folder):
    said = "Releases go out on Thursdays — never Fridays, naïvely or not."

    MemoryStore(folder).apply([Add(claim("release-day", said))])

    assert (folder / "MEMORY.md").read_bytes() == (LEGACY_INDEX + f"- [Release day](release-day.md) — {said}\n").encode()
    written = (folder / "release-day.md").read_bytes()
    assert said.encode() in written and b"\r" not in written


def test_a_write_refused_while_a_reader_has_the_file_open_is_made_once_the_reader_lets_go(folder, monkeypatch):
    replace, refused = os.replace, []

    def refused_twice(source, target):
        if len(refused) < 2:
            refused.append(target)
            raise PermissionError("another process has the file open")
        replace(source, target)

    monkeypatch.setattr(dream.disk, "PATIENCE", 5.0)
    monkeypatch.setattr(os, "replace", refused_twice)

    MemoryStore(folder).apply([Add(claim("release-day", "Releases go out on Thursdays."))])

    assert len(refused) == 2
    assert "release-day" in MemoryStore(folder).entries()
