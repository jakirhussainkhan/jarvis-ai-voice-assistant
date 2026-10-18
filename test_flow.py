"""End-to-end dispatch tests through main.handle (confirmation, router, agent).

These exercise the *real* path used by main.py rather than calling run_agent
directly: a pending confirmation is resolved first, then try_local() is
consulted, and only a miss reaches the agent.
"""

import pytest

import confirm
import desktop
import main
from memory import Memory


@pytest.fixture(autouse=True)
def _clear_confirm():
    confirm.clear()
    yield
    confirm.clear()


def test_local_command_never_reaches_agent(monkeypatch):
    monkeypatch.setattr(desktop, "open_youtube", lambda: "Opening YouTube.")

    def fail(*args, **kwargs):
        raise AssertionError("agent must not run for a local command")

    monkeypatch.setattr(main, "run_agent", fail)
    assert main.handle("open youtube", Memory()) == ("Opening YouTube.", "local", 0)


def test_pending_confirmation_is_resolved_before_the_router(monkeypatch):
    ran = []
    confirm.request(lambda: ran.append(True) or "Shutting down.", "Are you sure?")

    def fail(*args, **kwargs):
        raise AssertionError("the router/agent must not run while confirming")

    monkeypatch.setattr(main, "run_agent", fail)
    monkeypatch.setattr(main, "try_local", fail)
    assert main.handle("yes", Memory()) == ("Shutting down.", "local", 0)
    assert ran == [True]
    confirm.clear()


def test_search_command_reaches_agent(monkeypatch):
    sent = []

    def fake_agent(text, memory):
        sent.append(text)
        return "Opened YouTube and searched.", 123

    monkeypatch.setattr(main, "run_agent", fake_agent)
    reply, kind, tokens = main.handle(
        "open YouTube and search for lo-fi music", Memory()
    )
    assert kind == "agent"
    assert reply == "Opened YouTube and searched."
    assert tokens == 123
    assert sent == ["open YouTube and search for lo-fi music"]


def test_agent_cache_hit_reports_cache(monkeypatch):
    monkeypatch.setattr(main, "run_agent", lambda text, memory: ("cached reply", 0))
    assert main.handle("tell me something unique", Memory()) == (
        "cached reply", "cache", 0,
    )


def test_agent_failure_is_caught_not_raised(monkeypatch):
    """A network error in the agent must not kill the session."""
    def boom(text, memory):
        raise RuntimeError("network down")

    monkeypatch.setattr(main, "run_agent", boom)
    reply, kind, tokens = main.handle("tell me a joke", Memory())
    assert "network down" in reply
    assert kind == "local" and tokens == 0


def test_text_mode_reads_a_typed_line(monkeypatch):
    monkeypatch.setattr(main, "_TEXT", True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "open youtube")
    assert main.read_input() == "open youtube"


def test_text_mode_eof_is_an_exit(monkeypatch):
    monkeypatch.setattr(main, "_TEXT", True)

    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert main.read_input() == "exit"


