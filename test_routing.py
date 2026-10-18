"""Tests for the offline router (router.try_local).

The router's job is decision-making, so these tests replace the :mod:`desktop`
actions with spies and assert which one the router chose, and with what
arguments. The real system behaviour is covered by test_desktop.py.
"""

from pathlib import Path

import pytest

import confirm
import desktop
import router

APP = desktop.App("google-chrome", "Chrome", "google-chrome", [], Path("/a/chrome.desktop"))


@pytest.fixture(autouse=True)
def _clear_confirm():
    confirm.clear()
    yield
    confirm.clear()


def _spy(monkeypatch, name, reply):
    """Replace desktop.<name> with a spy that records its calls."""
    calls = []

    def fn(*args, **kwargs):
        calls.append((args, kwargs))
        return reply

    monkeypatch.setattr(desktop, name, fn)
    return calls


# --------------------------------------------------------------------------- #
# basic local answers
# --------------------------------------------------------------------------- #

def test_empty_and_blank_are_not_handled():
    assert router.try_local("") is None
    assert router.try_local("   ") is None


def test_time_date_and_status_are_local():
    assert router.try_local("what time is it").startswith("Current time:")
    assert router.try_local("what's the date today").startswith("Today is ")
    assert router.try_local("system status").startswith("CPU ")


def test_battery_is_local():
    reply = router.try_local("battery")
    assert reply.endswith(".") and ("Battery" in reply or "No battery" in reply)


def test_search_verb_defers_to_agent():
    assert router.try_local("search for cats") is None
    assert router.try_local("look up the weather") is None
    assert router.try_local("find my notes") is None
    assert router.try_local("open YouTube and search for lo-fi music") is None


# --------------------------------------------------------------------------- #
# opening apps
# --------------------------------------------------------------------------- #

def test_open_app_is_local(monkeypatch):
    calls = _spy(monkeypatch, "open_app", "Opening Chrome.")
    assert router.try_local("open chrome") == "Opening Chrome."
    assert calls == [(("chrome",), {})]


def test_bare_app_name_is_local(monkeypatch):
    _spy(monkeypatch, "open_app", "Opening Chrome.")
    assert router.try_local("chrome") == "Opening Chrome."


def test_open_system_settings_is_an_app_not_a_status_query(monkeypatch):
    """Regression: bare 'system' used to be swallowed by the status branch."""
    calls = _spy(monkeypatch, "open_app", "Opening System Settings.")
    assert router.try_local("open system settings") == "Opening System Settings."
    assert calls == [(("system settings",), {})]


def test_open_system_monitor_is_an_app(monkeypatch):
    calls = _spy(monkeypatch, "open_app", "Opening System Monitor.")
    assert router.try_local("open system monitor") == "Opening System Monitor."
    assert calls == [(("system monitor",), {})]


def test_system_status_still_works(monkeypatch):
    reply = router.try_local("system status")
    assert reply.startswith("CPU ")


# --------------------------------------------------------------------------- #
# closing apps
# --------------------------------------------------------------------------- #

def test_close_app_strong_match(monkeypatch):
    monkeypatch.setattr(desktop, "match_app", lambda name: (APP, 0.95))
    calls = _spy(monkeypatch, "close_app", "Closed Chrome.")
    assert router.try_local("close chrome") == "Closed Chrome."
    assert calls == [((APP,), {})]


def test_quit_and_kill_are_close_commands(monkeypatch):
    monkeypatch.setattr(desktop, "match_app", lambda name: (APP, 0.95))
    _spy(monkeypatch, "close_app", "Closed Chrome.")
    assert router.try_local("quit chrome") == "Closed Chrome."
    assert router.try_local("kill chrome") == "Closed Chrome."


def test_weak_match_asks_before_closing(monkeypatch):
    monkeypatch.setattr(desktop, "match_app", lambda name: (APP, 0.60))
    calls = _spy(monkeypatch, "close_app", "Closed Chrome.")
    reply = router.try_local("close chorme")
    assert reply == "Did you mean Chrome?"
    assert calls == []                 # nothing closed until confirmed
    assert confirm.is_pending()

    handled, result = confirm.resolve("yes")
    assert handled and result == "Closed Chrome."
    assert calls == [((APP,), {})]


def test_close_unknown_target_defers(monkeypatch):
    monkeypatch.setattr(desktop, "match_app", lambda name: (None, 0.1))
    assert router.try_local("close the door") is None


def test_close_browser_targets_the_browser_app(monkeypatch):
    """Regression: 'browser' was stripped, so 'close browser' did nothing."""
    monkeypatch.setattr(desktop, "match_app", lambda name: (APP, 0.95))
    calls = _spy(monkeypatch, "close_app", "Closed Chrome.")
    assert router.try_local("close the browser") == "Closed Chrome."
    assert calls == [((APP,), {})]


def test_close_youtube_is_not_a_search(monkeypatch):
    """Regression: 'close youtube' must never run a YouTube search/play."""
    play = _spy(monkeypatch, "play_youtube", "SHOULD NOT HAPPEN")
    search = _spy(monkeypatch, "search_youtube", "SHOULD NOT HAPPEN")
    open_ = _spy(monkeypatch, "open_youtube", "SHOULD NOT HAPPEN")
    close = _spy(monkeypatch, "close_youtube", "Which window?")

    assert router.try_local("close youtube") == "Which window?"
    assert close == [((), {})]
    assert play == search == open_ == []


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #

