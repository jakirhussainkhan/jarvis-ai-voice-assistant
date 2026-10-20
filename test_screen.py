"""Tests for screen / window interaction (platform adapters mocked).

The platform-specific commands now live in ``platform_adapters``; the actions in
``desktop.py`` are thin delegates. We therefore patch the shared
``platform_adapters.common`` seam (the single runner/which/session layer every
adapter uses) instead of the old ``desktop._run`` helpers.
"""

import desktop
from platform_adapters import common


def _record(monkeypatch, result=(0, "")):
    calls = []

    def fake_run(args, timeout=6):
        calls.append(args)
        return result

    monkeypatch.setattr(common, "run", fake_run)
    return calls


def _x11(monkeypatch, tools=("xdotool", "wmctrl")):
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(common, "has", lambda t: t in tools)


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #

def test_wayland_blocks_screen_control(monkeypatch):
    monkeypatch.setattr(common, "is_wayland", lambda: True)
    assert "wayland" in desktop.click_at(1, 2).lower()
    assert "wayland" in desktop.type_text("hi").lower()
    assert "wayland" in desktop.focus_window("x").lower()


def test_missing_xdotool_is_reported(monkeypatch):
    monkeypatch.setattr(common, "is_wayland", lambda: False)
    monkeypatch.setattr(common, "has", lambda t: False)
    assert "not installed" in desktop.click_at(1, 2).lower()


# --------------------------------------------------------------------------- #
# pointer / keyboard
# --------------------------------------------------------------------------- #

