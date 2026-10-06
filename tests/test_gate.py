import shutil

import pytest

from dream.claims import Claim, ClaimType, Evidence, Provenance, Scope
from dream.extract import Reply
from dream.gate import check, judge_with
from dream.memory import MemoryStore
from dream.reconcile import Add, Contest, Supersede

MEMORY = """---
name: {slot}
description: "{statement}"
metadata:
  type: project
---

{statement}
"""


def claim(slot, statement):
    return Claim(
        slot=slot,
        type=ClaimType.DECISION,
        scope=Scope.PROJECT,
        statement=statement,
        why="",
        provenance=Provenance.HUMAN,
        evidence=Evidence("session-b", 3, 4),
        said_at="2026-10-02T09:00:00Z",
    )


@pytest.fixture
def live(tmp_path):
    folder = tmp_path / "live"
    folder.mkdir()
    for slot, statement in [("deploy-target", "Deploys go to staging first."), ("ci-runner", "CI is self-hosted.")]:
        (folder / f"{slot}.md").write_text(MEMORY.format(slot=slot, statement=statement), encoding="utf-8", newline="\n")
    (folder / "MEMORY.md").write_text(
        "- [Deploy target](deploy-target.md) — staging first\n- [CI runner](ci-runner.md) — self-hosted\n",
        encoding="utf-8",
        newline="\n",
    )
    return folder


@pytest.fixture
def staged(live, tmp_path):
    return shutil.copytree(live, tmp_path / "staged")


def test_memory_changed_only_through_recorded_operations_may_go_live(live, staged):
    MemoryStore(staged).apply(
        [
            Add(claim("package-manager", "Use pnpm for JS projects.")),
            Supersede("deploy-target", claim("deploy-target", "Deploys go straight to production.")),
            Contest("ci-runner", claim("ci-runner", "CI uses hosted runners.")),
        ]
    )

    assert check(live, staged) == []


def test_an_entry_that_vanished_blocks_the_merge(live, staged):
    (staged / "ci-runner.md").unlink()
    (staged / "MEMORY.md").write_text("- [Deploy target](deploy-target.md) — staging first\n", encoding="utf-8", newline="\n")

    assert check(live, staged) == ["1 entry disappeared without a record: ci-runner"]


def test_a_memory_file_reworded_outside_any_operation_blocks_the_merge(live, staged):
    (staged / "ci-runner.md").write_text(MEMORY.format(slot="ci-runner", statement="CI is mostly self-hosted."), encoding="utf-8", newline="\n")

    assert check(live, staged) == ["ci-runner.md changed without a recorded operation"]


def test_an_index_claude_code_would_cut_off_or_that_points_nowhere_blocks_the_merge(live, staged):
    index = staged / "MEMORY.md"
    index.write_text(index.read_text(encoding="utf-8") + "- [Gone](gone.md) — nothing here\n" + "- filler\n" * 200, encoding="utf-8", newline="\n")

    assert check(live, staged) == [
        "MEMORY.md has 203 lines; Claude Code loads the first 200",
        "MEMORY.md points at gone.md, which is not a memory in this folder",
    ]


def asked(slot, statement, question):
    return Claim(**{**claim(slot, statement).__dict__, "asks": question})


def picks_by_keyword(index, questions):
    """A stand-in for the model: opens package-manager.md only while the index still mentions pnpm."""
    return ["package-manager.md" if "pnpm" in index else "none" for _ in questions]


def test_an_index_that_stops_leading_to_the_right_memory_blocks_the_merge(live, tmp_path):
    MemoryStore(live).apply([Add(asked("package-manager", "Use pnpm for JS projects.", "Which package manager do we use?"))])
    staged = shutil.copytree(live, tmp_path / "staged")
    index = staged / "MEMORY.md"
    index.write_text(index.read_text(encoding="utf-8").replace("Use pnpm for JS projects.", "tooling"), encoding="utf-8", newline="\n")

    assert check(live, staged, judge=picks_by_keyword) == [
        "the index leads to the right memory for 0 of 1 questions, down from 1"
    ]


def test_a_question_written_in_the_change_itself_still_tests_an_entry_that_was_already_there(live, tmp_path):
    MemoryStore(live).apply([Add(claim("package-manager", "Use pnpm for JS projects."))])
    staged = shutil.copytree(live, tmp_path / "staged")
    reviewed = MemoryStore(staged)
    reviewed.set_probe("package-manager", "Which package manager do we use?")
    reviewed.apply([Add(asked("formatter", "Format with ruff.", "Which formatter do we use?"))])
    index = staged / "MEMORY.md"
    index.write_text(index.read_text(encoding="utf-8").replace("Use pnpm for JS projects.", "tooling"), encoding="utf-8", newline="\n")
    asked_about = []

    def judge(index, questions):
        asked_about.append(list(questions))
        return picks_by_keyword(index, questions)

    assert check(live, staged, judge=judge) == ["the index leads to the right memory for 0 of 1 questions, down from 1"]
    assert asked_about == [["Which package manager do we use?"], ["Which package manager do we use?"]]


def test_the_model_is_not_asked_when_the_index_did_not_change(live, staged):
    def must_not_be_asked(index, questions):
        raise AssertionError("the judge was asked")

    MemoryStore(live).apply([Add(asked("package-manager", "Use pnpm for JS projects.", "Which package manager?"))])

    assert check(live, live, judge=must_not_be_asked) == []


def test_a_judge_backed_by_the_model_matches_its_answers_to_the_questions_by_number():
    prompts = []

    def runner(prompt):
        prompts.append(prompt)
        return Reply({"answers": [{"question": 2, "file": "ci-runner.md"}, {"question": 1, "file": "package-manager.md"}]})

    judge = judge_with(runner)

    assert judge("- [Package manager](package-manager.md) — pnpm\n", ["Which package manager?", "Where does CI run?", "Who?"]) == [
        "package-manager.md",
        "ci-runner.md",
        "",
    ]
    assert "1. Which package manager?" in prompts[0] and "- [Package manager](package-manager.md) — pnpm" in prompts[0]
