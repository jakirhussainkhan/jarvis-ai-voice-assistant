"""Tests for desktop actions (desktop.py) with subprocess calls mocked.

Nothing here touches a real app, player, mixer or the network.
"""

from pathlib import Path
import threading

import pytest

import desktop
from platform_adapters import common, System

@pytest.fixture(autouse=True)
def _reset_media_mic():
    """play_youtube mutes the process-wide mic; never leak that into other tests."""
    import voice
    voice.set_mic_muted(False)
    yield
    voice.set_mic_muted(False)


FAKE_APPS = [
    desktop.App("org.telegram.desktop", "Telegram Desktop", "telegram-desktop",
                ["chat", "messenger"], Path("/a/telegram.desktop")),
    desktop.App("org.gnome.Nautilus", "Files", "nautilus",
                ["file manager"], Path("/a/nautilus.desktop")),
    desktop.App("firefox", "Firefox", "firefox",
                ["browser", "web"], Path("/a/firefox.desktop")),
]


def _fake_index(monkeypatch):
    monkeypatch.setattr(desktop, "app_index", lambda refresh=False: FAKE_APPS)


def _record_run(monkeypatch, result=(0, "")):
    """Record every command issued through the shared platform runner."""
    calls = []

    def fake_run(args, timeout=6):
        calls.append(args)
        return result

    monkeypatch.setattr(common, "run", fake_run)
    return calls


# --------------------------------------------------------------------------- #
# app matching
# --------------------------------------------------------------------------- #

def test_match_app_exact_name(monkeypatch):
    _fake_index(monkeypatch)
    app, score = desktop.match_app("telegram")
    assert app is not None and app.name == "Telegram Desktop"
    assert score >= desktop.STRONG_MATCH


def test_match_app_matches_binary_and_generic(monkeypatch):
    _fake_index(monkeypatch)
    assert desktop.match_app("nautilus")[0].name == "Files"
    assert desktop.match_app("file manager")[0].name == "Files"


def test_match_app_returns_none_for_junk(monkeypatch):
    _fake_index(monkeypatch)
    app, _score = desktop.match_app("zzzzzz")
    assert app is None


def test_open_app_says_not_found_and_does_not_guess(monkeypatch):
    _fake_index(monkeypatch)
    launched = []
    monkeypatch.setattr(common, "spawn", lambda args: launched.append(args) or True)
    assert desktop.open_app("zzzzzz") == "I couldn't find that app."
    assert launched == []


def test_open_app_launches_match(monkeypatch):
    _fake_index(monkeypatch)
    launched = []
    monkeypatch.setattr(common, "has", lambda tool: tool == "gtk-launch")
    monkeypatch.setattr(common, "spawn", lambda args: launched.append(args) or True)
    assert desktop.open_app("telegram") == "Opening Telegram Desktop."
    assert launched == [["gtk-launch", "org.telegram.desktop"]]


# --------------------------------------------------------------------------- #
# protected list / closing
# --------------------------------------------------------------------------- #

def test_protected_names_are_recognised():
    assert desktop._is_protected("systemd")
    assert desktop._is_protected("cinnamon")
    assert desktop._is_protected("Xorg")
    assert desktop._is_protected("gnome-shell")
    assert not desktop._is_protected("firefox")


def test_jarvis_and_its_own_pid_are_protected():
    import os

    assert desktop._is_protected("python3-jarvis")
    assert desktop._is_protected("whatever", pid=os.getpid())


def test_close_app_refuses_protected_app(monkeypatch):
    app = desktop.App("jarvis", "Jarvis", "python3", [], Path("/a/jarvis.desktop"))
    assert desktop.close_app(app) == "I won't close Jarvis -- it is protected."


def test_close_app_terminates_match(monkeypatch):
    monkeypatch.setattr(System._window, "close_by_title", lambda name: False)
    monkeypatch.setattr(common, "matching_pids", lambda app: [4242])
    signalled = {}

    def fake_terminate(pids, timeout=3.0):
        signalled["pids"] = pids
        return len(pids)

    monkeypatch.setattr(common, "terminate", fake_terminate)
    app = desktop.App("firefox", "Firefox", "firefox", [], Path("/a/firefox.desktop"))
    assert desktop.close_app(app) == "Closed Firefox."
    assert signalled["pids"] == [4242]