def test_open_youtube(monkeypatch):
    _spy(monkeypatch, "open_youtube", "Opening YouTube.")
    assert router.try_local("open youtube") == "Opening YouTube."


def test_play_on_youtube_keeps_query(monkeypatch):
    calls = _spy(monkeypatch, "play_youtube", "Playing lo-fi on YouTube.")
    assert router.try_local("play lo-fi on youtube") == "Playing lo-fi on YouTube."
    assert calls == [(("lo-fi",), {})]


def test_play_extracts_song_from_a_messy_sentence(monkeypatch):
    """Regression: only the song name must reach play_youtube, not the sentence."""
    calls = _spy(monkeypatch, "play_youtube", "Playing udi udi on YouTube.")
    messy = ("play what do you mean music player i am i am just please the song "
             "udi udi on youtube")
    assert router.try_local(messy) == "Playing udi udi on YouTube."
    assert calls == [(("udi udi",), {})]


def test_play_without_a_song_asks_instead_of_searching(monkeypatch):
    calls = _spy(monkeypatch, "play_youtube", "SHOULD NOT HAPPEN")
    assert router.try_local("play the song on youtube") == "What should I play?"
    assert calls == []


def test_search_on_youtube_defers_to_agent(monkeypatch):
    # "search" is a search verb, so the router deliberately defers; the agent
    # calls fn_youtube(action="search") to open the results page.
    calls = _spy(monkeypatch, "search_youtube", "SHOULD NOT HAPPEN")
    assert router.try_local("search cats on youtube") is None
    assert calls == []


def test_bare_youtube_defers(monkeypatch):
    assert router.try_local("youtube") is None


# --------------------------------------------------------------------------- #
# media / volume / brightness / screenshot / lock
# --------------------------------------------------------------------------- #

def test_media_commands(monkeypatch):
    calls = _spy(monkeypatch, "media_control", "ok")
    assert router.try_local("pause the music") == "ok"
    assert router.try_local("next song") == "ok"
    assert calls[0] == (("pause",), {})
    assert calls[1] == (("next",), {})


def test_volume_up_and_set(monkeypatch):
    calls = _spy(monkeypatch, "volume_control", "Volume up.")
    assert router.try_local("volume up") == "Volume up."
    assert calls[0] == (("up", None), {})
    router.try_local("set volume to 150")
    assert calls[1] == (("set", 150), {})


def test_volume_mute_and_unmute(monkeypatch):
    calls = _spy(monkeypatch, "volume_control", "ok")
    router.try_local("mute")
    router.try_local("unmute")
    assert calls[0] == (("mute", None), {})
    assert calls[1] == (("unmute", None), {})


def test_volume_by_adjective(monkeypatch):
    """"make it louder" is a volume request even without the word "volume"."""
    calls = _spy(monkeypatch, "volume_control", "ok")
    assert router.try_local("make it louder") == "ok"
    assert router.try_local("a bit quieter") == "ok"
    assert calls[0] == (("up", None), {})
    assert calls[1] == (("down", None), {})


def test_brighter_and_darker(monkeypatch):
    calls = _spy(monkeypatch, "brightness_control", "ok")
    assert router.try_local("make it brighter") == "ok"
    assert router.try_local("make the screen darker") == "ok"
    assert calls[0] == (("up", None), {})
    assert calls[1] == (("down", None), {})


def test_brightness_up_and_set(monkeypatch):
    calls = _spy(monkeypatch, "brightness_control", "Brightness up.")
    assert router.try_local("brightness up") == "Brightness up."
    assert calls[0] == (("up", None), {})
    router.try_local("set brightness to 40")
    assert calls[1] == (("set", 40), {})


def test_screenshot_and_lock(monkeypatch):
    _spy(monkeypatch, "screenshot", "Screenshot saved as x.png.")
    _spy(monkeypatch, "lock_screen", "Locking the screen.")
    assert router.try_local("take a screenshot") == "Screenshot saved as x.png."
    assert router.try_local("lock the screen") == "Locking the screen."


def test_open_lock_screen_settings_does_not_lock(monkeypatch):
    lock = _spy(monkeypatch, "lock_screen", "Locking the screen.")
    opened = _spy(monkeypatch, "open_app", "Opening Settings.")
    assert router.try_local("open lock screen settings") == "Opening Settings."
    assert lock == []
    assert opened == [(("lock screen settings",), {})]


# --------------------------------------------------------------------------- #
# power: confirmation-gated
# --------------------------------------------------------------------------- #

def test_shutdown_asks_first_and_runs_after_yes(monkeypatch):
    calls = _spy(monkeypatch, "power", "Shutting down.")
    reply = router.try_local("shut down")
    assert reply == "Are you sure you want to shutdown?"
    assert calls == []                 # nothing runs yet
    assert confirm.is_pending()

    handled, result = confirm.resolve("yes")
    assert handled and result == "Shutting down."
    assert calls == [(("shutdown",), {})]


def test_restart_is_cancelled_by_anything_but_yes(monkeypatch):
    calls = _spy(monkeypatch, "power", "Restarting.")
    router.try_local("restart")
    assert confirm.resolve("no") == (True, "Cancelled.")
    assert calls == []


def test_power_word_with_a_target_is_not_power(monkeypatch):
    # "restart chrome" is about chrome, not the machine.
    assert router.try_local("restart chrome") is None
