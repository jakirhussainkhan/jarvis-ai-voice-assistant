"""Tests for long-term user facts, memory tools, and prompt injection."""

import types

import agent
import memory
import tools
from memory import Memory


class _FakeClient:
    """Minimal OpenAI-compatible client that records the messages it receives."""

    def __init__(self):
        self.calls = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = types.SimpleNamespace(content="Noted.", tool_calls=None)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=message)],
            usage=types.SimpleNamespace(total_tokens=4),
        )


def _clear_agent_state():
    agent._breaker.reset()
    agent._CACHE.clear()


def _memory_with_facts(tmp_path):
    mem = Memory(db_path=str(tmp_path / "memory.db"))
    mem.add_fact("my name is Jakir")
    mem.add_fact("I prefer tea over coffee")
    return mem


def test_agent_injects_facts_before_the_latest_query(tmp_path, monkeypatch):
    _clear_agent_state()
    mem = _memory_with_facts(tmp_path)
    client = _FakeClient()
    monkeypatch.setattr(agent, "_client", client)

    reply, tokens = agent.run_agent("What do you know about me?", mem)

    assert reply == "Noted."
    assert tokens == 4
    messages = client.calls[0]["messages"]
    assert messages[0] == {
        "role": "system", "content": agent.SYSTEM_INSTRUCTION,
    }
    assert messages[1]["role"] == "user"
    assert "[SYSTEM MEMORY:" in messages[1]["content"]
    assert "my name is Jakir" in messages[1]["content"]
    assert "I prefer tea over coffee" in messages[1]["content"]
    assert messages[-1]["role"] == "user"
    assert "What do you know about me?" in messages[-1]["content"]
    # Facts must never contaminate the static prefix-cached system prompt.
    assert "my name is Jakir" not in agent.SYSTEM_INSTRUCTION


def test_agent_does_not_inject_an_empty_memory_message(tmp_path, monkeypatch):
    _clear_agent_state()
    mem = Memory(db_path=str(tmp_path / "memory.db"))
    client = _FakeClient()
    monkeypatch.setattr(agent, "_client", client)

    agent.run_agent("Tell me a short joke.", mem)

    messages = client.calls[0]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "[SYSTEM MEMORY:" not in messages[1]["content"]


def test_memory_tools_are_registered_and_pruned_for_memory_queries():
    assert agent._DISPATCH["fn_remember_fact"] is tools.fn_remember_fact
    assert agent._DISPATCH["fn_recall_facts"] is tools.fn_recall_facts
    assert agent._DISPATCH["fn_forget_fact"] is tools.fn_forget_fact
    names = {
        schema["function"]["name"]
        for schema in agent.tools_for("Please remember my name is Jakir")
    }
    assert {"fn_remember_fact", "fn_recall_facts", "fn_forget_fact"} <= names

    # A correction or forget request must also expose the new tool.
    for utterance in ("forget my name", "my name is Jakir, not John"):
        names = {schema["function"]["name"] for schema in agent.tools_for(utterance)}
        assert "fn_forget_fact" in names


def test_system_prompt_teaches_name_corrections():
    prompt = agent.SYSTEM_INSTRUCTION
    assert "fn_forget_fact" in prompt
    assert "My name is X" in prompt
    assert "Forget my name" in prompt


def test_forget_fact_deletes_matching_saved_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY_DB", str(tmp_path / "facts.db"))
    memory.reset_default_memory()
    try:
        tools.fn_remember_fact("my name is Jakir")
        tools.fn_remember_fact("I prefer tea over coffee")

        out = tools.fn_forget_fact("Jakir")
        assert out == "Forgot facts matching Jakir"

        recalled = tools.fn_recall_facts()
        assert "Jakir" not in recalled
        assert "I prefer tea over coffee" in recalled
    finally:
        memory.reset_default_memory()


def test_forget_fact_reports_when_nothing_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY_DB", str(tmp_path / "facts.db"))
    memory.reset_default_memory()
    try:
        tools.fn_remember_fact("I prefer tea over coffee")
        assert "don't have any saved facts" in tools.fn_forget_fact("Jakir")
        assert "What should I forget" in tools.fn_forget_fact("  ")
    finally:
        memory.reset_default_memory()


def test_forget_fact_uses_the_active_turn_memory(tmp_path):
    mem = Memory(db_path=str(tmp_path / "memory.db"))
    mem.add_fact("my name is Jakir")
    token = tools.set_active_memory(mem)
    try:
        assert tools.fn_forget_fact("name") == "Forgot facts matching name"
    finally:
        tools.reset_active_memory(token)

    assert mem.get_facts() == []


def test_memory_tools_save_and_recall_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY_DB", str(tmp_path / "facts.db"))
    memory.reset_default_memory()
    try:
        out = tools.fn_remember_fact("my name is Jakir")
        assert "Jakir" in out

        recalled = tools.fn_recall_facts()
        assert "my name is Jakir" in recalled
        # A normal Memory instance points at the same database file.
        direct = Memory()
        try:
            assert direct.get_facts() == ["my name is Jakir"]
        finally:
            direct.close()
    finally:
        memory.reset_default_memory()


def test_fact_tools_use_the_active_turn_memory(tmp_path):
    mem = Memory(db_path=str(tmp_path / "memory.db"))
    token = tools.set_active_memory(mem)
    try:
        tools.fn_remember_fact("my name is Jakir")
    finally:
        tools.reset_active_memory(token)

    assert mem.get_facts() == ["my name is Jakir"]


def test_remember_fact_rejects_an_empty_fact(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY_DB", str(tmp_path / "facts.db"))
    memory.reset_default_memory()
    try:
        assert "What fact" in tools.fn_remember_fact("   ")
        assert "saved facts" in tools.fn_recall_facts().lower()
    finally:
        memory.reset_default_memory()
