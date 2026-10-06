import errno
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

import dream.disk
import dream.dreaming
from dream.archive import Archive
from dream.claims import Claim, ClaimType, Evidence, Provenance, Scope, Status
from dream.dreaming import dream as run_dream
from dream.dreaming import publish_project, read_ahead, review_project
from dream.extract import ExtractionError, Reply, Thread
from dream.memory import MemoryStore
from dream.mirror import Mirror
from dream.outside import Finished, Witnessed
from dream.reconcile import Add, Confirm, Review, Withhold
from dream.session import context, status
from support import assistant_text, human, put_session

PROJECT = "/work/shop"
LEGACY = """---
name: deploy-target
description: "Deploys go to the staging cluster first."
metadata:
  type: project
---

Deploys go to the staging cluster first.
"""


@pytest.fixture
def claude(tmp_path):
    """Claude Code's projects folder, holding one project's live memory."""
    root = tmp_path / "projects"
    memory = root / "-work-shop" / "memory"
    memory.mkdir(parents=True)
    (memory / "deploy-target.md").write_text(LEGACY, encoding="utf-8", newline="\n")
    (memory / "MEMORY.md").write_text("- [Deploy target](deploy-target.md) — staging first\n", encoding="utf-8", newline="\n")
    return root


@pytest.fixture
def archive(tmp_path):
    with Archive(tmp_path / "archive.db") as archive:
        yield archive


def pnpm_session(root, session_id="s-pnpm"):
    put_session(
        root,
        session_id,
        [
            human("set up the js workspace", 0),
            assistant_text("I'll use npm.", 1),
            human("no, always use pnpm for JS projects here", 2),
        ],
        cwd=PROJECT,
    )


PNPM = {
    "slot": "package-manager",
    "type": "preference",
    "scope": "project",
    "statement": "Use pnpm for JS projects.",
    "why": "",
    "provenance": "human",
    "first_turn": 2,
    "last_turn": 2,
    "quote": "always use pnpm for JS projects",
    "anchor": "",
}


class Model:
    """A stand-in for the model: answers with PNPM whenever the session mentions pnpm."""

    def __init__(self):
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        claims = [PNPM] if "always use pnpm" in prompt.split("## Session")[1] else []
        return Reply({"summary": "A session.", "claims": claims, "threads": []}, cost_usd=0.02)


def dreamt(archive, claude, tmp_path, model, **options):
    archive.ingest(claude)
    return run_dream(archive, memory_root=tmp_path / "memory", live_root=claude, runner=model, **options)


def test_a_dream_adds_what_a_session_established_to_its_own_copy_and_leaves_live_memory_alone(
    archive, claude, tmp_path
):
    pnpm_session(claude)
    model = Model()
    live = claude / "-work-shop" / "memory"
    live_before = {p.name: p.read_bytes() for p in live.iterdir()}

    report = dreamt(archive, claude, tmp_path, model)

    copy = tmp_path / "memory" / "-work-shop"
    assert sorted(MemoryStore(copy).entries()) == ["deploy-target", "package-manager"]
    assert (copy / "MEMORY.md").read_text(encoding="utf-8") == (
        "- [Deploy target](deploy-target.md) — staging first\n"
        "- [Package manager](package-manager.md) — Use pnpm for JS projects.\n"
    )
    assert {p.name: p.read_bytes() for p in live.iterdir()} == live_before
    (project,) = report.projects
    assert (project.project, project.added, project.merged, project.published) == (PROJECT, 1, True, False)
    assert report.cost_usd == 0.02

    again = dreamt(archive, claude, tmp_path, model)
    assert (model.calls, again.projects) == (1, [])


def test_publishing_writes_the_accepted_memory_to_live_and_nothing_else(archive, claude, tmp_path):
    pnpm_session(claude)
    live = claude / "-work-shop" / "memory"

    report = dreamt(archive, claude, tmp_path, Model(), publish=True)

    assert report.projects[0].published is True
    assert sorted(p.name for p in live.iterdir()) == ["MEMORY.md", "deploy-target.md", "package-manager.md"]
    assert "package-manager.md" in (live / "MEMORY.md").read_text(encoding="utf-8")


def test_a_memory_a_session_wrote_since_the_last_dream_is_taken_in_and_nothing_the_dream_added_is_lost(
    archive, claude, tmp_path
):
    pnpm_session(claude)
    dreamt(archive, claude, tmp_path, Model())
    live = claude / "-work-shop" / "memory"
    (live / "ci-runner.md").write_text(LEGACY.replace("deploy-target", "ci-runner").replace("Deploys go to the staging cluster first", "CI is self-hosted"), encoding="utf-8", newline="\n")
    (live / "MEMORY.md").write_text((live / "MEMORY.md").read_text(encoding="utf-8") + "- [CI runner](ci-runner.md) — self-hosted\n", encoding="utf-8", newline="\n")
    put_session(claude, "s-later", [human("nothing worth keeping", 0)], cwd=PROJECT)

    dreamt(archive, claude, tmp_path, Model())

    copy = tmp_path / "memory" / "-work-shop"
    assert sorted(MemoryStore(copy).entries()) == ["ci-runner", "deploy-target", "package-manager"]
    assert (copy / "MEMORY.md").read_text(encoding="utf-8") == (
        "- [Deploy target](deploy-target.md) — staging first\n"
        "- [CI runner](ci-runner.md) — self-hosted\n"
        "- [Package manager](package-manager.md) — Use pnpm for JS projects.\n"
    )


def test_a_session_the_model_could_not_read_stays_unread_and_the_rest_carry_on(archive, claude, tmp_path):
    put_session(claude, "s-broken", [human("this one upsets the model", 0)], cwd=PROJECT)
    pnpm_session(claude)

    class Flaky(Model):
        def __call__(self, prompt):
            if "upsets the model" in prompt:
                raise ExtractionError("Claude Code gave no structured answer")
            return super().__call__(prompt)

    report = dreamt(archive, claude, tmp_path, Flaky())

    assert report.failures == [("s-broken", "Claude Code gave no structured answer")]
    assert report.projects[0].added == 1
    assert [s.session_id for s in archive.awaiting_dream()] == ["s-broken"]


