"""Shared pytest configuration.

Keep a test run away from the developer's real data: the conversation database
is kept in memory, so tests never create or write ``~/.jarvis/memory.db``. A test
that needs real persistence passes its own ``db_path`` (see test_memory.py).

Semantic-cache embeddings are disabled by default so the suite never downloads a
model; tests that exercise the cache inject a fake embedder.
"""

import os

import pytest

os.environ.setdefault("JARVIS_MEMORY_DB", ":memory:")
os.environ.setdefault("JARVIS_SEMANTIC_CACHE", "0")


@pytest.fixture(autouse=True)
def _reset_fact_store():
    """Keep the process-wide facts store isolated between tests."""
    import memory

    memory.reset_default_memory()
    yield
    memory.reset_default_memory()


@pytest.fixture(autouse=True)
def _reset_agent_state():
    """Keep the module-level agent state isolated between test files.

    Several tests deliberately open the circuit breaker or switch the agent to
    the Ollama backend. Without this reset the next file inherits that state and
    fails for reasons unrelated to the code it exercises (for example the cache
    test suddenly raises ``LocalAIUnavailable`` because the breaker was left
    open). Reset before *and* after so either ordering is safe.
    """
    import agent
    import config

    def reset():
        agent._breaker.reset()
        agent._CACHE.clear()
        agent.USE_LOCAL_LLM = config.USE_LOCAL_LLM

    reset()
    yield
    reset()
