"""Tests for desktop actions (desktop.py) with subprocess calls mocked.

Nothing here touches a real app, player, mixer or the network.
"""

from pathlib import Path

import desktop

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
    calls = []

    def fake_run(args, timeout=6):
        calls.append(args)
        return result

    monkeypatch.setattr(desktop, "_run", fake_run)
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
    monkeypatch.setattr(desktop, "_spawn", lambda args: launched.append(args) or True)
    assert desktop.open_app("zzzzzz") == "I couldn't find that app."
    assert launched == []


def test_open_app_launches_match(monkeypatch):
    _fake_index(monkeypatch)
    launched = []
    monkeypatch.setattr(desktop, "has", lambda tool: tool == "gtk-launch")
    monkeypatch.setattr(desktop, "_spawn", lambda args: launched.append(args) or True)
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
    monkeypatch.setattr(desktop, "_wmctrl_close", lambda name: False)
    monkeypatch.setattr(desktop, "_matching_pids", lambda app: [4242])
    signalled = {}

    def fake_terminate(pids, timeout=3.0):
        signalled["pids"] = pids
        return len(pids)

    monkeypatch.setattr(desktop, "_terminate", fake_terminate)
    app = desktop.App("firefox", "Firefox", "firefox", [], Path("/a/firefox.desktop"))
    assert desktop.close_app(app) == "Closed Firefox."
    assert signalled["pids"] == [4242]


def test_close_app_reports_not_running(monkeypatch):
    monkeypatch.setattr(desktop, "_wmctrl_close", lambda name: False)
    monkeypatch.setattr(desktop, "_matching_pids", lambda app: [])
    app = desktop.App("firefox", "Firefox", "firefox", [], Path("/a/firefox.desktop"))
    assert desktop.close_app(app) == "I don't see Firefox running."


# --------------------------------------------------------------------------- #
# volume / brightness limits
# --------------------------------------------------------------------------- #

def test_volume_clamps_to_0_100(monkeypatch):
    monkeypatch.setattr(desktop, "has", lambda tool: tool == "pactl")
    calls = _record_run(monkeypatch)
    assert desktop.volume_control("set", 150) == "Volume set to 100 percent."
    assert desktop.volume_control("set", -5) == "Volume set to 0 percent."
    assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"] in calls
    assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "0%"] in calls


def test_volume_up_down_mute(monkeypatch):
    monkeypatch.setattr(desktop, "has", lambda tool: tool == "pactl")
    calls = _record_run(monkeypatch)
    assert desktop.volume_control("up") == "Volume up."
    assert desktop.volume_control("down") == "Volume down."
    assert desktop.volume_control("mute") == "Muted."
    assert desktop.volume_control("unmute") == "Unmuted."
    assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"] in calls
    assert ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"] in calls


def test_volume_reports_missing_pactl(monkeypatch):
    monkeypatch.setattr(desktop, "has", lambda tool: False)
    assert "pactl is not installed" in desktop.volume_control("up")


def test_brightness_clamps_to_1_100(monkeypatch):
    monkeypatch.setattr(desktop, "has", lambda tool: tool == "brightnessctl")
    calls = _record_run(monkeypatch)
    assert desktop.brightness_control("set", 500) == "Brightness set to 100 percent."
    assert desktop.brightness_control("set", 0) == "Brightness set to 1 percent."
    assert ["brightnessctl", "set", "100%"] in calls


def test_brightness_reports_missing_tool(monkeypatch):
    monkeypatch.setattr(desktop, "has", lambda tool: False)
    assert "brightnessctl is not installed" in desktop.brightness_control("up")


# --------------------------------------------------------------------------- #
# media
# --------------------------------------------------------------------------- #

def test_media_control_maps_actions(monkeypatch):
    monkeypatch.setattr(desktop, "has", lambda tool: tool == "playerctl")
    calls = _record_run(monkeypatch)
    assert desktop.media_control("pause") == "Media pause."
    assert desktop.media_control("next") == "Media next."
    assert ["playerctl", "pause"] in calls
    assert ["playerctl", "next"] in calls


def test_media_reports_missing_playerctl(monkeypatch):
    monkeypatch.setattr(desktop, "has", lambda tool: False)
    assert "playerctl is not installed" in desktop.media_control("pause")


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #

def test_play_youtube_uses_top_ytdlp_result(monkeypatch):
    monkeypatch.setattr(desktop, "has", lambda tool: tool == "yt-dlp")
    monkeypatch.setattr(desktop, "_run", lambda args, timeout=25: (0, "dQw4w9WgXcQ"))
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)

    assert desktop.play_youtube("a song") == "Playing a song on YouTube."
    assert opened == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


def test_play_youtube_without_ytdlp_opens_search(monkeypatch):
    monkeypatch.setattr(desktop, "has", lambda tool: False)
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)

    reply = desktop.play_youtube("a song")
    assert "yt-dlp is not installed" in reply
    assert opened and "results?search_query=a+song" in opened[0]


def test_search_and_open_youtube(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda url: opened.append(url) or True)
    assert desktop.open_youtube() == "Opening YouTube."
    assert desktop.search_youtube("cats") == "Here are the YouTube results for cats."
    assert opened[0] == "https://www.youtube.com"
    assert "results?search_query=cats" in opened[1]


def test_close_youtube_asks_when_it_cannot_target(monkeypatch):
    monkeypatch.setattr(desktop, "is_wayland", lambda: False)
    monkeypatch.setattr(desktop, "has", lambda tool: False)  # no xdotool
    assert desktop.close_youtube() == "Which window?"


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

    monkeypatch.setattr(desktop, "has", lambda tool: tool == "gnome-screenshot")
    monkeypatch.setattr(desktop, "_run", fake_run)

    reply = desktop.screenshot()
    assert reply.startswith("Screenshot saved as jarvis-screenshot-")
    assert (tmp_path / "Pictures").is_dir()


def test_lock_screen_uses_first_available_locker(monkeypatch):
    monkeypatch.setattr(desktop, "has", lambda tool: tool == "loginctl")
    calls = _record_run(monkeypatch)
    assert desktop.lock_screen() == "Locking the screen."
    assert ["loginctl", "lock-session"] in calls