def test_memory_the_gate_refuses_is_not_accepted_and_its_sessions_stay_unread(archive, claude, tmp_path, monkeypatch):
    pnpm_session(claude)
    monkeypatch.setattr(dream.dreaming, "check", lambda live, staged, judge: ["MEMORY.md has 203 lines"])

    report = dreamt(archive, claude, tmp_path, Model())

    (project,) = report.projects
    assert (project.merged, project.problems) == (False, ["MEMORY.md has 203 lines"])
    assert sorted(MemoryStore(tmp_path / "memory" / "-work-shop").entries()) == ["deploy-target"]
    assert [s.session_id for s in archive.awaiting_dream()] == ["s-pnpm"]


def test_nothing_is_published_if_live_memory_changed_while_the_dream_ran(archive, claude, tmp_path):
    pnpm_session(claude)
    live = claude / "-work-shop" / "memory"

    class WritesMeanwhile(Model):
        def __call__(self, prompt):
            (live / "written-meanwhile.md").write_text(LEGACY, encoding="utf-8", newline="\n")
            return super().__call__(prompt)

    report = dreamt(archive, claude, tmp_path, WritesMeanwhile(), publish=True)

    (project,) = report.projects
    assert (project.merged, project.published) == (True, False)
    assert project.problems == ["live memory changed during the dream, so nothing was published"]
    assert not (live / "package-manager.md").exists()


def test_only_the_persons_own_words_make_a_claim_apply_to_every_project(archive, claude, tmp_path):
    pnpm_session(claude)

    class Everywhere(Model):
        def __call__(self, prompt):
            reply = super().__call__(prompt)
            stated = {**PNPM, "scope": "global"}
            guessed = {**PNPM, "slot": "node-version", "statement": "Use Node 22.", "scope": "global",
                       "type": "lesson", "provenance": "inferred", "first_turn": 1, "last_turn": 1, "quote": "I'll use npm."}
            return Reply({**reply.data, "claims": [stated, guessed]}, reply.cost_usd)

    dreamt(archive, claude, tmp_path, Everywhere())

    assert sorted(MemoryStore(tmp_path / "memory" / "-global-").entries()) == ["package-manager"]
    assert "node-version" in MemoryStore(tmp_path / "memory" / "-work-shop").entries()


def test_a_session_the_model_has_already_read_is_not_sent_again_when_its_memory_was_refused(
    archive, claude, tmp_path, monkeypatch
):
    pnpm_session(claude)
    model = Model()
    with monkeypatch.context() as patched:
        patched.setattr(dream.dreaming, "check", lambda live, staged, judge: ["MEMORY.md has 203 lines"])
        dreamt(archive, claude, tmp_path, model)

    report = dreamt(archive, claude, tmp_path, model)

    assert model.calls == 1
    assert (report.projects[0].added, report.cost_usd) == (1, 0.0)


def test_a_new_session_is_given_what_applies_everywhere_and_what_this_project_left_open(archive, claude, tmp_path):
    pnpm_session(claude)

    class Model2(Model):
        def __call__(self, prompt):
            reply = super().__call__(prompt)
            thread = {"slot": "ci-cache", "state": "open", "statement": "CI cache for pnpm is not set up.", "turn": 2}
            return Reply({**reply.data, "claims": [{**PNPM, "scope": "global"}], "threads": [thread]}, reply.cost_usd)

    dreamt(archive, claude, tmp_path, Model2(), now="2026-10-02T12:00:00+00:00")

    given = context(tmp_path / "memory", PROJECT, now="2026-10-03T09:00:00+00:00", live_root=claude)
    assert (given["everywhere"], given["threads"]) == (
        ["Use pnpm for JS projects."],
        [{"slot": "ci-cache", "statement": "CI cache for pnpm is not set up."}],
    )
    elsewhere = context(tmp_path / "memory", "/work/other", now="2026-10-03T09:00:00+00:00", live_root=claude)
    assert (elsewhere["everywhere"], elsewhere["threads"], elsewhere["learned"]) == (["Use pnpm for JS projects."], [], [])
    assert status(tmp_path / "memory", PROJECT, now="2026-10-03T09:00:00+00:00")["open_threads"] == [
        {"slot": "ci-cache", "statement": "CI cache for pnpm is not set up.", "seen_at": "2026-10-01T09:00:02.000Z"}
    ]


