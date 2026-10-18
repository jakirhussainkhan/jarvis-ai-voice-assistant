"""Tests for memory.Memory."""

from memory import Memory


def test_add_and_render():
    mem = Memory()
    mem.add("user", "hello")
    mem.add("assistant", "hi")
    assert mem.as_text() == "User: hello\nJarvis: hi"


def test_model_role_is_labelled_jarvis():
    mem = Memory()
    mem.add("model", "hi")
    assert mem.as_text() == "Jarvis: hi"


def test_window_drops_oldest_turns():
    mem = Memory(maxlen=2)
    mem.add("user", "a")
    mem.add("assistant", "b")
    mem.add("user", "c")
    assert len(mem.turns) == 2
    assert mem.as_text() == "Jarvis: b\nUser: c"


def test_clear_empties_memory():
    mem = Memory()
    mem.add("user", "x")
    mem.clear()
    assert mem.as_text() == ""
