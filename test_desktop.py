"""Tests for desktop actions (desktop.py) with subprocess calls mocked.

Nothing here touches a real app, player, mixer or the network.
"""

from pathlib import Path

import pytest

import desktop
from platform_adapters import common, System

@pytest.fixture(autouse=True)
def _reset_voice_mute():
    """Keep the process-wide mic-mute state from leaking between tests."""
    import voice

    voice.set_mic_muted(False)
    voice._stop_listening.clear()
    yield
    voice.set_mic_muted(False)
    voice._stop_listening.clear()


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
# terminals: class-based close and dictation
# --------------------------------------------------------------------------- #

TERMINAL_APP = desktop.App(
    "org.gnome.Terminal", "Terminal", "gnome-terminal",
    ["shell"], Path("/a/org.gnome.Terminal.desktop"))

# Two real `wmctrl -lx` lines: the title is just the shell prompt, so a title
# match would never find the window.
WMCTRL_LX = (
    "0x03a00007  0 gnome-terminal-server.Gnome-terminal  "
    "jakir-host jakir@coder: ~\n"
    "0x04c0000a  0 firefox.Firefox  jakir-host Some page - Firefox\n"
)


def test_close_terminal_matches_the_window_class_not_the_title(monkeypatch):
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(common, "has", lambda tool: tool in ("wmctrl", "xdotool"))
    calls = []

    def fake_run(args, timeout=6):
        calls.append(args)
        if args[:2] == ["wmctrl", "-lx"]:
            return 0, WMCTRL_LX
        return 0, ""

    monkeypatch.setattr(common, "run", fake_run)

    assert desktop.close_terminal() == "Closing the terminal."
    assert ["wmctrl", "-ic", "0x03a00007"] in calls
    # The firefox window must never be touched.
    assert not any("0x04c0000a" in c for c in calls)


def test_close_terminal_reports_when_none_is_open(monkeypatch):
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(common, "has", lambda tool: tool == "wmctrl")
    monkeypatch.setattr(common, "run", lambda args, timeout=6: (0, ""))

    assert desktop.close_terminal() == "I don't see any terminal running."


def test_close_by_class_falls_back_to_xdotool_windowkill(monkeypatch):
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(common, "has", lambda tool: tool in ("wmctrl", "xdotool"))
    calls = []

    def fake_run(args, timeout=6):
        calls.append(args)
        if args[:2] == ["wmctrl", "-lx"]:
            return 0, WMCTRL_LX
        if args[:2] == ["wmctrl", "-ic"]:
            return 1, "close refused"
        if args[:2] == ["xdotool", "windowkill"]:
            return 0, ""
        return 0, ""

    monkeypatch.setattr(common, "run", fake_run)

    assert desktop.close_terminal() == "Closing the terminal."
    assert ["xdotool", "windowkill", "0x03a00007"] in calls


def test_mic_mute_never_touches_the_system_microphone(monkeypatch):
    """Regression: Jarvis's mic button must not mute the OS input source.

    Muting the system source lights the keyboard LED and silences other apps;
    Jarvis only stops its own capture stream.
    """
    import voice

    calls = []
    monkeypatch.setattr(common, "has", lambda tool: True)
    monkeypatch.setattr(common, "run",
                        lambda args, timeout=6: calls.append(args) or (0, ""))

    # There must be no adapter entry point for muting the system microphone.
    assert not hasattr(desktop.Audio, "set_source_mute")

    assert voice.set_mic_muted(True) is True
    assert voice.set_mic_muted(False) is False
    assert calls == []                 # no pactl (or any OS command) at all

    # Speaker mute is a different control and still uses the sink.
    desktop.Audio.set_mute(True)
    assert ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"] in calls


def test_close_app_closes_a_terminal_by_class(monkeypatch):
    """Regression: 'close terminal' used to say nothing was open."""
    closed = []
    monkeypatch.setattr(desktop, "Window",
                        type("W", (), {
                            "close_by_class": staticmethod(
                                lambda keys: closed.append(tuple(keys)) or True),
                        }))
    assert desktop.close_app(TERMINAL_APP) == "Closed Terminal."
    assert closed and "gnome-terminal" in closed[0]


