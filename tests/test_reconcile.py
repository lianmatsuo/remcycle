from dream.claims import Claim, ClaimType, Entry, Evidence, Provenance, Scope, Status
from dream.reconcile import Add, Confirm, Contest, Question, Reject, Supersede, reconcile


def claim(slot, statement, *, by=Provenance.HUMAN, type=ClaimType.PREFERENCE, at="2026-10-02T09:00:00Z", turns=(3, 4)):
    return Claim(
        slot=slot,
        type=type,
        scope=Scope.PROJECT,
        statement=statement,
        why="",
        provenance=by,
        evidence=Evidence("session-b", *turns),
        said_at=at,
    )


def entry(slot, statement, *, by=Provenance.HUMAN, type=ClaimType.PREFERENCE, at="2026-10-01T09:00:00Z"):
    return Entry(
        slot=slot,
        type=type,
        statement=statement,
        provenance=by,
        status=Status.ACTIVE,
        evidence=(Evidence("session-a", 1, 2),),
        said_at=at,
    )


def known(*entries):
    return {e.slot: e for e in entries}


def test_a_claim_about_something_new_is_added():
    new = claim("package-manager", "Use pnpm for JS projects.")

    assert reconcile({}, [new]) == [Add(new)]


def test_hearing_the_same_thing_again_adds_evidence_once():
    existing = entry("package-manager", "Use pnpm for JS projects.")
    again = claim("package-manager", "use pnpm for JS projects")

    first = reconcile(known(existing), [again])
    assert first == [Confirm("package-manager", Evidence("session-b", 3, 4), "2026-10-02T09:00:00Z")]

    already_recorded = Entry(**{**existing.__dict__, "evidence": (*existing.evidence, Evidence("session-b", 3, 4))})
    assert reconcile(known(already_recorded), [again]) == []


def test_a_newer_statement_of_equal_or_higher_authority_takes_over():
    existing = entry("package-manager", "Use npm for JS projects.", by=Provenance.ACCEPTED)
    changed = claim("package-manager", "Use pnpm for JS projects.", by=Provenance.HUMAN)

    assert reconcile(known(existing), [changed]) == [Supersede("package-manager", changed)]


def test_a_claim_older_than_what_memory_holds_changes_nothing():
    existing = entry("package-manager", "Use pnpm for JS projects.", at="2026-10-05T09:00:00Z")
    from_an_old_session = claim("package-manager", "Use npm for JS projects.", at="2026-09-20T09:00:00Z")

    assert reconcile(known(existing), [from_an_old_session]) == []


def test_a_weaker_claim_against_a_stated_preference_is_put_to_the_person():
    existing = entry("commit-style", "Write commit subjects in the imperative.", by=Provenance.HUMAN)
    guess = claim("commit-style", "Commit subjects use past tense.", by=Provenance.INFERRED)

    assert reconcile(known(existing), [guess]) == [Question("commit-style", guess)]


def test_a_weaker_claim_against_a_fact_withholds_the_fact():
    existing = entry("ci-runner", "CI runs on the self-hosted runner.", by=Provenance.HUMAN, type=ClaimType.FACT)
    seen = claim("ci-runner", "CI runs on hosted runners.", by=Provenance.OBSERVED, type=ClaimType.FACT)

    assert reconcile(known(existing), [seen]) == [Contest("ci-runner", seen)]


def test_claims_from_one_run_are_applied_in_the_order_they_were_said():
    later = claim("package-manager", "Use pnpm for JS projects.", at="2026-10-03T09:00:00Z")
    earlier = claim("package-manager", "Use npm for JS projects.", at="2026-10-02T09:00:00Z")

    assert reconcile({}, [later, earlier]) == [Add(earlier), Supersede("package-manager", later)]


def test_a_memory_from_before_provenance_was_kept_yields_to_the_person_but_not_to_a_guess():
    legacy = Entry(slot="deploy-target", statement="Deploys go to the staging cluster first.")
    stated = claim("deploy-target", "Deploys go straight to production.", by=Provenance.HUMAN, type=ClaimType.DECISION)
    guessed = claim("deploy-target", "Deploys go straight to production.", by=Provenance.INFERRED, type=ClaimType.DECISION)

    assert reconcile(known(legacy), [stated]) == [Supersede("deploy-target", stated)]
    assert reconcile(known(legacy), [guessed]) == [Question("deploy-target", guessed)]


def test_the_same_point_in_slightly_different_words_counts_as_the_same_statement():
    existing = entry("package-manager", "Use pnpm for all JS and TS projects.")
    reworded = claim("package-manager", "Use pnpm for JS and TS projects.")

    assert reconcile(known(existing), [reworded]) == [
        Confirm("package-manager", Evidence("session-b", 3, 4), "2026-10-02T09:00:00Z")
    ]


def test_a_statement_that_differs_by_a_negation_or_a_number_is_a_different_statement():
    rule = entry("migrations", "Run database migrations before deploying the api service to production.")
    negated = claim("migrations", "Do not run database migrations before deploying the api service to production.")
    limit = entry("retries", "Retry failed webhook deliveries up to 3 times with exponential backoff.")
    raised = claim("retries", "Retry failed webhook deliveries up to 5 times with exponential backoff.")

    assert reconcile(known(rule), [negated]) == [Supersede("migrations", negated)]
    assert reconcile(known(limit), [raised]) == [Supersede("retries", raised)]


def test_only_what_the_person_said_or_a_lesson_can_start_a_new_entry():
    guess = claim("test-runner", "The project uses vitest.", by=Provenance.INFERRED, type=ClaimType.FACT)
    lesson = claim(
        "flaky-e2e",
        "Retrying the e2e suite hid a real race.",
        by=Provenance.INFERRED,
        type=ClaimType.LESSON,
        at="2026-10-03T09:00:00Z",
    )

    assert reconcile({}, [guess, lesson]) == [Reject(guess, "nothing the person said supports it"), Add(lesson)]