def test_click_at_uses_xdotool(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    assert desktop.click_at(100, 200) == "Left-clicked at 100, 200."
    assert calls == [["xdotool", "mousemove", "100", "200", "click", "1"]]


def test_double_click(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    desktop.click_at(1, 2, double=True)
    assert calls == [["xdotool", "mousemove", "1", "2", "click",
                      "--repeat", "2", "--delay", "120", "1"]]


def test_right_click_uses_button_3(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    desktop.click_at(3, 4, button=3)
    assert calls == [["xdotool", "mousemove", "3", "4", "click", "3"]]


def test_click_rejects_bad_button(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    desktop.click_at(1, 1, button="nonsense")
    assert calls[-1][-1] == "1"


def test_move_mouse(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    desktop.move_mouse(7, 8)
    assert calls == [["xdotool", "mousemove", "7", "8"]]


def test_type_text(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    desktop.type_text("hello world")
    assert calls == [["xdotool", "type", "--clearmodifiers", "--delay", "12",
                      "hello world"]]


def test_type_text_empty(monkeypatch):
    _x11(monkeypatch)
    assert "nothing to type" in desktop.type_text("").lower()


def test_press_keys(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    desktop.press_keys("ctrl+s")
    assert calls == [["xdotool", "key", "--clearmodifiers", "ctrl+s"]]


def test_press_keys_rejects_injection(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    assert "invalid" in desktop.press_keys("rm -rf; ls").lower()
    assert calls == []


def test_scroll(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    desktop.scroll("down", 3)
    assert calls == [["xdotool", "click", "--repeat", "3", "5"]]


def test_screen_size(monkeypatch):
    monkeypatch.setattr(common, "run", lambda args, timeout=6: (0, "1920 1200"))
    assert desktop.screen_size() == (1920, 1200)


# --------------------------------------------------------------------------- #
# window management
# --------------------------------------------------------------------------- #

def test_focus_window(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    assert desktop.focus_window("Firefox") == "Focused Firefox."
    assert calls == [["wmctrl", "-a", "Firefox"]]


def test_maximize_window(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    desktop.maximize_window("Firefox")
    assert calls == [
        ["wmctrl", "-r", "Firefox"],
        ["wmctrl", "-r", "Firefox", "-b", "add,maximized_vert,maximized_horz"],
    ]


def test_minimize_window(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    desktop.minimize_window("Firefox")
    assert calls == [
        ["wmctrl", "-r", "Firefox"],
        ["wmctrl", "-r", "Firefox", "-b", "add,hidden"],
    ]


def test_list_windows(monkeypatch):
    monkeypatch.setattr(desktop, "_window_titles", lambda: ["A", "B"])
    out = desktop.list_windows()
    assert "1. A" in out and "2. B" in out


# --------------------------------------------------------------------------- #
# paths / notifications / clipboard
# --------------------------------------------------------------------------- #

def test_open_path_url(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop, "_open_url", lambda u: opened.append(u) or True)
    assert "example.com" in desktop.open_path("example.com")
    assert opened == ["https://example.com"]


def test_open_path_existing_file(monkeypatch, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x")
    spawned = []
    monkeypatch.setattr(common, "has", lambda t: t == "xdg-open")
    monkeypatch.setattr(common, "spawn", lambda args: spawned.append(args) or True)
    desktop.open_path(str(target))
    assert spawned == [["xdg-open", str(target)]]


def test_open_path_empty():
    assert "what should i open" in desktop.open_path("").lower()


def test_notify(monkeypatch):
    spawned = []
    monkeypatch.setattr(common, "has", lambda t: t == "notify-send")
    monkeypatch.setattr(common, "spawn", lambda args: spawned.append(args) or True)
    assert desktop.notify("hello") == "Notification sent."
    assert spawned == [["notify-send", "JARVIS", "hello"]]


def test_clipboard_copy_sends_shortcut(monkeypatch):
    _x11(monkeypatch)
    calls = _record(monkeypatch)
    desktop.clipboard("copy")
    assert calls == [["xdotool", "key", "--clearmodifiers", "ctrl+c"]]


def test_clipboard_read_with_xclip(monkeypatch):
    monkeypatch.setattr(common, "has", lambda t: t == "xclip")
    monkeypatch.setattr(common, "run", lambda args, timeout=6: (0, "from xclip"))
    assert desktop.clipboard("read") == "from xclip"


def test_clipboard_read_without_xclip_uses_qt(monkeypatch):
    """Regression: reading the clipboard must still work with no xclip/xsel."""
    monkeypatch.setattr(common, "has", lambda t: False)
    monkeypatch.setattr(desktop, "_qt_clipboard_read", lambda: "hello from qt")
    assert desktop.clipboard("read") == "hello from qt"


def test_clipboard_read_when_nothing_is_available(monkeypatch):
    monkeypatch.setattr(common, "has", lambda t: False)
    monkeypatch.setattr(desktop, "_qt_clipboard_read", lambda: None)
    assert "can't read the clipboard" in desktop.clipboard("read")


def test_find_files(monkeypatch):
    monkeypatch.setattr(common, "has", lambda t: t == "plocate")
    monkeypatch.setattr(common, "run",
                        lambda args, timeout=10: (0, "/home/u/a.txt\n/home/u/b.txt"))
    out = desktop.find_files("txt")
    assert "a.txt" in out and "b.txt" in out


def test_find_files_empty_query():
    assert "what file" in desktop.find_files("").lower()


# --------------------------------------------------------------------------- #
# media extras / terminal / capture
# --------------------------------------------------------------------------- #

def test_media_seek(monkeypatch):
    """Regression: playerctl wants '10-' (suffix), not '-10' (an option flag)."""
    monkeypatch.setattr(common, "has", lambda t: t == "playerctl")
    calls = _record(monkeypatch)
    desktop.media_seek(-5)
    desktop.media_seek(30)
    assert calls == [["playerctl", "position", "5-"],
                     ["playerctl", "position", "30+"]]


def test_media_seek_zero_asks(monkeypatch):
    assert "how many seconds" in desktop.media_seek(0).lower()


def test_run_in_terminal(monkeypatch):
    spawned = []
    monkeypatch.setattr(common, "has", lambda t: t == "gnome-terminal")
    monkeypatch.setattr(common, "spawn", lambda args: spawned.append(args) or True)
    assert "echo hi" in desktop.run_in_terminal("echo hi")
    assert spawned and spawned[0][0] == "gnome-terminal"


def test_capture_screenshot_falls_back_to_scrot(monkeypatch, tmp_path):
    out = tmp_path / "s.png"

    def fake_run(args, timeout=15):
        if args[0] == "scrot":
            out.write_bytes(b"png-bytes")
            return 0, ""
        return 127, "no"

    monkeypatch.setattr(common, "has",
                        lambda t: t in ("gnome-screenshot", "scrot"))
    monkeypatch.setattr(common, "run", fake_run)
    assert desktop.capture_screenshot(out) == out


def test_capture_screenshot_prefers_a_silent_tool(monkeypatch, tmp_path):
    """gnome-screenshot flashes the screen, so it must never be tried first.

    Reading the screen (and every step of vision.act) uses this, and the flash is
    painful to look at.
    """
    out = tmp_path / "s.png"
    called = []

    def fake_run(args, timeout=15):
        called.append(args[0])
        if args[0] == "scrot":
            out.write_bytes(b"png-bytes")
            return 0, ""
        return 127, "no"

    monkeypatch.setattr(common, "has", lambda t: True)   # everything installed
    monkeypatch.setattr(common, "run", fake_run)

    assert desktop.capture_screenshot(out) == out
    assert called == ["scrot"], "a silent capture must be used, never a flash"
    assert "gnome-screenshot" not in called


def test_capture_screenshot_uses_gnome_only_as_a_last_resort(monkeypatch, tmp_path):
    out = tmp_path / "s.png"
    called = []

    def fake_run(args, timeout=15):
        called.append(args[0])
        desktop.Path(args[-1]).write_bytes(b"png-bytes")
        return 0, ""

    monkeypatch.setattr(common, "has", lambda t: t == "gnome-screenshot")
    monkeypatch.setattr(common, "run", fake_run)
    assert desktop.capture_screenshot(out) == out
    assert called == ["gnome-screenshot"]


def test_matching_process_pids_empty_query():
    assert desktop.matching_process_pids("") == []
