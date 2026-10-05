import pytest

from dream.claims import Claim, ClaimType, Evidence, Provenance, Scope, Status
from dream.extract import Thread
from dream.memory import MemoryStore
from dream.reconcile import Add, Confirm, Contest, Question, Supersede

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
    (folder / "deploy_target.md").write_text(LEGACY_FILE)
    (folder / "MEMORY.md").write_text(LEGACY_INDEX)
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

    assert (folder / "package-manager.md").read_text() == (
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
    assert (folder / "MEMORY.md").read_text() == (
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

    assert "Deploys go straight to production." in (folder / "deploy_target.md").read_text()
    assert "staging" not in (folder / "deploy_target.md").read_text()
    assert (folder / "MEMORY.md").read_text() == "- [Deploy target](deploy_target.md) — Deploys go straight to production.\n"
    assert store.entries()["deploy_target"].provenance == Provenance.HUMAN
    assert [past.statement for past in store.history("deploy_target")] == [
        "Deploys go to the staging cluster first: never straight to production."
    ]


def test_a_contested_entry_is_withheld_from_sessions_and_put_to_the_person(folder):
    store = MemoryStore(folder)
    seen = claim("deploy_target", "Deploys go straight to production.", by=Provenance.OBSERVED, type=ClaimType.FACT)

    store.apply([Contest("deploy_target", seen)])

    assert not (folder / "deploy_target.md").exists()
    assert (folder / "MEMORY.md").read_text() == ""
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
    assert (folder / "MEMORY.md").read_text() == LEGACY_INDEX


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


def test_ruling_for_the_existing_entry_puts_a_withheld_one_back_as_it_was(folder):
    store = MemoryStore(folder)
    before = (folder / "deploy_target.md").read_bytes()
    store.apply([Contest("deploy_target", claim("deploy_target", "Deploys go straight to production.", by=Provenance.OBSERVED, type=ClaimType.FACT))])

    store.resolve("deploy_target", accept=False)

    assert (folder / "deploy_target.md").read_bytes() == before
    assert "](deploy_target.md)" in (folder / "MEMORY.md").read_text()
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
    (repo / "infra" / "deploy.sh").write_text("#!/bin/sh\n")
    store = MemoryStore(folder)
    fact = Claim(**{**claim("deploy-script", "Deploys run through infra/deploy.sh.", type=ClaimType.FACT).__dict__, "anchor": "infra/deploy.sh"})
    store.apply([Add(fact)])

    assert store.note_read("deploy-script", repo, "2026-10-03T09:00:00Z") is None
    (repo / "infra" / "deploy.sh").unlink()
    warning = store.note_read("deploy-script", repo, "2026-10-04T09:00:00Z")

    assert warning == "This memory is about infra/deploy.sh, which no longer exists in the repository. Check before relying on it."
    assert store.reads("deploy-script") == 2


def test_an_index_over_its_budget_sheds_the_oldest_unread_legacy_lines_and_keeps_every_file(folder):
    for n, modified in enumerate(["2026-05-01", "2026-09-01", "2026-07-01"]):
        (folder / f"note-{n}.md").write_text(
            f'---\nname: note-{n}\ndescription: "Note {n}."\nmetadata:\n  type: project\n  modified: {modified}T00:00:00Z\n---\n\nNote {n}.\n'
        )
    (folder / "MEMORY.md").write_text(
        "- [Note 0](note-0.md) — n0\n- [Note 1](note-1.md) — n1\n- [Note 2](note-2.md) — n2\n"
    )
    store = MemoryStore(folder)
    store.apply([Add(claim("package-manager", "Use pnpm for JS projects."))])
    store.note_read("note-0", folder, "2026-10-01T09:00:00Z")

    shed = store.fit_index(max_lines=2, max_bytes=10_000)

    assert shed == ["note-2", "note-1"]
    assert (folder / "MEMORY.md").read_text() == (
        "- [Note 0](note-0.md) — n0\n- [Package manager](package-manager.md) — Use pnpm for JS projects.\n"
    )
    assert sorted(p.name for p in folder.glob("note-*.md")) == ["note-0.md", "note-1.md", "note-2.md"]
