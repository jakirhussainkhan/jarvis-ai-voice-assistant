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


# --------------------------------------------------------------------------- #
# long-term user facts
# --------------------------------------------------------------------------- #

def test_add_and_get_facts(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    assert mem.add_fact("my name is Jakir") is True
    assert mem.add_fact("I prefer tea over coffee") is True

    # Newest facts come first.
    assert mem.get_facts() == [
        "I prefer tea over coffee",
        "my name is Jakir",
    ]


def test_empty_fact_is_ignored(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    assert mem.add_fact("   ") is False
    assert mem.get_facts() == []


def test_facts_are_limited_to_the_most_recent(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    for index in range(20):
        mem.add_fact(f"fact {index}")

    facts = mem.get_facts(limit=15)
    assert len(facts) == 15
    assert facts[0] == "fact 19"
    assert facts[-1] == "fact 5"
    assert "fact 4" not in facts
    assert len(mem.get_facts(limit=None)) == 20
    assert mem.get_facts(limit=0) == []


def test_facts_persist_across_instances(tmp_path):
    path = str(tmp_path / "m.db")
    first = Memory(db_path=path)
    first.add_fact("my birthday is in May")
    first.close()

    second = Memory(db_path=path)
    assert second.get_facts() == ["my birthday is in May"]


def test_clear_history_keeps_long_term_facts(tmp_path):
    mem = Memory(db_path=str(tmp_path / "m.db"))
    mem.add("user", "hello")
    mem.add_fact("my name is Jakir")
    mem.clear()

    assert mem.as_text() == ""
    assert mem.get_facts() == ["my name is Jakir"]

    mem.clear_facts()
    assert mem.get_facts() == []
