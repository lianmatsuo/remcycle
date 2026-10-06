import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import dream.outside
from dream.extract import Reply
from dream.memory import Left
from dream.outside import Change, Finished, changes, witness_with


def commit(repo, message, when):
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@localhost",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@localhost",
    }
    (repo / "notes.txt").write_text(message, encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-q", "-m", message],
        check=True,
        capture_output=True,
        env=env,
    )


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "shop"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    commit(repo, "Start the workspace", "2026-10-01T09:00:00+00:00")
    commit(
        repo,
        "Set up the pnpm cache in CI\n\nThe CI cache for pnpm now restores from the lockfile hash.",
        "2026-10-04T09:00:00+00:00",
    )
    return repo


def github(pull_requests):
    """Stands in for the `gh` command, and leaves git to the real one."""

    def run(argv, cwd):
        if argv[0] == "gh":
            return None if pull_requests is None else json.dumps(pull_requests)
        return dream.outside.run(argv, cwd)

    return run


def test_commits_made_since_a_time_are_read_from_the_repository_itself(repo):
    (found,) = changes(repo, "2026-10-02T00:00:00+00:00", run=github(None))

    assert found.ref.startswith("commit ") and len(found.ref) == len("commit ") + 7
    assert datetime.fromisoformat(found.when) == datetime(2026, 10, 4, 9, tzinfo=UTC)
    assert found.text == "Set up the pnpm cache in CI\n\nThe CI cache for pnpm now restores from the lockfile hash."


def test_merged_pull_requests_are_read_through_the_github_command_when_it_answers(repo):
    merged = [
        {"number": 9, "title": "Bootstrap", "body": "", "mergedAt": "2026-09-20T10:00:00Z"},
        {"number": 12, "title": "Cache pnpm in CI", "body": "Restores the pnpm store.", "mergedAt": "2026-10-04T10:00:00Z"},
    ]

    found = changes(repo, "2026-10-02T00:00:00+00:00", run=github(merged))

    assert [change.ref for change in found][1:] == ["pull request #12"]
    assert found[1] == Change("pull request #12", "2026-10-04T10:00:00Z", "Cache pnpm in CI\n\nRestores the pnpm store.")


def test_a_folder_that_is_not_a_repository_has_no_changes(tmp_path):
    assert changes(tmp_path, "2026-10-02T00:00:00+00:00", run=github(None)) == []


THREAD = Left("ci-cache", "CI cache for pnpm is not set up.", "2026-10-02T12:00:00+00:00")
CHANGE = Change(
    "commit 3f2a9c1",
    "2026-10-04T09:00:00+00:00",
    "Set up the pnpm cache in CI\n\nThe CI cache for pnpm now restores from the lockfile hash.",
)


class Model:
    """A stand-in for the model: says which threads were finished and records what it was shown."""

    def __init__(self, *finished):
        self.finished = list(finished)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return Reply({"finished": self.finished}, cost_usd=0.02)


def reading(*found):
    asked = []

    def read(repository, since):
        asked.append(since)
        return list(found)

    read.asked = asked
    return read


def test_a_thread_is_finished_by_a_change_whose_own_words_show_it():
    model = Model({"slot": "ci-cache", "ref": "commit 3f2a9c1", "quote": "CI cache for pnpm now restores"})
    read = reading(CHANGE)

    seen = witness_with(model, read=read)(Path("/work/shop"), [THREAD])

    assert seen.finished == [Finished("ci-cache", "commit 3f2a9c1", "CI cache for pnpm now restores")]
    assert (seen.asked, seen.cost_usd) == (True, 0.02)
    assert read.asked == ["2026-10-02T12:00:00+00:00"]
    assert "ci-cache" in model.prompts[0] and "commit 3f2a9c1" in model.prompts[0]
    assert "lockfile hash" in model.prompts[0]


def test_a_finding_is_dropped_unless_it_names_a_thread_and_a_change_shown_and_quotes_that_change():
    model = Model(
        {"slot": "ci-cache", "ref": "commit 3f2a9c1", "quote": "the cache was finished last week"},
        {"slot": "ci-cache", "ref": "commit 0000000", "quote": "CI cache for pnpm now restores"},
        {"slot": "deploys", "ref": "commit 3f2a9c1", "quote": "CI cache for pnpm now restores"},
    )

    seen = witness_with(model, read=reading(CHANGE))(Path("/work/shop"), [THREAD])

    assert (seen.finished, seen.asked) == ([], True)


def test_the_model_is_not_asked_about_changes_older_than_the_thread_or_already_looked_at():
    model = Model({"slot": "ci-cache", "ref": "commit 3f2a9c1", "quote": "CI cache for pnpm now restores"})
    reopened = Left("ci-cache", "CI cache for pnpm is not set up.", "2026-10-05T12:00:00+00:00")
    looked_at = Left("ci-cache", "CI cache for pnpm is not set up.", "2026-10-02T12:00:00+00:00", "2026-10-04T12:00:00+00:00")

    for thread in (reopened, looked_at):
        seen = witness_with(model, read=reading(CHANGE))(Path("/work/shop"), [thread])
        assert (seen.finished, seen.asked, seen.cost_usd) == ([], False, 0.0)

    assert model.prompts == []
