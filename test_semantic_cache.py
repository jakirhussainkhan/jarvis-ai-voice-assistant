"""Tests for the semantic answer cache (no model download, no network)."""

import agent
import semantic_cache


def _capital_embedder(text):
    """Tiny deterministic embedding: capital questions share a vector."""
    text = text.lower()
    if "capital" in text and "france" in text:
        return [1.0, 0.0, 0.0]
    if "weather" in text:
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]


def _cache(**kwargs):
    kwargs.setdefault("db_path", ":memory:")
    kwargs.setdefault("embedder", _capital_embedder)
    kwargs.setdefault("enabled", True)
    return semantic_cache.SemanticCache(**kwargs)


def test_similar_queries_hit_the_same_answer():
    cache = _cache()
    assert cache.store("What is the capital of France?", "Paris.") is True
    assert cache.lookup("Which city is the capital of France?") == "Paris."
    assert len(cache) == 1


def test_dissimilar_cached_query_misses():
    cache = _cache()
    cache.store("What is the capital of France?", "Paris.")
    assert cache.lookup("Who wrote Hamlet?") is None


def test_dynamic_queries_are_never_cached():
    cache = _cache()
    assert cache.is_cacheable("what time is it") is False
    assert cache.is_cacheable("what is the weather today") is False
    assert cache.store("What time is it?", "Three o'clock.") is False
    assert cache.store("What is the weather today?", "Sunny.") is False
    assert cache.lookup("Tell me the time") is None


def test_error_replies_are_not_cached():
    cache = _cache()
    assert cache.store(
        "What is the capital of France?",
        "Sorry, I couldn't finish that.",
    ) is False
    assert len(cache) == 0


def test_cache_persists_in_sqlite(tmp_path):
    path = str(tmp_path / "semantic.db")
    cache = _cache(db_path=path)
    cache.store("What is the capital of France?", "Paris.")
    cache.close()

    reopened = _cache(db_path=path)
    assert reopened.lookup("Which city is the capital of France?") == "Paris."


def test_disabled_cache_is_a_noop():
    cache = _cache(enabled=False)
    assert cache.store("What is the capital of France?", "Paris.") is False
    assert cache.lookup("Which city is the capital of France?") is None
    assert len(cache) == 0


def test_injected_embedder_enables_the_cache(monkeypatch):
    monkeypatch.setenv("JARVIS_SEMANTIC_CACHE", "0")
    cache = semantic_cache.SemanticCache(
        db_path=":memory:",
        embedder=_capital_embedder,
    )
    assert cache.enabled is True
    assert semantic_cache.SemanticCache.is_cacheable(
        "What is the capital of France?"
    ) is True


def test_agent_uses_semantic_cache_for_paraphrases(monkeypatch):
    cache = _cache()
    calls = []

    def fake_chat(message, tools=None):
        calls.append(message)
        return "Paris is the capital of France.", 12

    monkeypatch.setattr(agent, "_semantic_cache", cache)
    monkeypatch.setattr(agent, "_client", object())
    monkeypatch.setattr(agent, "_chat", fake_chat)
    agent._CACHE.clear()

    first = agent.run_agent("What is the capital of France?")
    second = agent.run_agent("Which city is the capital of France?")

    assert first == ("Paris is the capital of France.", 12)
    assert second == ("Paris is the capital of France.", 0)
    assert len(calls) == 1
    assert len(cache) == 1


def test_agent_does_not_store_dynamic_answers(monkeypatch):
    cache = _cache()
    monkeypatch.setattr(agent, "_semantic_cache", cache)
    monkeypatch.setattr(agent, "_client", object())
    monkeypatch.setattr(agent, "_chat", lambda message, tools=None: ("3 pm", 2))
    agent._CACHE.clear()

    agent.run_agent("What time is it?")

    assert len(cache) == 0
