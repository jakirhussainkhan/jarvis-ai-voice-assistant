"""Router tests for screen-reading phrases (vision) and their guards."""

import confirm
import desktop
import router
import vision


def setup_function(_fn):
    confirm.clear()


def teardown_function(_fn):
    confirm.clear()


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


# --------------------------------------------------------------------------- #
# screen actions (vision.act)
# --------------------------------------------------------------------------- #

def test_on_screen_actions_go_to_vision_act(monkeypatch):
    monkeypatch.setattr(vision, "act", lambda goal, **k: f"act:{goal}")
    phrases = (
        "play the video on my screen",
        "play that video on the screen",
        "click the play button on the screen",
        "open the first result on my display",
        "close that popup on the screen",
        "play the second song",
        "play this video",
    )
    for phrase in phrases:
        assert router.try_local(phrase) == f"act:{phrase}", phrase


def test_named_play_is_never_a_screen_action(monkeypatch):
    """A named song goes to yt-dlp -- it must not become a screen click."""
    monkeypatch.setattr(vision, "act",
                        lambda goal, **k: (_ for _ in ()).throw(AssertionError("screen")))
    calls = []
    monkeypatch.setattr(desktop, "play_youtube",
                        lambda q: calls.append(q) or f"play:{q}")
    assert router.try_local("play kesariya") == "play:kesariya"
    assert router.try_local("play lo-fi on youtube") == "play:lo-fi"
    assert calls == ["kesariya", "lo-fi"]


def test_risky_screen_goal_asks_first(monkeypatch):
    """A destructive on-screen goal is confirmed before it runs."""
    monkeypatch.setattr(vision, "act", lambda goal, **k: "acted")
    out = router.try_local("click the delete button on the screen")
    assert confirm.is_pending()
    assert "click the delete button on the screen" in out
    handled, reply = confirm.resolve("yes")
    assert handled and reply == "acted"
