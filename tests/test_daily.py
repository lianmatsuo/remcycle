import os
import subprocess
import sys
import time

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
    if sys.platform == "win32":
        assert seen["creationflags"] == subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert seen["start_new_session"] is True
    assert seen["cwd"] == tmp_path
    assert seen["env"] == {
        "PATH": "/usr/bin:/bin",
        "CLAUDE_CONFIG_DIR": "/home/me/.claude",
        "ANTHROPIC_API_KEY": "sk-test",
        "CLAUDE_CODE_USE_BEDROCK": "1",
    }


def test_the_background_run_writes_what_it_prints_to_its_log(tmp_path):
    log = tmp_path / "data" / "daily.log"

    pid = dream.daily.start([sys.executable, "-c", "print('the dream ran')"], log)
    assert pid > 0
    waited_until = time.monotonic() + 30
    while "the dream ran" not in log.read_text(encoding="utf-8") and time.monotonic() < waited_until:
        time.sleep(0.05)
    assert "the dream ran" in log.read_text(encoding="utf-8")


def test_a_process_counts_as_running_until_it_has_ended():
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()

    assert dream.daily.is_alive(os.getpid())
    assert not dream.daily.is_alive(ended.pid)
    assert not dream.daily.is_alive(None)
