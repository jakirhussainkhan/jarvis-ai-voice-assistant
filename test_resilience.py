"""Tests for the API circuit breaker and its agent integration (no network)."""

import types

import pytest

import agent
import resilience


class _Clock:
    """A deterministic monotonic clock for the breaker's cooldown."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def _clean_agent_state():
    """The breaker and reply cache are module globals; never leak state."""
    agent._breaker.reset()
    agent._CACHE.clear()
    yield
    agent._breaker.reset()
    agent._CACHE.clear()


# --------------------------------------------------------------------------- #
# breaker state machine
# --------------------------------------------------------------------------- #

def test_starts_closed_and_allows_requests():
    breaker = resilience.CircuitBreaker()
    assert breaker.state == resilience.CLOSED
    assert breaker.failures == 0
    assert breaker.allow_request() is True


def test_opens_after_three_consecutive_failures():
    breaker = resilience.CircuitBreaker(failure_threshold=3)

    for _ in range(2):
        assert breaker.allow_request() is True
        breaker.record_failure()

    assert breaker.state == resilience.CLOSED
    assert breaker.allow_request() is True
    breaker.record_failure()

    assert breaker.state == resilience.OPEN
    assert breaker.allow_request() is False


def test_success_resets_the_failure_streak():
    breaker = resilience.CircuitBreaker(failure_threshold=3)

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    assert breaker.failures == 0

    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == resilience.CLOSED
    breaker.record_failure()
    assert breaker.state == resilience.OPEN


def test_half_open_after_cooldown_allows_exactly_one_probe():
    clock = _Clock()
    breaker = resilience.CircuitBreaker(3, 30, clock=clock)

    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == resilience.OPEN

    clock.advance(29.9)
    assert breaker.allow_request() is False
    clock.advance(0.2)

    assert breaker.state == resilience.HALF_OPEN
    assert breaker.allow_request() is True
    assert breaker.allow_request() is False      # one probe at a time

    breaker.record_success()
    assert breaker.state == resilience.CLOSED
    assert breaker.allow_request() is True


def test_failed_half_open_probe_reopens_the_circuit():
    clock = _Clock()
    breaker = resilience.CircuitBreaker(3, 30, clock=clock)

    for _ in range(3):
        breaker.record_failure()
    clock.advance(31)

    assert breaker.allow_request() is True
    breaker.record_failure()
    assert breaker.state == resilience.OPEN
    assert breaker.allow_request() is False

    clock.advance(31)
    assert breaker.state == resilience.HALF_OPEN


# --------------------------------------------------------------------------- #
# error classification + agent integration
# --------------------------------------------------------------------------- #

class _ServerError(Exception):
    status_code = 503


def test_is_transient_classifies_outages_not_client_errors():
    assert agent._is_transient(_ServerError("down")) is True
    assert agent._is_transient(TimeoutError("request timed out")) is True
    assert agent._is_transient(ConnectionError("connection reset")) is True

    assert agent._is_transient(ValueError("bad request")) is False

    class RateLimit(Exception):
        status_code = 429

    assert agent._is_transient(RateLimit("slow down")) is False


def _response(content, total_tokens=1):
    message = types.SimpleNamespace(content=content, tool_calls=None)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=message)],
        usage=types.SimpleNamespace(total_tokens=total_tokens),
    )


class _FakeClient:
    def __init__(self, handler):
        self.calls = 0
        self._handler = handler
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        return self._handler(kwargs)


def _always_fail(_kwargs):
    raise _ServerError("upstream is down")


def test_three_transient_failures_open_the_breaker(monkeypatch):
    client = _FakeClient(_always_fail)
    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr("router.try_local", lambda _text: None)

    for i in range(3):
        with pytest.raises(_ServerError):
            agent.run_agent(f"query {i}")

    assert agent._breaker.state == resilience.OPEN
    assert client.calls == 3

    # While open, the API is skipped entirely and the user hears a graceful reply.
    reply, tokens = agent.run_agent("query 3")
    assert client.calls == 3
    assert reply == agent.API_FALLBACK
    assert tokens == 0


def test_open_circuit_routes_to_the_local_router(monkeypatch):
    client = _FakeClient(_always_fail)
    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr("router.try_local", lambda _text: None)

    for i in range(3):
        with pytest.raises(_ServerError):
            agent.run_agent(f"query {i}")

    monkeypatch.setattr(
        "router.try_local", lambda _text: "It is four o'clock, sir.")
    reply, tokens = agent.run_agent("what time is it")

    assert reply == "It is four o'clock, sir."
    assert tokens == 0
    assert client.calls == 3
    # A transient fallback must not poison the cache for the next real attempt.
    assert agent._cache_key("what time is it") not in agent._CACHE


def test_success_resets_the_streak_and_closes_the_circuit(monkeypatch):
    replies = [
        _ServerError("down"),
        _ServerError("down"),
        _response("hello", total_tokens=7),
        _ServerError("down"),
        _ServerError("down"),
    ]

    def handler(_kwargs):
        item = replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = _FakeClient(handler)
    monkeypatch.setattr(agent, "_client", client)

    for i in range(2):
        with pytest.raises(_ServerError):
            agent.run_agent(f"failing {i}")
    assert agent._breaker.failures == 2

    assert agent.run_agent("working query") == ("hello", 7)
    assert agent._breaker.failures == 0
    assert agent._breaker.state == resilience.CLOSED

    for i in range(2):
        with pytest.raises(_ServerError):
            agent.run_agent(f"failing again {i}")
    assert agent._breaker.failures == 2
    assert agent._breaker.state == resilience.CLOSED


def test_half_open_probe_success_closes_the_circuit(monkeypatch):
    clock = _Clock()
    breaker = resilience.CircuitBreaker(3, 30, clock=clock)
    monkeypatch.setattr(agent, "_breaker", breaker)
    client = _FakeClient(lambda _kwargs: _response("the API is back"))
    monkeypatch.setattr(agent, "_client", client)

    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == resilience.OPEN
    clock.advance(30)

    reply, _tokens = agent.run_agent("probe please")

    assert reply == "the API is back"
    assert client.calls == 1
    assert breaker.state == resilience.CLOSED


def test_half_open_probe_failure_reopens_the_circuit(monkeypatch):
    clock = _Clock()
    breaker = resilience.CircuitBreaker(3, 30, clock=clock)
    monkeypatch.setattr(agent, "_breaker", breaker)
    client = _FakeClient(_always_fail)
    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr("router.try_local", lambda _text: None)

    for _ in range(3):
        breaker.record_failure()
    clock.advance(30)

    with pytest.raises(_ServerError):
        agent.run_agent("let us probe")
    assert breaker.state == resilience.OPEN
    assert client.calls == 1

    reply, _tokens = agent.run_agent("after the failed probe")
    assert reply == agent.API_FALLBACK
    assert client.calls == 1
