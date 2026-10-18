"""Tests for the agent response cache and fallbacks (no network)."""

import agent


def test_cache_key_is_deterministic():
    # _cache_key hashes its input verbatim; run_agent applies .lower() first, so
    # case-insensitivity is a property of the cache lookup, not of this helper.
    assert agent._cache_key("hello") == agent._cache_key("hello")
    assert agent._cache_key("hello") != agent._cache_key("goodbye")


def test_second_identical_query_is_a_cache_hit(monkeypatch):
    calls = []

    def fake_chat(message):
        calls.append(message)
        return "hello back", 42

    monkeypatch.setattr(agent, "_client", object())
    monkeypatch.setattr(agent, "_chat", fake_chat)
    agent._CACHE.clear()

    first = agent.run_agent("What is the weather?")
    second = agent.run_agent("what is the weather?")  # same after .lower()

    assert first == ("hello back", 42)
    assert second == ("hello back", 0)          # tokens 0 => served from cache
    assert calls == ["What is the weather?"]    # the API was hit only once


def test_blank_reply_is_replaced(monkeypatch):
    monkeypatch.setattr(agent, "_client", object())
    monkeypatch.setattr(agent, "_chat", lambda message: ("", 0))
    agent._CACHE.clear()
    assert agent.run_agent("a blank answer please") == (
        "Sorry, I couldn't finish that.", 0,
    )


def test_no_client_means_no_api_call(monkeypatch):
    monkeypatch.setattr(agent, "_client", None)
    agent._CACHE.clear()
    assert agent.run_agent("anything at all") == (
        "Sorry, I couldn't finish that.", 0,
    )


def test_cache_hit_does_not_write_memory(monkeypatch):
    monkeypatch.setattr(agent, "_client", object())
    monkeypatch.setattr(agent, "_chat", lambda message: ("hi", 7))
    agent._CACHE.clear()

    mem = Memory()

    agent.run_agent("repeat me", mem)
    assert len(mem.turns) == 2          # first call records both turns
    agent.run_agent("repeat me", mem)   # cache hit
    assert len(mem.turns) == 2          # memory unchanged


class Memory:
    """Minimal memory double so this test does not depend on memory.Memory."""

    def __init__(self):
        self.turns = []

    def add(self, role, text):
        self.turns.append((role, text))

    def as_text(self):
        return "\n".join(f"{r}: {t}" for r, t in self.turns)