def test_dictate_in_terminal_opens_types_and_enters(monkeypatch):
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(common, "has",
                        lambda tool: tool in ("xdotool", "gnome-terminal"))
    spawned = []
    monkeypatch.setattr(common, "spawn", lambda args: spawned.append(args) or True)
    calls = []
    monkeypatch.setattr(common, "run",
                        lambda args, timeout=6: calls.append(args) or (0, ""))
    monkeypatch.setattr(desktop.time, "sleep", lambda _s: None)

    reply = desktop.dictate_in_terminal("ls -la")

    assert reply == "Typed in the terminal: ls -la"
    assert spawned == [["gnome-terminal"]]
    assert ["xdotool", "type", "--clearmodifiers", "ls -la"] in calls
    assert ["xdotool", "key", "Return"] in calls


def test_dictate_in_terminal_needs_xdotool(monkeypatch):
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(common, "has", lambda tool: False)
    monkeypatch.setattr(common, "spawn",
                        lambda args: pytest.fail("must not open a terminal"))
    assert "xdotool" in desktop.dictate_in_terminal("ls")


def test_dictate_in_terminal_rejects_an_empty_command(monkeypatch):
    assert desktop.dictate_in_terminal("  ") == "What should I type in the terminal?"


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


def test_brightness_falls_back_to_xrandr_when_brightnessctl_missing(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "xrandr")
    calls = []

    def fake_run(args, timeout=6):
        calls.append(args)
        if args == ["xrandr"]:
            return 0, "eDP-1 connected primary 1920x1080+0+0\nHDMI-1 disconnected\n"
        return 0, ""

    monkeypatch.setattr(common, "run", fake_run)

    assert desktop.brightness_control("set", 40) == "Brightness set to 40 percent."
    assert ["xrandr", "--output", "eDP-1", "--brightness", "0.40"] in calls


def test_brightness_falls_back_to_xrandr_when_brightnessctl_fails(monkeypatch):
    """A permissions error from brightnessctl must not leave the screen stuck."""
    monkeypatch.setattr(common, "has",
                        lambda tool: tool in ("brightnessctl", "xrandr"))
    calls = []

    def fake_run(args, timeout=6):
        calls.append(args)
        if args and args[0] == "brightnessctl":
            return 1, "Permission denied"
        if args == ["xrandr"]:
            return 0, "eDP-1 connected primary 1920x1080+0+0\n"
        if args == ["xrandr", "--verbose"]:
            return 0, "\tBrightness: 0.50\n"
        return 0, ""

    monkeypatch.setattr(common, "run", fake_run)

    assert desktop.brightness_control("up") == "Brightness up."
    assert ["brightnessctl", "set", "+10%"] in calls
    assert ["xrandr", "--output", "eDP-1", "--brightness", "0.60"] in calls


def test_brightness_xrandr_picks_the_primary_connected_display(monkeypatch):
    from platform_adapters.linux import system as linux_system

    text = ("HDMI-1 connected 1920x1080+0+0\n"
            "eDP-1 connected primary 1366x768+0+0\n")
    assert linux_system._xrandr_display(text) == "eDP-1"
    # No primary flag -> the first connected output wins.
    assert linux_system._xrandr_display("DP-2 connected 1x1+0+0\n") == "DP-2"
    assert linux_system._xrandr_display("DP-1 disconnected\n") is None


def test_brightness_without_a_value_asks_instead_of_crashing(monkeypatch):
    """"brightness" (no number) must not reach int(None) in the xrandr path."""
    monkeypatch.setattr(common, "has", lambda tool: tool in ("brightnessctl", "xrandr"))
    monkeypatch.setattr(common, "run", lambda args, timeout=6: (0, ""))

    assert "brightness" in desktop.brightness_control("set", None).lower()


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


def test_media_pause_falls_back_to_space_in_the_browser(monkeypatch):
    """No MPRIS session, so playerctl can't pause: press space in the browser."""
    monkeypatch.setattr(common, "has", lambda tool: tool in ("playerctl", "xdotool"))
    monkeypatch.setattr(common, "run", lambda args, timeout=6: (1, ""))
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "123")
    pressed = []
    monkeypatch.setattr(desktop, "_press_in_window",
                        lambda wid, key, restore=True: pressed.append(key) or True)

    assert desktop.media_control("pause") == "Media pause."
    assert desktop.media_control("resume") == "Media resume."
    assert pressed == ["space", "space"]