def test_close_app_reports_not_running(monkeypatch):
    monkeypatch.setattr(System._window, "close_by_title", lambda name: False)
    monkeypatch.setattr(common, "matching_pids", lambda app: [])
    app = desktop.App("firefox", "Firefox", "firefox", [], Path("/a/firefox.desktop"))
    assert desktop.close_app(app) == "I don't see Firefox running."


# --------------------------------------------------------------------------- #
# volume / brightness limits
# --------------------------------------------------------------------------- #

def test_volume_clamps_to_0_100(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "pactl")
    calls = _record_run(monkeypatch)
    assert desktop.volume_control("set", 150) == "Volume set to 100 percent."
    assert desktop.volume_control("set", -5) == "Volume set to 0 percent."
    assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"] in calls
    assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "0%"] in calls


def test_volume_up_down_mute(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "pactl")
    calls = _record_run(monkeypatch)
    assert desktop.volume_control("up") == "Volume up."
    assert desktop.volume_control("down") == "Volume down."
    assert desktop.volume_control("mute") == "Muted."
    assert desktop.volume_control("unmute") == "Unmuted."
    assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"] in calls
    assert ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"] in calls


def test_volume_reports_missing_pactl(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: False)
    assert "pactl is not installed" in desktop.volume_control("up")


def test_brightness_clamps_to_1_100(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "brightnessctl")
    calls = _record_run(monkeypatch)
    assert desktop.brightness_control("set", 500) == "Brightness set to 100 percent."
    assert desktop.brightness_control("set", 0) == "Brightness set to 1 percent."
    assert ["brightnessctl", "set", "100%"] in calls


def test_brightness_reports_missing_tool(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: False)
    assert "brightnessctl is not installed" in desktop.brightness_control("up")


def test_volume_reports_audio_failure(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "pactl")
    monkeypatch.setattr(common, "run", lambda args, timeout=6: (1, ""))
    assert "audio system" in desktop.volume_control("up")
    assert "audio system" in desktop.volume_control("mute")


def test_brightness_reports_failure(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "brightnessctl")
    monkeypatch.setattr(common, "run", lambda args, timeout=6: (1, ""))
    assert "brightness" in desktop.brightness_control("down").lower()


# --------------------------------------------------------------------------- #
# media
# --------------------------------------------------------------------------- #

def test_media_control_maps_actions(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "playerctl")
    calls = _record_run(monkeypatch)
    assert desktop.media_control("pause") == "Media pause."
    assert desktop.media_control("next") == "Media next."
    assert ["playerctl", "pause"] in calls
    assert ["playerctl", "next"] in calls


def test_media_reports_missing_playerctl(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: False)
    assert "playerctl is not installed" in desktop.media_control("pause")


def test_media_next_falls_back_to_the_page_shortcut(monkeypatch):
    """Chromium/Brave report CanGoNext = false, so use YouTube's own shortcut."""
    monkeypatch.setattr(common, "has", lambda tool: tool in ("playerctl", "xdotool"))
    monkeypatch.setattr(common, "run",
                        lambda args, timeout=6: (1, "No player could handle this command"))
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "123")
    pressed = []
    monkeypatch.setattr(desktop, "_press_in_window",
                        lambda wid, key, restore=True: pressed.append((wid, key)) or True)

    assert desktop.media_control("next") == "Media next."
    assert desktop.media_control("previous") == "Media previous."
    assert pressed == [("123", "shift+n"), ("123", "shift+p")]


def test_media_next_without_a_page_is_honest(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "playerctl")
    monkeypatch.setattr(common, "run",
                        lambda args, timeout=6: (1, "No player could handle this command"))
    monkeypatch.setattr(desktop, "_youtube_window", lambda: None)
    assert desktop.media_control("next") == "That player can't skip tracks."


