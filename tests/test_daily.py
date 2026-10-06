import dream.daily


def test_the_background_run_is_cut_loose_from_the_session_that_started_it(tmp_path, monkeypatch):
    seen = {}

    class Popen:
        def __init__(self, argv, **options):
            seen.update(argv=argv, **options)
            self.pid = 4242

    monkeypatch.setattr(dream.daily.subprocess, "Popen", Popen)
    session = {
        "PATH": "/usr/bin:/bin",
        "CLAUDE_CONFIG_DIR": "/home/me/.claude",
        "ANTHROPIC_API_KEY": "sk-test",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "claude-desktop",
        "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH": "1",
        "CLAUDE_CODE_SESSION_ID": "7f3a9c2e",
        "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/socket",
        "CLAUDE_CODE_PLUGIN_DIRS": "/home/me/remcycle/mod",
        "CLAUDE_EFFORT": "high",
    }

    assert dream.daily.start(["dream"], tmp_path / "daily.log", env=session) == 4242
    assert (seen["start_new_session"], seen["cwd"]) == (True, tmp_path)
    assert seen["env"] == {
        "PATH": "/usr/bin:/bin",
        "CLAUDE_CONFIG_DIR": "/home/me/.claude",
        "ANTHROPIC_API_KEY": "sk-test",
        "CLAUDE_CODE_USE_BEDROCK": "1",
    }
