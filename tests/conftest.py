import pytest


@pytest.fixture(autouse=True)
def away_from_real_data(tmp_path, monkeypatch):
    """Point remcycle's folders and Claude Code's away from the real ones, for a test that does not say where they are."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "unset-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "unset-config"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "unset-claude"))
