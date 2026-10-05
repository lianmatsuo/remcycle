import shutil

import pytest

from dream.claims import Claim, ClaimType, Evidence, Provenance, Scope
from dream.gate import check
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
        (folder / f"{slot}.md").write_text(MEMORY.format(slot=slot, statement=statement))
    (folder / "MEMORY.md").write_text(
        "- [Deploy target](deploy-target.md) — staging first\n- [CI runner](ci-runner.md) — self-hosted\n"
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
    (staged / "MEMORY.md").write_text("- [Deploy target](deploy-target.md) — staging first\n")

    assert check(live, staged) == ["1 entry disappeared without a record: ci-runner"]


def test_a_memory_file_reworded_outside_any_operation_blocks_the_merge(live, staged):
    (staged / "ci-runner.md").write_text(MEMORY.format(slot="ci-runner", statement="CI is mostly self-hosted."))

    assert check(live, staged) == ["ci-runner.md changed without a recorded operation"]


def test_an_index_claude_code_would_cut_off_or_that_points_nowhere_blocks_the_merge(live, staged):
    index = staged / "MEMORY.md"
    index.write_text(index.read_text() + "- [Gone](gone.md) — nothing here\n" + "- filler\n" * 200)

    assert check(live, staged) == [
        "MEMORY.md has 203 lines; Claude Code loads the first 200",
        "MEMORY.md points at gone.md, which is not a memory in this folder",
    ]