def test_media_reports_no_player(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "playerctl")
    monkeypatch.setattr(common, "run", lambda args, timeout=6: (1, ""))
    monkeypatch.setattr(desktop, "_youtube_window", lambda: None)
    assert desktop.media_control("pause") == "There is no media player to control."


def test_media_resume_does_not_claim_it_cannot_skip(monkeypatch):
    """The "no player could handle this command" wording only fits next/prev."""
    monkeypatch.setattr(common, "has", lambda tool: tool == "playerctl")
    monkeypatch.setattr(common, "run",
                        lambda args, timeout=6: (1, "No player could handle this command"))
    monkeypatch.setattr(desktop, "_youtube_window", lambda: None)
    assert desktop.media_control("resume") == "There is no media player to control."


def test_media_pause_never_toggles_a_stopped_video(monkeypatch):
    """`k` is a play/pause *toggle*: it must never be used to pause silence."""
    monkeypatch.setattr(common, "has", lambda tool: tool in ("playerctl", "xdotool"))
    monkeypatch.setattr(common, "run", lambda args, timeout=6: (1, ""))
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "123")
    pressed = []
    monkeypatch.setattr(desktop, "_press_in_window",
                        lambda wid, key, restore=True: pressed.append(key) or True)

    assert desktop.media_control("pause") == "There is no media player to control."
    assert pressed == []


