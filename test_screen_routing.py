"""Router tests for screen-reading phrases (vision) and their guards."""

import desktop
import router
import vision


def test_screen_phrases_go_to_vision(monkeypatch):
    monkeypatch.setattr(vision, "look", lambda q=None: f"seen:{q}")
    for phrase in ("what's on my screen", "read the screen",
                   "describe my screen", "look at my monitor",
                   "show me what's on my screen"):
        assert router.try_local(phrase) == f"seen:{phrase}", phrase


def test_open_screen_settings_is_not_vision(monkeypatch):
    called = []
    monkeypatch.setattr(vision, "look", lambda q=None: called.append(q) or "v")
    monkeypatch.setattr(desktop, "open_app", lambda name: f"app:{name}")
    assert router.try_local("open screen settings") == "app:screen settings"
    assert called == []


def test_show_screen_recorder_is_not_vision(monkeypatch):
    called = []
    monkeypatch.setattr(vision, "look", lambda q=None: called.append(q) or "v")
    monkeypatch.setattr(desktop, "open_app", lambda name: f"app:{name}")
    router.try_local("show me the screen recorder")
    assert called == []


def test_plain_commands_are_unaffected(monkeypatch):
    monkeypatch.setattr(vision, "look", lambda q=None: "v")
    monkeypatch.setattr(desktop, "open_app", lambda name: f"app:{name}")
    assert router.try_local("open firefox") == "app:firefox"


def test_terminal_commands_reach_the_agent():
    for phrase in ("run ls", "execute pwd",
                   "what does the command ls do", "open a shell",
                   "run a script in the terminal"):
        assert router.try_local(phrase) is None, phrase


def test_open_terminal_still_opens_the_app(monkeypatch):
    monkeypatch.setattr(desktop, "open_app", lambda name: f"app:{name}")
    assert router.try_local("open terminal") == "app:terminal"


def test_run_an_app_is_left_to_the_agent(monkeypatch):
    # 'run firefox' is handled by the agent (fn_open_app), not mistaken for a
    # command with a weird target.
    monkeypatch.setattr(desktop, "open_app",
                        lambda name: (_ for _ in ()).throw(AssertionError("local")))
    assert router.try_local("run firefox") is None
