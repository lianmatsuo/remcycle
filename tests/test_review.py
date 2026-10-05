import pytest

from dream.extract import Reply
from dream.memory import MemoryStore, Open
from dream.review import review

MEMORY = """---
name: {slot}
description: "{statement}"
metadata:
  type: project
  modified: {modified}T00:00:00Z
---

{body}
"""


def put(folder, slot, statement, body, modified="2026-09-01"):
    (folder / f"{slot}.md").write_text(MEMORY.format(slot=slot, statement=statement, body=body, modified=modified))
    with (folder / "MEMORY.md").open("a") as index:
        index.write(f"- [{slot}]({slot}.md) — {statement}\n")


@pytest.fixture
def folder(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    put(folder, "deploy_target", "Deploys go to staging first.", "Deploys go to the staging cluster first.", "2026-05-01")
    put(folder, "cutover_status", "Cutover in progress.", "The database cutover is planned for 12 August.")
    return folder


def noted(slot, **fields):
    return {"slot": slot, "topic": "Deploys", "asks": f"What about {slot}?", "dated": False, "quote": "", "reason": "", **fields}


class Reviewer:
    """A stand-in for the model: returns a fixed review and records what it was shown."""

    def __init__(self, memories=(), pairs=()):
        self.answer = {"memories": list(memories), "pairs": list(pairs)}
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return Reply(self.answer, cost_usd=0.05)


def test_every_memory_gets_a_topic_and_a_probe_and_a_dated_one_is_put_to_the_person(folder):
    store = MemoryStore(folder)
    model = Reviewer(
        [
            noted("deploy_target"),
            noted("cutover_status", dated=True, quote="planned for 12 August", reason="the cutover date has passed"),
        ]
    )

    report = review(store, model)

    assert store.topics() == ["Deploys"]
    assert store.probes() == {"cutover_status": "What about cutover_status?", "deploy_target": "What about deploy_target?"}
    assert store.queue() == [Open("cutover_status", None, False, "it looks dated: the cutover date has passed")]
    assert (report.reviewed, report.questions, report.cost_usd) == (2, 1, 0.05)
    assert "### deploy_target" in model.prompts[0] and "planned for 12 August" in model.prompts[0]


def test_a_finding_that_quotes_words_the_memory_does_not_contain_is_dropped(folder):
    store = MemoryStore(folder)
    model = Reviewer([noted("cutover_status", dated=True, quote="the cutover finished in July", reason="it is done")])

    report = review(store, model)

    assert store.queue() == []
    assert report.unsupported == 1


def test_two_memories_that_disagree_are_put_to_the_person_on_the_older_one(folder):
    put(folder, "deploy_direct", "Deploys go straight to production.", "Deploys go straight to production now.", "2026-09-20")
    store = MemoryStore(folder)
    pair = {
        "a": "deploy_direct",
        "b": "deploy_target",
        "kind": "contradicts",
        "quote_a": "straight to production",
        "quote_b": "staging cluster first",
        "reason": "they give different deploy targets",
    }

    review(store, Reviewer(pairs=[pair]))

    assert store.queue() == [
        Open("deploy_target", None, False, "it contradicts `deploy_direct`: they give different deploy targets")
    ]


def test_a_memory_already_reviewed_is_not_sent_again_until_it_changes(folder):
    store = MemoryStore(folder)
    model = Reviewer([noted("deploy_target"), noted("cutover_status")])
    review(store, model)

    review(store, model)
    assert len(model.prompts) == 1

    put(folder, "cutover_status", "Cutover done.", "The database cutover finished on 14 August.")
    review(store, model)
    assert len(model.prompts) == 2
    assert "### cutover_status" in model.prompts[1] and "### deploy_target" not in model.prompts[1]