class _FakeClock:
    """A monotonic clock that only advances when something sleeps."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_ensure_browser_playing_presses_k_once(monkeypatch):
    """A freshly opened tab does not autoplay; press its own play key once."""
    monkeypatch.setattr(common, "has", lambda tool: True)
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(desktop, "time", _FakeClock())
    monkeypatch.setattr(desktop, "_browser_media_player", lambda: False)
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "123")
    pressed = []
    monkeypatch.setattr(desktop, "_press_in_window",
                        lambda wid, key, restore=False: pressed.append(key) or True)

    assert desktop.ensure_browser_playing(timeout=1.0) is False
    assert pressed == ["k"]                 # once, not once per poll


def test_ensure_browser_playing_leaves_autoplay_alone(monkeypatch):
    """If the video already started, do not touch it (a key would pause it)."""
    monkeypatch.setattr(common, "has", lambda tool: True)
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(desktop, "time", _FakeClock())
    monkeypatch.setattr(desktop, "_browser_media_player", lambda: True)
    pressed = []
    monkeypatch.setattr(desktop, "_press_in_window",
                        lambda wid, key, restore=False: pressed.append(key) or True)

    assert desktop.ensure_browser_playing(timeout=9.0) is True
    assert pressed == []


def test_ensure_browser_playing_needs_x11_and_the_tools(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: False)      # no xdotool/playerctl
    monkeypatch.setattr(desktop, "_press_in_window",
                        lambda *a, **k: pytest.fail("must not press anything"))
    assert desktop.ensure_browser_playing() is False


def test_pause_media_if_playing_uses_playerctl(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "playerctl")
    calls = []

    def fake_run(args, timeout=6):
        calls.append(args)
        return (0, "Playing") if args == ["playerctl", "status"] else (0, "")

    monkeypatch.setattr(common, "run", fake_run)

    token = desktop.pause_media_if_playing()
    assert token == ("playerctl",)
    assert ["playerctl", "pause"] in calls

    desktop.resume_media(token)
    assert ["playerctl", "play"] in calls


def test_pause_media_skips_when_nothing_is_playing(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "playerctl")
    calls = []

    def fake_run(args, timeout=6):
        calls.append(args)
        return (0, "Paused") if args == ["playerctl", "status"] else (0, "")

    monkeypatch.setattr(common, "run", fake_run)

    assert desktop.pause_media_if_playing() is None
    assert ["playerctl", "pause"] not in calls


def test_pause_media_falls_back_to_pactl(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "pactl")
    calls = []

    def fake_run(args, timeout=6):
        calls.append(args)
        if args == ["pactl", "list", "sink-inputs"]:
            return 0, "Sink Input #9\n\tState: RUNNING\n"
        return 0, ""

    monkeypatch.setattr(common, "run", fake_run)

    token = desktop.pause_media_if_playing()
    assert token == ("pactl",)
    assert ["pactl", "suspend-sink", "@DEFAULT_SINK@", "1"] in calls

    desktop.resume_media(token)
    assert ["pactl", "suspend-sink", "@DEFAULT_SINK@", "0"] in calls


def test_pause_and_resume_media_never_raise(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: False)
    assert desktop.pause_media_if_playing() is None
    desktop.resume_media(None)           # no token -> no-op, must not raise


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #

def test_extract_media_query_basic():
    assert desktop.extract_media_query("play despacito on youtube") == "despacito"
    assert desktop.extract_media_query("listen to lo-fi on youtube") == "lo-fi"
    assert desktop.extract_media_query("watch Bohemian Rhapsody") == "Bohemian Rhapsody"
    assert desktop.extract_media_query("despacito") == "despacito"


def test_extract_media_query_from_messy_sentence():
    """Regression: a whole misheard sentence must collapse to the song name."""
    messy = ("play what do you mean music player i am i am just please the song "
             "udi udi on youtube")
    assert desktop.extract_media_query(messy) == "udi udi"


def test_extract_media_query_empty_when_only_filler():
    assert desktop.extract_media_query("play the song on youtube") == ""
    assert desktop.extract_media_query("play some music") == ""
    assert desktop.extract_media_query("") == ""


def test_extract_media_query_keeps_a_word_like_mean():
    """Only the phrase 'what do you mean'/'i mean' is filler, not the word."""
    assert desktop.extract_media_query("play mean on youtube") == "mean"


def test_extract_media_query_keeps_song_titles_intact():
    """Regression: title words ('the', 'like', 'hey', 'just', 'on', ...) are not
    filler -- stripping them played the wrong song (e.g. "Hey Jude" -> "jude")."""
    assert desktop.extract_media_query("play Hey Jude on youtube") == "Hey Jude"
    assert desktop.extract_media_query("play Like a Prayer on youtube") == "Like a Prayer"
    assert desktop.extract_media_query(
        "play Just the Way You Are on youtube") == "Just the Way You Are"
    assert desktop.extract_media_query("play The Nights on youtube") == "The Nights"
    assert desktop.extract_media_query("play on the floor on youtube") == "on the floor"
    assert desktop.extract_media_query("play Video Games on youtube") == "Video Games"


def test_extract_media_query_still_drops_filler_phrases():
    """A clear filler phrase is removed, but the title after it survives."""
    assert desktop.extract_media_query("play the song udi udi") == "udi udi"
    assert desktop.extract_media_query("play some music udi udi") == "udi udi"


def test_play_youtube_asks_when_query_is_empty(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: True)
    assert desktop.play_youtube("play the song on youtube") == "What should I play?"


def test_play_youtube_releases_the_mic_before_media(monkeypatch):
    """YouTube must not compete with barge-in for the audio device."""
    import voice

    calls = []
    monkeypatch.setattr(voice, "set_mic_muted",
                        lambda muted: calls.append(("muted", muted)) or muted)
    monkeypatch.setattr(voice, "pause_barge_in",
                        lambda: calls.append(("paused", None)))
    monkeypatch.setattr(common, "has", lambda tool: False)
    monkeypatch.setattr(desktop, "_open_url", lambda url: True)

    desktop.play_youtube("despacito")

    assert ("muted", True) in calls
    assert ("paused", None) in calls


def test_play_youtube_uses_top_ytdlp_result(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "yt-dlp")
    seen = {}

    def fake_run(args, timeout=25):
        seen["args"] = args
        return 0, "dQw4w9WgXcQ"

    monkeypatch.setattr(common, "run", fake_run)
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)

    assert desktop.play_youtube("despacito") == "Playing despacito on YouTube."
    assert opened == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
    # Must resolve the search hit's id without a format selection (which fails
    # on YouTube with older yt-dlp builds).
    assert "--flat-playlist" in seen["args"]
    assert "--get-id" not in seen["args"]


def test_play_youtube_nudges_playback_in_the_background(monkeypatch):
    """Autoplay is usually blocked, so start the video ourselves -- off-thread,
    so the spoken reply is never delayed."""
    monkeypatch.setattr(common, "has",
                        lambda tool: tool in ("yt-dlp", "playerctl"))
    monkeypatch.setattr(common, "run", lambda args, timeout=25: (0, "dQw4w9WgXcQ"))
    monkeypatch.setattr(desktop, "_open_url", lambda url: True)
    started = threading.Event()
    monkeypatch.setattr(desktop, "ensure_browser_playing",
                        lambda timeout=9.0: started.set() or True)

    assert desktop.play_youtube("despacito") == "Playing despacito on YouTube."
    assert started.wait(3.0), "the playback nudge should run in the background"


def test_play_youtube_reuses_the_open_youtube_tab(monkeypatch):
    """A YouTube tab that is already open must be used, never a second one."""
    monkeypatch.setattr(common, "has", lambda tool: tool in ("yt-dlp", "xdotool"))
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "555")
    calls = []

    def fake_run(args, timeout=25):
        calls.append(tuple(args))
        args = tuple(args)
        if args[0] == "yt-dlp":
            return 0, "dQw4w9WgXcQ"
        if args[:2] == ("xdotool", "getwindowfocus"):
            return 0, "555"
        if args[:2] == ("xdotool", "getwindowname"):
            # the first call is "before", the second "after" the navigation
            fake_run.names.append(args[-1])
            name = "YouTube - Brave" if len(fake_run.names) == 1 \
                else "Despacito - YouTube"
            return 0, name
        return 0, ""

    fake_run.names = []

    monkeypatch.setattr(common, "run", fake_run)
    monkeypatch.setattr(desktop.time, "sleep", lambda _s: None)
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)
    nudged = []
    monkeypatch.setattr(desktop, "ensure_browser_playing",
                        lambda timeout=9.0: nudged.append(1) or True)

    reply = desktop.play_youtube("despacito")
    assert reply == "Playing despacito in your open YouTube tab."
    assert opened == [], "no second tab may open"

    address_bar = [c for c in calls if c[:2] == ("xdotool", "key")
                   and "ctrl+l" in c]
    typed = [c for c in calls if c[:2] == ("xdotool", "type")]
    enter = [c for c in calls if c[:2] == ("xdotool", "key") and "Return" in c]
    activated = [c for c in calls if c[:2] == ("xdotool", "windowactivate")]
    assert len(address_bar) == 1
    assert len(enter) == 1
    assert any("watch?v=dQw4w9WgXcQ" in c[-1] for c in typed), typed
    assert any("555" in c for c in activated), calls
    assert nudged, "the playback nudge must still run for the reused tab"


def test_play_youtube_without_ytdlp_opens_search(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: False)
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)

    reply = desktop.play_youtube("despacito")
    assert "yt-dlp is not installed" in reply
    assert opened and "results?search_query=despacito" in opened[0]


def test_search_and_open_youtube(monkeypatch):
    monkeypatch.setattr(desktop, "_youtube_window", lambda: None)
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)
    assert desktop.open_youtube() == "Opening YouTube."
    assert desktop.search_youtube("cats") == "Here are the YouTube results for cats."
    assert opened[0] == "https://www.youtube.com"
    assert "results?search_query=cats" in opened[1]


def test_open_youtube_brings_the_existing_tab_to_the_front(monkeypatch):
    """"Open YouTube" while YouTube is open must not spawn a second tab."""
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "555")
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)
    called = []

    def fake_run(args, timeout=6):
        called.append(tuple(args))
        args = tuple(args)
        if args[:2] == ("xdotool", "getwindowfocus"):
            return 0, "555"          # the focus really moved
        return 0, ""

    monkeypatch.setattr(common, "run", fake_run)

    assert desktop.open_youtube() == "YouTube is already open -- I brought it up."
    assert opened == []
    assert any("555" in c for c in called), called


def test_open_youtube_falls_back_when_activation_is_refused(monkeypatch):
    """Cinnamon may refuse to raise the window: then open a new tab instead."""
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "555")
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr(
        common, "run",
        lambda args, timeout=6: (0, {("xdotool", "getwindowfocus"): "999"}
                                 .get(tuple(args[:2]), "")))

    assert desktop.open_youtube() == "Opening YouTube."
    assert opened == ["https://www.youtube.com"]


def test_search_youtube_reuses_the_open_youtube_tab(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "xdotool")
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "555")
    calls = []

    def fake_run(args, timeout=6):
        calls.append(tuple(args))
        args = tuple(args)
        if args[:2] == ("xdotool", "getwindowfocus"):
            return 0, "555"
        if args[:2] == ("xdotool", "getwindowname"):
            fake_run.names.append(args[-1])
            return 0, ("YouTube - Brave" if len(fake_run.names) == 1
                       else "cats - YouTube")
        return 0, ""

    fake_run.names = []
    monkeypatch.setattr(common, "run", fake_run)
    monkeypatch.setattr(desktop.time, "sleep", lambda _s: None)
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)

    assert desktop.search_youtube("cats") == "Searching YouTube for cats."
    assert opened == []
    typed = [c for c in calls if c[:2] == ("xdotool", "type")]
    assert any("results?search_query=cats" in c[-1] for c in typed), typed


def test_close_youtube_closes_the_open_youtube_tab_anywhere(monkeypatch):
    """The YouTube tab is closed from any window -- no "which window" nag."""
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(common, "has", lambda tool: tool == "xdotool")
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "555")
    pressed = []
    monkeypatch.setattr(desktop, "_press_in_window",
                        lambda wid, key, **kw: pressed.append((wid, key)) or True)

    assert desktop.close_youtube() == "Closing the YouTube tab."
    assert pressed == [("555", "ctrl+w")]


def test_close_youtube_never_closes_a_random_focused_tab(monkeypatch):
    """A browser focused on something else must be left alone, not ctrl+w'd."""
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(common, "has", lambda tool: tool == "xdotool")
    monkeypatch.setattr(desktop, "_youtube_window", lambda: None)
    monkeypatch.setattr(desktop, "_browser_media_player", lambda: False)
    monkeypatch.setattr(
        common, "run",
        lambda args, timeout=6: (0, "My important essay - Brave"))

    assert desktop.close_youtube() == "There is no YouTube tab open."


