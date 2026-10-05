import os

import pytest

from dream.claims import ClaimType, Evidence, Provenance, Scope
from dream.extract import ClaudeCode, Correction, ExtractionError, Reply, extract
from dream.transcript import Author, Kind, Turn

SESSION = "7f3a9c2e-1b4d-4e6f-8a90-123456789abc"

TURNS = [
    Turn(0, Author.HUMAN, Kind.PROMPT, "set up the js workspace", "u0", "2026-10-02T09:00:00Z"),
    Turn(1, Author.ASSISTANT, Kind.REPLY, "I'll use npm unless you prefer something else.", "u1", "2026-10-02T09:00:05Z"),
    Turn(2, Author.ASSISTANT, Kind.TOOL, "Bash npm init -y", "u2", "2026-10-02T09:00:06Z"),
    Turn(3, Author.HUMAN, Kind.PROMPT, "no, always use pnpm for JS projects, npm lockfiles drifted on us", "u3", "2026-10-02T09:01:00Z"),
    Turn(4, Author.ASSISTANT, Kind.REPLY, "Switched to pnpm.", "u4", "2026-10-02T09:01:30Z"),
]


def raw_claim(**overrides):
    return {
        "slot": "package-manager",
        "type": "preference",
        "scope": "project",
        "statement": "Use pnpm for JS projects.",
        "why": "npm lockfiles drifted.",
        "provenance": "human",
        "first_turn": 3,
        "last_turn": 4,
        "quote": "always use pnpm for JS projects",
        "anchor": "",
        **overrides,
    }


def answering(*claims, threads=(), corrections=(), summary="Set up the JS workspace."):
    """A stand-in for the model: records the prompt it was given and returns a fixed answer."""
    prompts = []

    def runner(prompt):
        prompts.append(prompt)
        answer = {"summary": summary, "claims": list(claims), "threads": list(threads), "corrections": list(corrections)}
        return Reply(answer, cost_usd=0.01)

    runner.prompts = prompts
    return runner


def test_a_supported_claim_comes_back_typed_with_its_evidence_and_when_it_was_said():
    extraction = extract(SESSION, TURNS, known={}, runner=answering(raw_claim()))

    (claim,) = extraction.claims
    assert (claim.slot, claim.type, claim.scope, claim.provenance) == (
        "package-manager",
        ClaimType.PREFERENCE,
        Scope.PROJECT,
        Provenance.HUMAN,
    )
    assert (claim.statement, claim.why) == ("Use pnpm for JS projects.", "npm lockfiles drifted.")
    assert claim.evidence == Evidence(SESSION, 3, 4)
    assert claim.said_at == "2026-10-02T09:01:00Z"
    assert extraction.summary == "Set up the JS workspace."
    assert extraction.cost_usd == 0.01


def test_a_claim_whose_quote_is_not_in_the_turns_it_cites_is_thrown_out():
    invented = raw_claim(slot="test-runner", quote="we always run vitest in watch mode")

    extraction = extract(SESSION, TURNS, known={}, runner=answering(invented))

    assert extraction.claims == ()
    assert [(r.slot, r.reason) for r in extraction.rejected] == [
        ("test-runner", "its quote is not in turns 3 to 4")
    ]


def test_words_the_person_never_typed_cannot_carry_the_persons_authority():
    from_the_assistant = raw_claim(first_turn=1, last_turn=1, quote="I'll use npm unless you prefer something else")

    (claim,) = extract(SESSION, TURNS, known={}, runner=answering(from_the_assistant)).claims

    assert claim.provenance == Provenance.INFERRED


def test_the_model_is_shown_what_memory_already_holds_and_the_prose_but_not_the_tool_calls():
    runner = answering()

    extract(SESSION, TURNS, known={"deploy-target": "Deploys go to staging first."}, runner=runner)

    (prompt,) = runner.prompts
    assert "- deploy-target: Deploys go to staging first." in prompt
    assert "[3] person: no, always use pnpm for JS projects, npm lockfiles drifted on us" in prompt
    assert "[1] assistant: I'll use npm unless you prefer something else." in prompt
    assert "npm init" not in prompt
    assert 'Call the person "the user"' in prompt


def test_a_session_too_long_for_one_pass_is_read_in_several_and_the_results_are_joined():
    runner = answering(raw_claim())

    extraction = extract(SESSION, TURNS, known={}, runner=runner, budget=60)

    assert len(runner.prompts) > 1
    assert "[0] person" in runner.prompts[0] and "[4] assistant" in runner.prompts[-1]
    assert len(extraction.claims) == len(runner.prompts)
    assert extraction.cost_usd == pytest.approx(0.01 * len(runner.prompts))


def test_threads_left_open_or_finished_come_back_with_their_state():
    threads = [
        {"slot": "ci-cache", "state": "open", "statement": "CI cache for pnpm is not set up yet.", "turn": 4},
        {"slot": "lockfile-cleanup", "state": "done", "statement": "Old npm lockfile removed.", "turn": 4},
    ]

    extraction = extract(SESSION, TURNS, known={}, runner=answering(threads=threads))

    assert [(t.slot, t.is_open) for t in extraction.threads] == [("ci-cache", True), ("lockfile-cleanup", False)]


def test_a_model_that_cannot_be_asked_is_an_error_for_that_session():
    def unreachable(prompt):
        raise ExtractionError("could not run Claude Code: not signed in")

    with pytest.raises(ExtractionError, match="not signed in"):
        extract(SESSION, TURNS, known={}, runner=unreachable)


def test_a_correction_counts_against_an_entry_only_when_the_person_typed_it():
    typed = {"slot": "package-manager", "turn": 3, "quote": "no, always use pnpm for JS projects"}
    not_typed = {"slot": "deploy-target", "turn": 1, "quote": "I'll use npm unless you prefer something else"}

    extraction = extract(SESSION, TURNS, known={}, runner=answering(corrections=[typed, not_typed]))

    assert extraction.corrections == (Correction("package-manager", Evidence(SESSION, 3, 3)),)


def test_the_model_step_runs_without_the_plugins_of_the_session_that_started_it(tmp_path, monkeypatch):
    stand_in = tmp_path / "claude"
    stand_in.write_text(
        "#!/bin/sh\n"
        "cat > /dev/null\n"
        'printf \'{"structured_output": {"plugins": "%s", "hooks": "%s", "home": "%s"}, "total_cost_usd": 0.01}\''
        ' "${CLAUDE_CODE_PLUGIN_DIRS-unset}" "${CLAUDE_CODE_ENABLE_FUNCTION_HOOKS-unset}" "$HOME"\n'
    )
    stand_in.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("CLAUDE_CODE_PLUGIN_DIRS", "/somewhere/mod")
    monkeypatch.setenv("CLAUDE_CODE_ENABLE_FUNCTION_HOOKS", "1")
    monkeypatch.setenv("HOME", "/home/me")

    reply = ClaudeCode()("what did this session establish?")

    assert reply.data == {"plugins": "unset", "hooks": "unset", "home": "/home/me"}
    assert reply.cost_usd == 0.01
