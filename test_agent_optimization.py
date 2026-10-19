"""Tests for the token-saving agent changes: intent pruning, the rate-limit
fallback model and the static (prefix-cacheable) system prompt. No network."""

import types

import agent


def _response(content, tool_calls=None, total_tokens=0):
    message = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = types.SimpleNamespace(message=message)
    usage = types.SimpleNamespace(total_tokens=total_tokens) if total_tokens else None
    return types.SimpleNamespace(choices=[choice], usage=usage)


class _FakeClient:
    """Minimal stand-in with the chat.completions.create surface the agent uses."""

    def __init__(self, handler):
        self.calls = []
        self._handler = handler
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._handler(kwargs)


# --------------------------------------------------------------------------- #
# intent classification + pruning
# --------------------------------------------------------------------------- #

def test_classify_intent_media():
    assert agent.classify_intent("play some lo-fi music") == "MEDIA"


def test_classify_intent_web():
    assert agent.classify_intent("what is the weather in Paris") == "WEB"


def test_classify_intent_vision():
    assert agent.classify_intent("click the login button on my screen") == "VISION"


def test_classify_intent_general():
    assert agent.classify_intent("tell me a joke") == "GENERAL"


def test_tools_for_prunes_to_a_small_relevant_set(monkeypatch):
    monkeypatch.setattr(agent, "TOOL_PRUNING", True)
    schemas = agent.tools_for("play a song")
    names = {schema["function"]["name"] for schema in schemas}
    assert names == {"fn_youtube", "fn_media", "fn_media_seek",
                     "fn_now_playing", "fn_volume"}
    assert len(schemas) < len(agent._TOOL_SCHEMAS)


def test_tools_for_can_be_disabled(monkeypatch):
    monkeypatch.setattr(agent, "TOOL_PRUNING", False)
    assert agent.tools_for("play a song") == agent._TOOL_SCHEMAS


def test_every_pruned_group_is_small_and_well_formed():
    for intent, schemas in agent._SCHEMAS_BY_INTENT.items():
        assert 3 <= len(schemas) <= 5, intent
        for schema in schemas:
            assert schema["type"] == "function"
            assert schema["function"]["name"].startswith("fn_")


# --------------------------------------------------------------------------- #
# static system prompt (prefix cache)
# --------------------------------------------------------------------------- #

def test_chat_sends_the_static_system_prompt_first(monkeypatch):
    client = _FakeClient(lambda _kwargs: _response("ok", total_tokens=3))
    monkeypatch.setattr(agent, "_client", client)

    reply, tokens = agent._chat("hello", agent.tools_for("hello"))

    assert reply == "ok"
    assert tokens == 3
    sent = client.calls[0]
    assert sent["messages"][0] == {
        "role": "system", "content": agent.SYSTEM_INSTRUCTION,
    }
    # The prompt is a constant -- no per-call data that would break the cache.
    assert agent.SYSTEM_INSTRUCTION is agent.SYSTEM_INSTRUCTION
    assert "openai/gpt-oss-120b" not in agent.SYSTEM_INSTRUCTION
    # The creator identity is a strict, always-present part of the prompt.
    assert "IDENTITY RULE:" in agent.SYSTEM_INSTRUCTION
    assert "Jakir Hussain Khan" in agent.SYSTEM_INSTRUCTION
    assert "Do not mention OpenAI, Groq" in agent.SYSTEM_INSTRUCTION


# --------------------------------------------------------------------------- #
# rate-limit fallback model
# --------------------------------------------------------------------------- #

def test_rate_limit_falls_back_to_the_smaller_model(monkeypatch):
    def handler(kwargs):
        if kwargs["model"] == "big-model":
            raise _RateLimit("rate limited")
        return _response("fallback reply")

    client = _FakeClient(handler)
    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr(agent, "MODEL_NAME", "big-model")
    monkeypatch.setattr(agent, "FALLBACK_MODEL", "small-model")

    reply, _tokens = agent._chat("hi")

    assert reply == "fallback reply"
    assert [call["model"] for call in client.calls] == ["big-model", "small-model"]


def test_non_rate_limit_error_is_not_swallowed(monkeypatch):
    def handler(_kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(agent, "_client", _FakeClient(handler))
    monkeypatch.setattr(agent, "MODEL_NAME", "big-model")
    monkeypatch.setattr(agent, "FALLBACK_MODEL", "small-model")

    try:
        agent._chat("hi")
    except ValueError as exc:
        assert "boom" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("a non-rate-limit error must propagate")


def test_is_rate_limit_detects_429():
    class Boom(Exception):
        status_code = 429

    assert agent._is_rate_limit(Boom("nope")) is True
    assert agent._is_rate_limit(ValueError("rate limit exceeded")) is True
    assert agent._is_rate_limit(ValueError("bad request")) is False


class _RateLimit(Exception):
    status_code = 429