def test_a_new_session_is_handed_what_the_dream_learned_that_claude_codes_own_memory_lacks(archive, claude, tmp_path):
    pnpm_session(claude)
    put_session(claude, "s-ci", [human("our CI is self-hosted, remember that", 0)], cwd=PROJECT)
    ci = {**PNPM, "slot": "ci-runner", "statement": "CI is self-hosted.", "first_turn": 0, "last_turn": 0,
          "quote": "our CI is self-hosted, remember that"}
    hunch = {**PNPM, "slot": "cache-hunch", "type": "lesson", "provenance": "inferred", "statement": "The cache may be cold on Mondays.",
             "first_turn": 1, "last_turn": 1, "quote": "I'll use npm."}

    class Learns(Model):
        def __call__(self, prompt):
            reply = super().__call__(prompt)
            session = prompt.split("## Session")[1]
            claims = [PNPM, hunch] if "always use pnpm" in session else [ci]
            return Reply({**reply.data, "claims": claims}, reply.cost_usd)

    dreamt(archive, claude, tmp_path, Learns())
    copy = tmp_path / "memory" / "-work-shop"

    given = context(tmp_path / "memory", PROJECT, now="2026-10-03T09:00:00+00:00", live_root=claude)

    # Newest first: the person spoke of pnpm two seconds after the other session spoke of CI.
    assert given["learned"] == [
        {"slot": "package-manager", "statement": "Use pnpm for JS projects."},
        {"slot": "ci-runner", "statement": "CI is self-hosted."},
    ]
    assert (given["learned_in"], given["learned_more"]) == (str(copy), 0)

    tight = context(tmp_path / "memory", PROJECT, now="2026-10-03T09:00:00+00:00", live_root=claude, room=30)
    assert (tight["learned"], tight["learned_more"]) == (
        [{"slot": "package-manager", "statement": "Use pnpm for JS projects."}],
        1,
    )

    (claude / "-work-shop" / "memory" / "package-manager.md").write_text((copy / "package-manager.md").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    after = context(tmp_path / "memory", PROJECT, now="2026-10-03T09:00:00+00:00", live_root=claude)
    assert after["learned"] == [{"slot": "ci-runner", "statement": "CI is self-hosted."}]


def test_a_new_session_is_handed_what_is_new_first_and_then_what_has_been_needed_most(tmp_path):
    def said(slot, statement, at):
        return Claim(slot, ClaimType.PREFERENCE, Scope.PROJECT, statement, "", Provenance.HUMAN, Evidence("s-first", 0, 0), at)

    store = MemoryStore(tmp_path / "memory" / "-work-shop")
    store.folder.mkdir(parents=True)
    store.apply(
        [
            Add(said("old-once", "Old, said once.", "2026-08-01T09:00:00Z")),
            Add(said("old-again", "Old, said again.", "2026-07-01T09:00:00Z")),
            Add(said("fresh", "Fresh, said once.", "2026-10-01T09:00:00Z")),
        ]
    )
    store.apply([Confirm("old-again", Evidence("s-later", 1, 1), "2026-07-20T09:00:00Z")])

    def handed(**limits):
        given = context(tmp_path / "memory", PROJECT, now="2026-10-06T09:00:00+00:00", live_root=tmp_path / "claude", **limits)
        return [memory["slot"] for memory in given["learned"]], given["learned_more"]

    assert handed() == (["fresh", "old-again", "old-once"], 0)
    # Room for the first two statements only: what was said once, long ago, is what stays behind.
    assert handed(room=40) == (["fresh", "old-again"], 1)

    # A busy fortnight does not crowd out what has come up in session after session.
    store.apply([Confirm("old-again", Evidence(f"s-{n}", 1, 1), "2026-08-01T09:00:00Z") for n in range(3)])
    store.apply([Add(said(f"fresh-{n}", f"Fresh note {n}, said once.", f"2026-10-0{n + 2}T09:00:00Z")) for n in range(3)])
    assert handed(room=40) == (["old-again", "fresh-2"], 4)


def test_a_look_up_counted_while_the_dream_is_at_work_is_kept(tmp_path):
    pnpm = Claim("package-manager", ClaimType.PREFERENCE, Scope.PROJECT, "Use pnpm for JS projects.", "", Provenance.HUMAN,
                 Evidence("s-first", 0, 0), "2026-10-01T09:00:00Z")
    mirror = Mirror(tmp_path / "memory" / "-work-shop")
    mirror.sync(None, PROJECT)
    MemoryStore(mirror.stage()).apply([Add(pnpm)])
    mirror.accept("dream: 1 sessions")

    staged = MemoryStore(mirror.stage())
    MemoryStore(mirror.folder).note_found(["package-manager"], "2026-10-06T09:00:00Z")
    # The copy being worked on ranks by the same count, look-ups made since it was staged included.
    assert staged.reads("package-manager") == 1
    staged.apply([Confirm("package-manager", Evidence("s-later", 1, 1), "2026-10-05T09:00:00Z")])
    mirror.accept("dream: 1 sessions")

    assert MemoryStore(mirror.folder).reads("package-manager") == 1
    assert MemoryStore(mirror.folder).needed("package-manager") == 3


@pytest.mark.parametrize(
    ("dies_at", "holds"),
    [
        ("the first rename", {"package-manager"}),
        ("the second rename", {"package-manager", "ci-runner"}),
        ("the third rename", {"package-manager", "ci-runner"}),
        ("clearing the old content", {"package-manager", "ci-runner"}),
    ],
)
def test_an_accept_that_is_cut_short_leaves_a_whole_copy_and_is_finished_by_the_next_use(tmp_path, monkeypatch, dies_at, holds):
    def said(slot, statement):
        return Claim(slot, ClaimType.PREFERENCE, Scope.PROJECT, statement, "", Provenance.HUMAN, Evidence("s-first", 0, 0), "2026-10-01T09:00:00Z")

    mirror = Mirror(tmp_path / "memory" / "-work-shop")
    mirror.sync(None, PROJECT)
    MemoryStore(mirror.stage()).apply([Add(said("package-manager", "Use pnpm for JS projects."))])
    mirror.accept("dream: 1 sessions")
    MemoryStore(mirror.stage()).apply([Add(said("ci-runner", "CI is self-hosted."))])

    renames = []
    rename, rmtree = os.rename, shutil.rmtree

    def dying_rename(source, target):
        renames.append(source)
        nth = ("first", "second", "third", "a later")[min(len(renames), 4) - 1]
        if dies_at == f"the {nth} rename":
            raise OSError("the process was killed")
        rename(source, target)

    def dying_rmtree(folder, *args, **kwargs):
        if dies_at == "clearing the old content":
            raise OSError("the process was killed")
        rmtree(folder, *args, **kwargs)

    monkeypatch.setattr(os, "rename", dying_rename)
    monkeypatch.setattr(shutil, "rmtree", dying_rmtree)
    with pytest.raises(OSError, match="killed"):
        mirror.accept("dream: 1 sessions")
    monkeypatch.undo()

    # Whatever the moment, a reader finds a whole copy: the old one or the new one, never part of one.
    assert set(MemoryStore(mirror.folder).entries()) in ({"package-manager"}, {"package-manager", "ci-runner"}, set())

    after = Mirror(tmp_path / "memory" / "-work-shop")
    after.sync(None, PROJECT)

    assert set(MemoryStore(after.folder).entries()) == holds
    assert after.last("sync") is not None
    assert not (tmp_path / "memory" / "-work-shop.retired").exists()


def test_status_lists_what_the_dream_holds_for_a_project_and_what_waits_on_the_person(archive, claude, tmp_path):
    pnpm_session(claude)

    class Disagrees(Model):
        def __call__(self, prompt):
            reply = super().__call__(prompt)
            guess = {**PNPM, "slot": "deploy-target", "statement": "Deploys go straight to production.",
                     "type": "decision", "provenance": "inferred", "first_turn": 1, "last_turn": 1, "quote": "I'll use npm."}
            return Reply({**reply.data, "claims": [PNPM, guess]}, reply.cost_usd)

    dreamt(archive, claude, tmp_path, Disagrees())

    state = status(tmp_path / "memory", PROJECT, now="2026-10-03T09:00:00+00:00")
    assert state["withheld"] == 0
    assert state["memories"] == [
        {"slot": "deploy-target", "statement": "Deploys go to the staging cluster first.", "from": None, "said_at": None},
        {"slot": "package-manager", "statement": "Use pnpm for JS projects.", "from": "human",
         "said_at": "2026-10-01T09:00:02.000Z"},
    ]
    index = (
        "- [Deploy target](deploy-target.md) — staging first\n"
        "- [Package manager](package-manager.md) — Use pnpm for JS projects.\n"
    )
    assert state["index"] == {"lines": 2, "line_limit": 200, "bytes": len(index.encode()), "byte_limit": 25_000}
    assert state["elsewhere"] == []
    assert status(tmp_path / "memory", "/work/other", now="2026-10-03T09:00:00+00:00")["elsewhere"] == [
        {"project": "/work/shop", "waiting": 1}
    ]
    assert state["waiting"] == [
        {"slot": "deploy-target", "holds": "Deploys go to the staging cluster first.",
         "suggests": "Deploys go straight to production.", "reasons": [], "from": "inferred",
         "withheld": False, "evidence": "dream show s-pnpm --first 1 --last 1",
         "file": str(tmp_path / "memory" / "-work-shop" / "deploy-target.md")}
    ]
    in_claude_code = status(tmp_path / "memory", PROJECT, now="2026-10-03T09:00:00+00:00", live_root=claude)
    assert in_claude_code["waiting"][0]["file"] == str(claude / "-work-shop" / "memory" / "deploy-target.md")

    copy = MemoryStore(tmp_path / "memory" / "-work-shop")
    copy.apply([Review("deploy-target", "it looks dated: the cutover is over"), Review("deploy-target", "it repeats `x`: same rule")])
    (question,) = status(tmp_path / "memory", PROJECT, now="2026-10-03T09:00:00+00:00")["waiting"]
    # A ruling acts on the newest thing waiting, so that is what the question puts to the person.
    assert (question["slot"], question["suggests"]) == ("deploy-target", None)
    assert question["reasons"] == ["it looks dated: the cutover is over", "it repeats `x`: same rule"]


def test_a_thread_the_repository_shows_was_finished_outside_any_session_is_closed(archive, claude, tmp_path):
    repo = tmp_path / "shop"
    repo.mkdir()
    put_session(claude, "s-open", [human("the CI cache for pnpm still needs setting up", 0)], cwd=str(repo))
    ci_cache = {"slot": "ci-cache", "state": "open", "statement": "CI cache for pnpm is not set up.", "turn": 0}
    lint = {"slot": "lint-rules", "state": "open", "statement": "Lint rules are not agreed.", "turn": 0}

    class LeavesThreads(Model):
        def __call__(self, prompt):
            reply = super().__call__(prompt)
            return Reply({**reply.data, "claims": [], "threads": [ci_cache, lint]}, reply.cost_usd)

    asked = []

    def witness(repository, threads):
        asked.append((repository, [(thread.slot, thread.checked_to) for thread in threads]))
        if len(asked) > 1:
            return Witnessed()
        return Witnessed([Finished("ci-cache", "commit 3f2a9c1", "CI cache for pnpm now restores")], True, 0.02)

    first = dreamt(archive, claude, tmp_path, LeavesThreads(), witness=witness, now="2026-10-05T03:30:00+00:00")
    copy = MemoryStore(tmp_path / "memory" / dream.dreaming.key(str(repo)))

    assert asked == [(repo, [("ci-cache", None), ("lint-rules", None)])]
    assert copy.threads(now="2026-10-05T04:00:00+00:00") == {"lint-rules": "Lint rules are not agreed."}
    assert first.projects[0].closed == [("ci-cache", "commit 3f2a9c1")]
    assert "Closed `ci-cache`: finished by commit 3f2a9c1" in dream.dreaming.render(first)
    assert first.cost_usd == pytest.approx(0.04)

    dreamt(archive, claude, tmp_path, LeavesThreads(), witness=witness, now="2026-10-06T03:30:00+00:00")

    assert asked[1] == (repo, [("lint-rules", "2026-10-05T03:30:00+00:00")])


def test_a_thread_a_session_closed_is_checked_against_that_sessions_own_transcript(archive, claude, tmp_path):
    put_session(claude, "s-open", [human("the CI cache for pnpm still needs setting up", 0)], cwd=PROJECT)
    ci_cache = {"slot": "ci-cache", "state": "open", "statement": "CI cache for pnpm is not set up.", "turn": 0}
    lint = {"slot": "lint-rules", "state": "open", "statement": "Lint rules are not agreed.", "turn": 0}

    class Reads(Model):
        """Leaves two threads open after the first session; of the later ones, says only `ci-cache` is still open."""

        def __init__(self):
            super().__init__()
            self.shown = []

        def __call__(self, prompt):
            reply = super().__call__(prompt)
            self.shown.append(prompt.split("## Open threads")[1].split("##")[0])
            threads = [ci_cache, lint] if self.calls == 1 else [{**ci_cache, "statement": "The cache step was only sketched."}]
            return Reply({**reply.data, "claims": [], "threads": threads}, reply.cost_usd)

    model = Reads()
    dreamt(archive, claude, tmp_path, model, now="2026-10-02T03:30:00+00:00")
    copy = MemoryStore(tmp_path / "memory" / "-work-shop")
    for slot, why in (("ci-cache", "The cache step is in the workflow now."), ("lint-rules", "Agreed on the ruff defaults.")):
        copy.close_thread(slot, by="session", session_id="s-close", why=why, at="2026-10-02T15:00:00+00:00")
    put_session(claude, "s-close", [human("sketch the cache step, and take ruff's defaults for lint", 0)], cwd=PROJECT)

    report = dreamt(archive, claude, tmp_path, model, now="2026-10-03T03:30:00+00:00")

    assert "ci-cache" in model.shown[1] and "The cache step is in the workflow now." in model.shown[1]
    assert copy.threads(now="2026-10-03T04:00:00+00:00") == {"ci-cache": "The cache step was only sketched."}
    assert [c.slot for c in copy.closed(now="2026-10-03T04:00:00+00:00")] == ["lint-rules"]
    assert report.projects[0].reopened == ["ci-cache"]
    assert "Reopened `ci-cache`" in dream.dreaming.render(report)


def test_status_says_when_a_dream_last_changed_the_projects_memory(archive, claude, tmp_path):
    pnpm_session(claude)
    assert status(tmp_path / "memory", PROJECT, now="2026-10-03T09:00:00+00:00")["last_dream"] is None

    dreamt(archive, claude, tmp_path, Model())

    changed = datetime.fromisoformat(status(tmp_path / "memory", PROJECT, now="2026-10-03T09:00:00+00:00")["last_dream"])
    assert abs((datetime.now(UTC) - changed).total_seconds()) < 60


def test_a_fact_about_a_file_that_has_gone_is_withheld_by_the_next_dream_even_with_no_new_sessions(
    archive, claude, tmp_path
):
    repo = tmp_path / "shop"
    (repo / "infra").mkdir(parents=True)
    (repo / "infra" / "deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8", newline="\n")
    put_session(claude, "s-fact", [human("deploys always run through infra/deploy.sh, never by hand", 0)], cwd=str(repo))
    fact = {**PNPM, "slot": "deploy-script", "type": "fact", "statement": "Deploys run through infra/deploy.sh.",
            "first_turn": 0, "last_turn": 0, "quote": "deploys always run through infra/deploy.sh", "anchor": "infra/deploy.sh"}

    class KnowsTheScript(Model):
        def __call__(self, prompt):
            reply = super().__call__(prompt)
            return Reply({**reply.data, "claims": [fact]}, reply.cost_usd)

    dreamt(archive, claude, tmp_path, KnowsTheScript())
    copy = MemoryStore(tmp_path / "memory" / dream.dreaming.key(str(repo)))
    assert copy.entries()["deploy-script"].status == Status.ACTIVE

    (repo / "infra" / "deploy.sh").unlink()
    report = dreamt(archive, claude, tmp_path, KnowsTheScript())

    assert copy.entries()["deploy-script"].status == Status.STALE
    assert [item.reason for item in copy.queue()] == ["infra/deploy.sh, which it is about, is no longer in the repository"]
    assert report.projects[0].count(Withhold) == 1


def test_an_entry_the_person_corrected_the_assistant_for_following_is_demoted_and_put_to_them(
    archive, claude, tmp_path
):
    put_session(claude, "s-corrected", [human("why did you deploy to staging first? stop doing that", 0)], cwd=PROJECT)

    class SawACorrection(Model):
        def __call__(self, prompt):
            reply = super().__call__(prompt)
            correction = {"slot": "deploy-target", "turn": 0, "quote": "why did you deploy to staging first"}
            return Reply({**reply.data, "corrections": [correction]}, reply.cost_usd)

    dreamt(archive, claude, tmp_path, SawACorrection())

    copy = tmp_path / "memory" / "-work-shop"
    assert (copy / "deploy-target.md").exists()
    assert (copy / "MEMORY.md").read_text(encoding="utf-8") == ""
    assert [item.reason for item in MemoryStore(copy).queue()] == [
        "the person corrected the assistant after it followed this: dream show s-correc --first 0 --last 0"
    ]


def test_the_dream_puts_its_index_change_to_the_judge_before_accepting_it(archive, claude, tmp_path):
    asked = []

    def judge(index, questions):
        asked.append(list(questions))
        return ["none" for _ in questions]  # never finds anything, before or after: no regression

    class WithProbe(Model):
        def __call__(self, prompt):
            reply = super().__call__(prompt)
            session = prompt.split("## Session")[1]
            claims = [{**PNPM, "asks": "Which package manager do we use?"}] if "always use pnpm" in session else []
            if "self-hosted" in session:
                claims = [{**PNPM, "slot": "ci-runner", "statement": "CI is self-hosted.", "first_turn": 0,
                           "last_turn": 0, "quote": "our CI is self-hosted, remember that", "asks": "Where does CI run?"}]
            return Reply({**reply.data, "claims": claims}, reply.cost_usd)

    pnpm_session(claude)
    dreamt(archive, claude, tmp_path, WithProbe(), judge=judge)
    put_session(claude, "s-ci", [human("our CI is self-hosted, remember that", 0)], cwd=PROJECT)
    report = dreamt(archive, claude, tmp_path, WithProbe(), judge=judge)

    assert asked == [["Which package manager do we use?"], ["Which package manager do we use?"]]
    assert report.projects[0].merged is True


def test_reviewing_a_project_works_on_its_copy_and_leaves_live_memory_alone(claude, tmp_path):
    live = claude / "-work-shop" / "memory"
    live_before = {p.name: p.read_bytes() for p in live.iterdir()}

    def reviewer(prompt):
        note = {"slot": "deploy-target", "topic": "Deploys", "asks": "Where do deploys go first?",
                "dated": True, "quote": "staging cluster first", "reason": "production deploys are direct now"}
        return Reply({"memories": [note], "pairs": []}, cost_usd=0.03)

    report = review_project(PROJECT, memory_root=tmp_path / "memory", live_root=claude, runner=reviewer)

    copy = MemoryStore(tmp_path / "memory" / "-work-shop")
    assert (report.reviewed, report.questions, report.merged) == (1, 1, True)
    assert (copy.topics(), copy.probes()) == (["Deploys"], {"deploy-target": "Where do deploys go first?"})
    assert [item.reason for item in copy.queue()] == ["it looks dated: production deploys are direct now"]
    assert {p.name: p.read_bytes() for p in live.iterdir()} == live_before


def test_a_review_keeps_one_line_per_entry_when_the_index_by_topic_leads_to_fewer_memories(tmp_path):
    claude = tmp_path / "projects"
    live = claude / "-work-shop" / "memory"
    live.mkdir(parents=True)
    slots = [f"note_{n:02d}" for n in range(61)]
    for slot in slots:
        (live / f"{slot}.md").write_text(LEGACY.replace("deploy-target", slot), encoding="utf-8", newline="\n")
    (live / "MEMORY.md").write_text("".join(f"- [{slot}]({slot}.md) — a note\n" for slot in slots), encoding="utf-8", newline="\n")

    def reviewer(prompt):
        shown = [line[4:] for line in prompt.split("## Notes")[1].splitlines() if line.startswith("### ")]
        notes = [
            {"slot": slot, "topic": "Deploys" if int(slot[-2:]) % 2 else "Tooling", "asks": f"What does {slot} say?",
             "dated": False, "quote": "", "reason": ""}
            for slot in shown
        ]
        return Reply({"memories": notes, "pairs": []}, cost_usd=0.03)

    def prefers_entry_lines(index, questions):
        """A stand-in for the model: finds a memory only when the index names its file."""
        return [f"{q.split()[2]}.md" if f"({q.split()[2]}.md)" in index else "none" for q in questions]

    report = review_project(PROJECT, memory_root=tmp_path / "memory", live_root=claude, runner=reviewer, judge=prefers_entry_lines)

    copy = MemoryStore(tmp_path / "memory" / "-work-shop")
    assert (report.reviewed, report.merged, report.kept_flat) == (61, True, True)
    assert not copy.by_topic()
    assert (tmp_path / "memory" / "-work-shop" / "MEMORY.md").read_text(encoding="utf-8") == (live / "MEMORY.md").read_text(encoding="utf-8")
    assert (copy.topics(), len(copy.probes())) == (["Deploys", "Tooling"], 61)


def test_a_long_project_path_gets_the_folder_name_claude_code_gives_it():
    # A real pair: a path whose name passes 200 characters, and the folder Claude Code 2.1.286 made for it.
    path = (
        "/private/tmp/remcycle-example/an-organisation-with-a-long-name/a-team-with-a-long-name-of-its-own/"
        "a-product-line-with-several-words/a-service-in-that-product-line/a-worktree-for-one-branch-of-that-service/checkout"
    )

    assert dream.dreaming.key(path) == (
        "-private-tmp-remcycle-example-an-organisation-with-a-long-name-a-team-with-a-long-name-of-its-own-"
        "a-product-line-with-several-words-a-service-in-that-product-line-a-worktree-for-one-branch-of-that-ser-wpbyg6"
    )
    assert dream.dreaming.key("/work/shop") == "-work-shop"


def test_publishing_a_project_lists_the_change_first_and_writes_it_only_when_told(archive, claude, tmp_path):
    pnpm_session(claude)
    dreamt(archive, claude, tmp_path, Model())
    copy = MemoryStore(tmp_path / "memory" / "-work-shop")
    copy.apply([Review("deploy-target", "it looks dated: the cutover is over")])
    copy.resolve("deploy-target", accept=True)
    live = claude / "-work-shop" / "memory"
    before = {p.name: p.read_bytes() for p in live.iterdir()}
    where = {"memory_root": tmp_path / "memory", "live_root": claude, "backups": tmp_path / "backups"}

    plan = publish_project(PROJECT, **where, write=False)

    assert (plan.added, plan.replaced, plan.removed) == (
        ["package-manager.md"],
        ["MEMORY.md"],
        [("deploy-target.md", "you retired it")],
    )
    assert (plan.written, plan.live) == (False, live)
    assert {p.name: p.read_bytes() for p in live.iterdir()} == before

    done = publish_project(PROJECT, **where, write=True)

    assert done.written
    assert sorted(p.name for p in live.iterdir()) == ["MEMORY.md", "package-manager.md"]
    assert (live / "MEMORY.md").read_text(encoding="utf-8") == "- [Package manager](package-manager.md) — Use pnpm for JS projects.\n"
    assert {p.name: p.read_bytes() for p in done.backup.iterdir()} == before

    again = publish_project(PROJECT, **where, write=True)
    assert (again.added, again.replaced, again.removed, again.written) == ([], [], [], False)


def test_publishing_keeps_what_a_session_wrote_since_the_dream_last_looked(archive, claude, tmp_path):
    pnpm_session(claude)
    dreamt(archive, claude, tmp_path, Model())
    live = claude / "-work-shop" / "memory"
    (live / "release-notes.md").write_text(LEGACY.replace("deploy-target", "release-notes"), encoding="utf-8", newline="\n")
    with (live / "MEMORY.md").open("a", encoding="utf-8", newline="\n") as index:
        index.write("- [Release notes](release-notes.md) — written by hand each Friday\n")
    (live / "deploy-target.md").write_text(LEGACY.replace("staging cluster first", "production directly"), encoding="utf-8", newline="\n")

    done = publish_project(
        PROJECT, memory_root=tmp_path / "memory", live_root=claude, backups=tmp_path / "backups", write=True
    )

    assert done.removed == []
    assert "production directly" in (live / "deploy-target.md").read_text(encoding="utf-8")
    assert (live / "release-notes.md").exists()
    assert "- [Release notes](release-notes.md) — written by hand each Friday\n" in (live / "MEMORY.md").read_text(encoding="utf-8")
    assert "- [Package manager](package-manager.md)" in (live / "MEMORY.md").read_text(encoding="utf-8")


def test_only_a_project_with_a_folder_of_its_own_can_be_published(tmp_path):
    for nowhere in ("(no project)", "(global)"):
        with pytest.raises(LookupError, match="no folder"):
            publish_project(nowhere, memory_root=tmp_path / "memory", live_root=tmp_path, backups=tmp_path / "b", write=True)
    with pytest.raises(LookupError, match="holds no memory"):
        publish_project("/work/unknown", memory_root=tmp_path / "memory", live_root=tmp_path, backups=tmp_path / "b", write=True)


def test_a_dream_given_a_moment_reads_only_the_sessions_that_ended_after_it(archive, claude, tmp_path):
    for session_id, day in (("s-before", "2026-09-20"), ("s-after", "2026-10-03")):
        put_session(
            claude,
            session_id,
            [
                human("set up the js workspace", 0, timestamp=f"{day}T09:00:00.000Z"),
                assistant_text("I'll use npm.", 1, timestamp=f"{day}T09:00:01.000Z"),
                human("no, always use pnpm for JS projects here", 2, timestamp=f"{day}T09:00:02.000Z"),
            ],
            cwd=PROJECT,
        )
    model = Model()

    dreamt(archive, claude, tmp_path, model, since="2026-10-01T00:00:00+00:00")

    assert model.calls == 1
    assert [s.session_id for s in archive.awaiting_dream()] == ["s-before"]


def test_reading_ahead_asks_the_model_and_changes_no_memory_so_the_dream_after_it_asks_nothing(archive, claude, tmp_path):
    pnpm_session(claude)
    archive.ingest(claude)
    model = Model()

    ahead = read_ahead(archive, memory_root=tmp_path / "memory", runner=model)

    assert (model.calls, ahead.failures, ahead.cost_usd) == (1, [], 0.02)
    assert not (tmp_path / "memory").exists()

    def no_model(prompt):
        raise AssertionError("the dream asked the model again")

    report = run_dream(archive, memory_root=tmp_path / "memory", live_root=claude, runner=no_model, ask=False)

    assert sorted(MemoryStore(tmp_path / "memory" / "-work-shop").entries()) == ["deploy-target", "package-manager"]
    assert report.sessions == 1


def test_a_dream_told_not_to_ask_leaves_a_session_with_nothing_read_ahead_unread(archive, claude, tmp_path):
    pnpm_session(claude)
    archive.ingest(claude)

    def no_model(prompt):
        raise AssertionError("the dream asked the model")

    report = run_dream(archive, memory_root=tmp_path / "memory", live_root=claude, runner=no_model, ask=False)

    assert (report.sessions, report.failures) == (0, [])
    assert [s.session_id for s in archive.awaiting_dream()] == ["s-pnpm"]


@pytest.mark.parametrize("project", ["/work/shop", "C:\\work\\shop"])
def test_a_project_is_a_folder_however_its_system_writes_the_path(tmp_path, project):
    pnpm = Claim("package-manager", ClaimType.PREFERENCE, Scope.PROJECT, "Use pnpm for JS projects.", "", Provenance.HUMAN, Evidence("s-first", 0, 0), "2026-10-01T09:00:00Z")
    mirror = Mirror(tmp_path / "memory" / dream.dreaming.key(project))
    mirror.sync(None, project)
    store = MemoryStore(mirror.stage())
    store.apply([Add(pnpm), Review("package-manager", "it looks dated: the cutover is over")])
    mirror.accept("dream: 1 sessions")
    where = {"memory_root": tmp_path / "memory", "live_root": tmp_path / "projects"}

    given = context(tmp_path / "memory", project, now="2026-10-03T09:00:00+00:00", live_root=tmp_path / "projects")
    plan = publish_project(project, **where, backups=tmp_path / "backups", write=False)

    assert given["learned"] == [{"slot": "package-manager", "statement": "Use pnpm for JS projects."}]
    assert status(tmp_path / "memory", "/work/other", now="2026-10-03T09:00:00+00:00")["elsewhere"] == [{"project": project, "waiting": 1}]
    assert plan.added == ["MEMORY.md", "package-manager.md"]


def test_an_accept_refused_while_a_reader_has_a_file_of_the_copy_open_is_made_once_the_reader_lets_go(tmp_path, monkeypatch):
    pnpm = Claim("package-manager", ClaimType.PREFERENCE, Scope.PROJECT, "Use pnpm for JS projects.", "", Provenance.HUMAN, Evidence("s-first", 0, 0), "2026-10-01T09:00:00Z")
    mirror = Mirror(tmp_path / "memory" / "-work-shop")
    mirror.sync(None, PROJECT)
    MemoryStore(mirror.stage()).apply([Add(pnpm)])
    rename, refused = os.rename, []

    def refused_once_each(source, target):
        if source not in refused:
            refused.append(source)
            raise PermissionError("another process has a file in the folder open")
        rename(source, target)

    monkeypatch.setattr(dream.disk, "PATIENCE", 5.0)
    monkeypatch.setattr(os, "rename", refused_once_each)

    mirror.accept("dream: 1 sessions")

    assert len(refused) == 3
    assert set(MemoryStore(mirror.folder).entries()) == {"package-manager"}


SHOP_PNPM = Claim("package-manager", ClaimType.PREFERENCE, Scope.PROJECT, "Use pnpm for JS projects.", "", Provenance.HUMAN, Evidence("s-first", 0, 0), "2026-10-01T09:00:00Z")


def refusing(monkeypatch, module, name, folder):
    """Have `module.name` refuse the folder of that name, as Windows does while another program has a file in it open."""
    real = getattr(module, name)

    def held_open(source, *rest, **more):
        if Path(source).name == folder:
            raise PermissionError("another program has a file in the folder open")
        return real(source, *rest, **more)

    monkeypatch.setattr(dream.disk, "PATIENCE", 0.0)
    monkeypatch.setattr(module, name, held_open)


def test_an_accept_refused_for_good_leaves_the_copy_as_it_was_and_keeps_what_is_done_to_it_before_the_next(tmp_path, monkeypatch):
    mirror = Mirror(tmp_path / "memory" / "-work-shop")
    mirror.sync(None, PROJECT)
    copy = MemoryStore(mirror.folder)
    copy.note_threads([Thread("ci-cache", "CI cache for pnpm is not set up.", True, 4)], "s-first", "2026-10-01T09:00:00Z")
    MemoryStore(mirror.stage()).apply([Add(SHOP_PNPM)])

    refusing(monkeypatch, os, "rename", "-work-shop")
    with pytest.raises(PermissionError):
        mirror.accept("dream: 1 sessions")
    monkeypatch.undo()

    assert mirror.last("sync") is not None
    assert set(copy.entries()) == set()
    copy.close_thread("ci-cache", at="2026-10-02T09:00:00Z")  # as `dream close` does, before the next dream
    MemoryStore(mirror.stage()).apply([Add(SHOP_PNPM)])
    mirror.accept("dream: 1 sessions")

    assert copy.threads("2026-10-02T10:00:00Z") == {}
    assert set(copy.entries()) == {"package-manager"}


def test_a_project_whose_copy_another_program_holds_open_waits_and_the_other_projects_are_dreamt(archive, claude, tmp_path, monkeypatch):
    pnpm_session(claude)
    put_session(
        claude,
        "s-site",
        [human("set up the js workspace", 0), assistant_text("I'll use npm.", 1), human("no, always use pnpm for JS projects here", 2)],
        cwd="/work/site",
    )

    refusing(monkeypatch, os, "rename", "-work-shop")
    while_moving = dreamt(archive, claude, tmp_path, Model())
    monkeypatch.undo()
    refusing(monkeypatch, shutil, "rmtree", "-work-shop.staging")
    while_clearing = dreamt(archive, claude, tmp_path, Model())
    monkeypatch.undo()

    def shop(report):
        return next(project for project in report.projects if project.project == PROJECT)

    assert not shop(while_moving).merged and "could not be moved into place" in shop(while_moving).problems[0]
    assert not shop(while_clearing).merged and "could not be made ready" in shop(while_clearing).problems[0]
    assert next(project for project in while_moving.projects if project.project == "/work/site").merged
    assert [session.session_id for session in archive.awaiting_dream()] == ["s-pnpm"]

    dreamt(archive, claude, tmp_path, Model())

    assert archive.awaiting_dream() == []
    assert "package-manager" in MemoryStore(tmp_path / "memory" / "-work-shop").entries()


def test_what_a_session_said_for_every_project_is_not_lost_when_the_copy_for_all_cannot_take_it(archive, claude, tmp_path, monkeypatch):
    pnpm_session(claude)

    class SaidForAll(Model):
        def __call__(self, prompt):
            reply = super().__call__(prompt)
            return Reply({**reply.data, "claims": [{**PNPM, "scope": "global"}]}, reply.cost_usd)

    refusing(monkeypatch, os, "rename", dream.dreaming.key(dream.dreaming.GLOBAL))
    report = dreamt(archive, claude, tmp_path, SaidForAll())
    monkeypatch.undo()

    for_all = next(project for project in report.projects if project.project == dream.dreaming.GLOBAL)
    assert not for_all.merged and "could not be moved into place" in for_all.problems[0]
    assert [session.session_id for session in archive.awaiting_dream()] == ["s-pnpm"]

    dreamt(archive, claude, tmp_path, SaidForAll())

    assert archive.awaiting_dream() == []
    given = context(tmp_path / "memory", "/work/other", now="2026-10-03T09:00:00+00:00", live_root=claude)
    assert given["everywhere"] == ["Use pnpm for JS projects."]


def test_a_delete_refused_while_a_reader_has_the_file_open_is_made_once_the_reader_lets_go(tmp_path, monkeypatch):
    live = tmp_path / "live"
    live.mkdir()
    (live / "deploy-target.md").write_text(LEGACY, encoding="utf-8", newline="\n")
    mirror = Mirror(tmp_path / "memory" / "-work-shop")
    mirror.sync(live, PROJECT)
    mirror.stage()
    (live / "deploy-target.md").unlink()
    unlink, rmtree, refused = os.unlink, shutil.rmtree, []

    def unlink_refused_twice(file, *rest, **more):
        if Path(file) == mirror.folder / "deploy-target.md" and refused.count("file") < 2:
            refused.append("file")
            raise PermissionError("another process has the file open")
        unlink(file, *rest, **more)

    def rmtree_refused_once(folder, *rest, **more):
        if Path(folder) == mirror.staging and "folder" not in refused:
            refused.append("folder")
            raise PermissionError("another process has a file in the folder open")
        if Path(folder) == mirror.staging and "not empty" not in refused:
            refused.append("not empty")
            raise OSError(errno.ENOTEMPTY, "a file deleted from the folder is still open in another process")
        rmtree(folder, *rest, **more)

    monkeypatch.setattr(dream.disk, "PATIENCE", 5.0)
    monkeypatch.setattr(os, "unlink", unlink_refused_twice)
    monkeypatch.setattr(shutil, "rmtree", rmtree_refused_once)

    mirror.sync(live, PROJECT)
    staged = mirror.stage()

    assert sorted(refused) == ["file", "file", "folder", "not empty"]
    assert not (mirror.folder / "deploy-target.md").exists() and not (staged / "deploy-target.md").exists()