def test_close_youtube_closes_a_focused_playing_tab(monkeypatch):
    """No "- YouTube" suffix in the title, but the browser is playing: close it."""
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(common, "has", lambda tool: tool == "xdotool")
    monkeypatch.setattr(desktop, "_youtube_window", lambda: None)
    monkeypatch.setattr(desktop, "_browser_media_player", lambda: True)
    monkeypatch.setattr(
        common, "run",
        lambda args, timeout=6: (0, "Never Gonna Give You Up - Brave"))

    assert desktop.close_youtube() == "Closing the YouTube tab."


# --------------------------------------------------------------------------- #
# screenshot / lock
# --------------------------------------------------------------------------- #

def test_screenshot_reports_file_name(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.Path, "home", staticmethod(lambda: tmp_path))

    def fake_run(args, timeout=6):
        if args[0] == "gnome-screenshot":
            Path(args[-1]).write_bytes(b"png")
            return 0, ""
        return 0, ""

    monkeypatch.setattr(common, "has", lambda tool: tool == "gnome-screenshot")
    monkeypatch.setattr(common, "run", fake_run)

    reply = desktop.screenshot()
    assert reply.startswith("Screenshot saved as jarvis-screenshot-")
    assert (tmp_path / "Pictures").is_dir()


def test_lock_screen_uses_first_available_locker(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "loginctl")
    calls = _record_run(monkeypatch)
    assert desktop.lock_screen() == "Locking the screen."
    assert ["loginctl", "lock-session"] in calls
