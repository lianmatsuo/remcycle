import pytest

import dream.dreaming
from dream.archive import Archive
from dream.dreaming import dream as run_dream
from dream.session import context, status
from dream.extract import ExtractionError, Reply
from dream.memory import MemoryStore
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
    (memory / "deploy-target.md").write_text(LEGACY)
    (memory / "MEMORY.md").write_text("- [Deploy target](deploy-target.md) — staging first\n")
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
    assert (copy / "MEMORY.md").read_text() == (
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
    assert "package-manager.md" in (live / "MEMORY.md").read_text()


def test_a_memory_a_session_wrote_since_the_last_dream_is_taken_in_and_nothing_the_dream_added_is_lost(
    archive, claude, tmp_path
):
    pnpm_session(claude)
    dreamt(archive, claude, tmp_path, Model())
    live = claude / "-work-shop" / "memory"
    (live / "ci-runner.md").write_text(LEGACY.replace("deploy-target", "ci-runner").replace("Deploys go to the staging cluster first", "CI is self-hosted"))
    (live / "MEMORY.md").write_text((live / "MEMORY.md").read_text() + "- [CI runner](ci-runner.md) — self-hosted\n")
    put_session(claude, "s-later", [human("nothing worth keeping", 0)], cwd=PROJECT)

    dreamt(archive, claude, tmp_path, Model())

    copy = tmp_path / "memory" / "-work-shop"
    assert sorted(MemoryStore(copy).entries()) == ["ci-runner", "deploy-target", "package-manager"]
    assert (copy / "MEMORY.md").read_text() == (
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
    monkeypatch.setattr(dream.dreaming, "check", lambda live, staged: ["MEMORY.md has 203 lines"])

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
            (live / "written-meanwhile.md").write_text(LEGACY)
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
        patched.setattr(dream.dreaming, "check", lambda live, staged: ["MEMORY.md has 203 lines"])
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

    given = context(tmp_path / "memory", PROJECT, now="2026-10-03T09:00:00+00:00")
    assert given == {
        "everywhere": ["Use pnpm for JS projects."],
        "open_threads": ["CI cache for pnpm is not set up."],
    }
    assert context(tmp_path / "memory", "/work/other", now="2026-10-03T09:00:00+00:00") == {
        "everywhere": ["Use pnpm for JS projects."],
        "open_threads": [],
    }


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
    assert (state["entries"], state["withheld"]) == (2, 0)
    assert state["learned"] == [
        {"slot": "package-manager", "statement": "Use pnpm for JS projects.", "from": "human", "evidence": "dream show s-pnpm --first 2 --last 2"}
    ]
    assert state["waiting"] == [
        {"slot": "deploy-target", "suggests": "Deploys go straight to production.", "from": "inferred",
         "withheld": False, "evidence": "dream show s-pnpm --first 1 --last 1"}
    ]
