"""Tests for the optional Ollama backend (dormant by default, no real network)."""

import sys
import types

import agent
import config
import main
import resilience


class _FakeClient:
    """Minimal OpenAI-compatible client used by the agent."""

    def __init__(self, handler):
        self.calls = 0
        self._handler = handler
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls += 1
        return self._handler(kwargs)


def _response(content="ok", total_tokens=1):
    message = types.SimpleNamespace(content=content, tool_calls=None)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=message)],
        usage=types.SimpleNamespace(total_tokens=total_tokens),
    )


def _connection_error(_kwargs):
    raise ConnectionError("connection refused")


def _reset(monkeypatch, *, local=False):
    agent._breaker.reset()
    agent._CACHE.clear()
    monkeypatch.setattr(agent, "USE_LOCAL_LLM", local)
    monkeypatch.setattr(agent, "_local_client", None)


def test_config_defaults_keep_ollama_dormant():
    assert config.USE_LOCAL_LLM is False
    assert config.OLLAMA_BASE_URL == "http://localhost:11434/v1"
    assert config.OLLAMA_MODEL == "llama3"


def test_default_client_is_groq(monkeypatch):
    _reset(monkeypatch)
    groq = object()
    monkeypatch.setattr(agent, "_client", groq)

    assert agent._get_client() is groq


def test_local_mode_creates_an_ollama_client(monkeypatch):
    _reset(monkeypatch, local=True)
    created = {}

    class _OpenAI:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(agent, "OpenAI", _OpenAI)

    client = agent._get_client()

    assert isinstance(client, _OpenAI)
    assert created["api_key"] == "ollama"
    assert created["base_url"] == agent.OLLAMA_BASE_URL
    assert created["max_retries"] == 0


def test_default_run_agent_uses_groq(monkeypatch):
    _reset(monkeypatch)
    groq = _FakeClient(lambda _kwargs: _response("Groq answer."))
    local = _FakeClient(lambda _kwargs: _response("Local answer."))
    monkeypatch.setattr(agent, "_client", groq)
    monkeypatch.setattr(agent, "_local_client", local)

    reply, tokens = agent.run_agent("tell me something interesting")

    assert reply == "Groq answer."
    assert tokens == 1
    assert groq.calls == 1
    assert local.calls == 0


def test_local_mode_returns_a_friendly_error_when_ollama_is_down(monkeypatch):
    _reset(monkeypatch, local=True)
    local = _FakeClient(_connection_error)
    monkeypatch.setattr(agent, "_client", object())
    monkeypatch.setattr(agent, "_local_client", local)

    reply, tokens = agent.run_agent("tell me something interesting")

    assert reply == agent.LOCAL_AI_FALLBACK
    assert reply == (
        "Local AI is not running. Please start Ollama or check your connection."
    )
    assert tokens == 0
    assert local.calls == 1


def test_local_client_setup_error_is_graceful(monkeypatch):
    _reset(monkeypatch, local=True)
    monkeypatch.setattr(agent, "_client", object())

    def broken_client():
        raise ConnectionError("cannot construct client")

    monkeypatch.setattr(agent, "_get_client", broken_client)

    reply, tokens = agent.run_agent("tell me something interesting")

    assert reply == agent.LOCAL_AI_FALLBACK
    assert tokens == 0


def test_open_circuit_automatically_uses_local_mode(monkeypatch):
    _reset(monkeypatch)
    for _ in range(agent._breaker.failure_threshold):
        agent._breaker.record_failure()
    assert agent._breaker.state == resilience.OPEN
    local = object()
    monkeypatch.setattr(agent, "_local_client", local)

    assert agent._get_client() is local


def test_cli_local_flag_forces_local_mode(monkeypatch, capsys):
    monkeypatch.setattr(agent, "USE_LOCAL_LLM", False)
    monkeypatch.setattr(config, "USE_LOCAL_LLM", False)
    monkeypatch.setattr(sys, "argv", ["main.py", "--local", "--check"])
    monkeypatch.setattr("doctor.report", lambda: "readiness")

    main.main()

    assert agent.USE_LOCAL_LLM is True
    assert config.USE_LOCAL_LLM is True
    assert "readiness" in capsys.readouterr().out