def test_media_pause_is_honest_without_a_browser_window(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "playerctl")
    monkeypatch.setattr(common, "run", lambda args, timeout=6: (1, ""))
    monkeypatch.setattr(desktop, "_youtube_window", lambda: None)

    assert desktop.media_control("pause") == "There is no media player to control."


class _FakeClock:
    """A monotonic clock that only advances when something sleeps."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _fake_browser_run(statuses, calls=None):
    """A ``common.run`` fake for playerctl status + xdotool focus key presses.

    ``statuses`` are returned by successive ``playerctl status`` calls (the last
    one repeats). ``("xdotool", "key", "--clearmodifiers", "space")`` is recorded
    in ``calls`` when the helper actually presses Space.
    """
    calls = calls if calls is not None else []
    pending = list(statuses)

    def fake_run(args, timeout=25):
        args = tuple(args)
        calls.append(args)
        if args[:2] == ("playerctl", "status"):
            value = pending.pop(0) if len(pending) > 1 else pending[0]
            if value is None:                      # playerctl failed/timed out
                return 1, ""
            return 0, value
        if args[:2] in (("xdotool", "getactivewindow"),
                        ("xdotool", "getwindowfocus")):
            return 0, "555"
        return 0, ""

    return fake_run, calls


def test_ensure_browser_playing_presses_space_when_paused(monkeypatch):
    """A reused tab often lands paused; focus it and send ``xdotool key space``."""
    monkeypatch.setattr(common, "has",
                        lambda tool: tool in ("playerctl", "xdotool"))
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(desktop, "time", _FakeClock())
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "555")
    fake_run, calls = _fake_browser_run(["Paused", "Playing"])
    monkeypatch.setattr(common, "run", fake_run)

    assert desktop.ensure_browser_playing() is True
    assert ("xdotool", "key", "--clearmodifiers", "space") in calls
    assert ("xdotool", "windowactivate", "--sync", "555") in calls


def test_ensure_browser_playing_leaves_a_playing_video_alone(monkeypatch):
    """If playerctl says Playing, do not send a key (Space would pause it)."""
    monkeypatch.setattr(common, "has",
                        lambda tool: tool in ("playerctl", "xdotool"))
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(desktop, "time", _FakeClock())
    fake_run, calls = _fake_browser_run(["Playing"])
    monkeypatch.setattr(common, "run", fake_run)

    assert desktop.ensure_browser_playing() is True
    assert not any(c[:2] == ("xdotool", "key") for c in calls)
    assert not any(c[:2] == ("xdotool", "windowactivate") for c in calls)


def test_ensure_browser_playing_presses_space_when_playerctl_fails(monkeypatch):
    """No media session (autoplay blocked) -> press Space, then verify."""
    monkeypatch.setattr(common, "has",
                        lambda tool: tool in ("playerctl", "xdotool"))
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(desktop, "time", _FakeClock())
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "555")
    fake_run, calls = _fake_browser_run([None, "Playing"])
    monkeypatch.setattr(common, "run", fake_run)

    assert desktop.ensure_browser_playing() is True
    assert ("xdotool", "key", "--clearmodifiers", "space") in calls


def test_ensure_browser_playing_reports_still_paused(monkeypatch):
    """If Space did not start it, the caller must be able to tell the truth."""
    monkeypatch.setattr(common, "has",
                        lambda tool: tool in ("playerctl", "xdotool"))
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(desktop, "time", _FakeClock())
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "555")
    fake_run, _calls = _fake_browser_run(["Paused"])
    monkeypatch.setattr(common, "run", fake_run)

    assert desktop.ensure_browser_playing() is False


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


def test_extract_media_query_from_search_and_play():
    """The song sits before the play verb in "search <song> and play it"."""
    assert desktop.extract_media_query(
        "search despacito and play now") == "despacito"
    assert desktop.extract_media_query(
        "search for despacito on youtube and play it") == "despacito"
    assert desktop.extract_media_query(
        "look up lofi beats then play") == "lofi beats"
    assert desktop.extract_media_query(
        "search and play despacito") == "despacito"


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


def test_play_youtube_keeps_the_mic_and_media_alone(monkeypatch):
    """Playing YouTube must not mute the mic or pause background audio."""
    import voice

    monkeypatch.setattr(voice, "set_mic_muted",
                        lambda *a, **k: pytest.fail("must not mute the mic"))
    monkeypatch.setattr(voice, "pause_barge_in",
                        lambda: pytest.fail("must not pause barge-in"))
    monkeypatch.setattr(desktop, "pause_media_if_playing",
                        lambda: pytest.fail("must not pause media"))
    monkeypatch.setattr(common, "has", lambda tool: False)
    monkeypatch.setattr(desktop, "_open_url", lambda url: True)

    assert "YouTube results" in desktop.play_youtube("despacito")


def test_play_youtube_uses_top_ytdlp_result(monkeypatch):
    monkeypatch.setattr(common, "has", lambda tool: tool == "yt-dlp")
    seen = {}

    def fake_run(args, timeout=25):
        seen["args"] = args
        return 0, "dQw4w9WgXcQ"

    monkeypatch.setattr(common, "run", fake_run)
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr(desktop.time, "sleep", lambda _s: None)
    monkeypatch.setattr(desktop, "ensure_browser_playing", lambda: True)

    assert desktop.play_youtube("despacito") == "Playing despacito on YouTube."
    assert opened == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
    # Must resolve the search hit's id without a format selection (which fails
    # on YouTube with older yt-dlp builds).
    assert "--flat-playlist" in seen["args"]
    assert "--get-id" not in seen["args"]


def test_play_youtube_verifies_playback_before_reporting(monkeypatch):
    """The reply may only say "Playing" after ensure_browser_playing() ran."""
    monkeypatch.setattr(common, "has", lambda tool: tool == "yt-dlp")
    monkeypatch.setattr(common, "run", lambda args, timeout=25: (0, "dQw4w9WgXcQ"))
    monkeypatch.setattr(desktop.time, "sleep", lambda _s: None)
    events = []
    monkeypatch.setattr(desktop, "_navigate_youtube",
                        lambda url: events.append("navigated") or "opened")
    monkeypatch.setattr(desktop, "ensure_browser_playing",
                        lambda: events.append("verified") or True)

    assert desktop.play_youtube("despacito") == "Playing despacito on YouTube."
    assert events == ["navigated", "verified"]


def test_play_youtube_reports_blocked_autoplay_honestly(monkeypatch):
    """Never claim "Playing" when the player could not be started."""
    monkeypatch.setattr(common, "has", lambda tool: tool == "yt-dlp")
    monkeypatch.setattr(common, "run", lambda args, timeout=25: (0, "dQw4w9WgXcQ"))
    monkeypatch.setattr(desktop.time, "sleep", lambda _s: None)
    monkeypatch.setattr(desktop, "_navigate_youtube", lambda url: "opened")
    monkeypatch.setattr(desktop, "ensure_browser_playing", lambda: False)

    reply = desktop.play_youtube("despacito")
    assert "still paused" in reply
    assert not reply.startswith("Playing")


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
                        lambda: nudged.append(1) or True)

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


def test_open_youtube_never_opens_a_second_tab(monkeypatch):
    """If activation is refused, keep the existing tab -- never duplicate it."""
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "555")
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr(
        common, "run",
        lambda args, timeout=6: (0, {("xdotool", "getwindowfocus"): "999"}
                                 .get(tuple(args[:2]), "")))

    assert desktop.open_youtube() == (
        "YouTube is already open, but I couldn't bring it to the front.")
    assert opened == []


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


def test_play_youtube_never_duplicates_an_open_tab(monkeypatch):
    """A failed in-place navigation must not fall back to opening a new tab."""
    monkeypatch.setattr(common, "has", lambda tool: tool == "yt-dlp")
    monkeypatch.setattr(common, "run",
                        lambda args, timeout=25: (0, "dQw4w9WgXcQ"))
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "555")
    monkeypatch.setattr(desktop, "_reuse_youtube_tab", lambda url: False)
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)

    reply = desktop.play_youtube("despacito")
    assert "couldn't navigate" in reply
    assert opened == [], "webbrowser.open is forbidden while YouTube is open"


def test_search_youtube_never_duplicates_an_open_tab(monkeypatch):
    monkeypatch.setattr(desktop, "_youtube_window", lambda: "555")
    monkeypatch.setattr(desktop, "_reuse_youtube_tab", lambda url: False)
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)

    reply = desktop.search_youtube("cats")
    assert "couldn't navigate" in reply
    assert opened == []


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
