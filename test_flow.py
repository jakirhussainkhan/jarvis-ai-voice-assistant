"""End-to-end dispatch tests through main.handle (router first, then agent).

These exercise the *real* path used by main.py rather than calling run_agent
directly: try_local() is consulted first and only a miss reaches the agent.
"""

import main
import router
from memory import Memory


def test_local_command_never_reaches_agent(monkeypatch):
    monkeypatch.setattr(router, "_open", lambda url: True)

    def fail(*args, **kwargs):
        raise AssertionError("agent must not run for a local command")

    monkeypatch.setattr(main, "run_agent", fail)
    assert main.handle("open youtube", Memory()) == ("Opening YouTube.", "local", 0)


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
