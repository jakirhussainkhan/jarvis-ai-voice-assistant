"""Tests for the SQLite-backed memory.Memory."""

from memory import Memory, default_db_path


def test_add_and_render(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add("user", "hello")
    mem.add("assistant", "hi")
    assert mem.as_text() == "User: hello\nJarvis: hi"


def test_model_role_is_labelled_jarvis(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add("model", "hi")
    assert mem.as_text() == "Jarvis: hi"


def test_transcript_is_windowed(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"), limit=2)
    mem.add("user", "a")
    mem.add("assistant", "b")
    mem.add("user", "c")
    # Only the last `limit` turns are returned, oldest first.
    assert mem.get_transcript() == "Jarvis: b\nUser: c"
    assert mem.get_transcript(limit=1) == "User: c"


def test_history_persists_across_instances(tmp_path):
    path = str(tmp_path / "m.db")
    first = Memory(db_path=path)
    first.add("user", "remember this")
    first.close()

    second = Memory(db_path=path)
    assert "remember this" in second.as_text()


def test_clear_empties_memory(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add("user", "x")
    mem.clear()
    assert mem.as_text() == ""


def test_default_path_honours_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_MEMORY_DB", str(tmp_path / "custom.db"))
    assert default_db_path() == str(tmp_path / "custom.db")


def test_default_path_is_under_the_home_directory(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_DB", raising=False)
    monkeypatch.setenv("HOME", "/home/tester")
    assert default_db_path() == "/home/tester/.jarvis/memory.db"
