"""Tests for the offline-first parts of the router.

The whole point of :mod:`router` is that a fixed, useful set of voice commands
works with no internet at all. These tests pin that set down: a dictated
terminal command, offline help, window listing, now-playing/seek, clipboard and
opening folders must all be answered locally (never by the agent), and they must
not hijack ordinary commands.
"""

from pathlib import Path

import pytest

import confirm
import desktop
import router
import shell


@pytest.fixture(autouse=True)
def _clear_confirm():
    confirm.clear()
    yield
    confirm.clear()


def _spy(monkeypatch, name, reply):
    calls = []

    def fn(*args, **kwargs):
        calls.append((args, kwargs))
        return reply

    monkeypatch.setattr(desktop, name, fn)
    return calls


# --------------------------------------------------------------------------- #
# offline help
# --------------------------------------------------------------------------- #

def test_help_lists_offline_capabilities():
    for phrase in ("help", "what can you do", "what can you do offline",
                   "list your commands", "what works offline",
                   "does it work without internet"):
        reply = router.try_local(phrase)
        assert reply == router.CAPABILITIES, phrase
        assert "no internet" in reply


def test_help_never_reaches_the_agent():
    # A plain capability question is answered locally, so it works offline.
    assert router.try_local("help") is not None


# --------------------------------------------------------------------------- #
# dictated terminal commands (offline)
# --------------------------------------------------------------------------- #

def test_marked_command_runs_locally(monkeypatch):
    calls = _spy_shell(monkeypatch)
    assert router.try_local("terminal ls -la") == "ran:ls -la"
    assert router.try_local("run the command df -h") == "ran:df -h"
    assert router.try_local("command pwd") == "ran:pwd"
    assert calls == [("ls -la",), ("df -h",), ("pwd",)]


def test_bare_run_is_left_to_the_agent():
    # No marker -> not a dictated command; the agent decides (it may open an app).
    assert router.try_local("run firefox") is None
    assert router.try_local("run ls") is None


def test_dictated_command_beats_offline_help(monkeypatch):
    # A command that happens to contain the word "offline" must still run.
    calls = _spy_shell(monkeypatch)
    assert router.try_local("terminal ping offline host") == "ran:ping offline host"
    assert calls == [("ping offline host",)]


def _spy_shell(monkeypatch):
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append((cmd,))
        return f"ran:{cmd}"

    monkeypatch.setattr(shell, "run", fake_run)
    return calls


# --------------------------------------------------------------------------- #
# windows
# --------------------------------------------------------------------------- #

def test_list_windows_is_local(monkeypatch):
    calls = _spy(monkeypatch, "list_windows", "1. Terminal")
    assert router.try_local("what windows are open") == "1. Terminal"
    assert router.try_local("list windows") == "1. Terminal"
    assert len(calls) == 2


def test_list_windows_does_not_hit_the_open_branch(monkeypatch):
    opened = _spy(monkeypatch, "open_app", "SHOULD NOT HAPPEN")
    _spy(monkeypatch, "list_windows", "1. x")
    router.try_local("what windows are open")
    assert opened == []


# --------------------------------------------------------------------------- #
# now playing / seek
# --------------------------------------------------------------------------- #

def test_now_playing_is_local(monkeypatch):
    calls = _spy(monkeypatch, "now_playing", "Nothing is playing.")
    assert router.try_local("what's playing") == "Nothing is playing."
    assert calls == [((), {})]


def test_seek_back_and_forward(monkeypatch):
    calls = _spy(monkeypatch, "media_seek", "ok")
    assert router.try_local("go back 10 seconds") == "ok"
    assert router.try_local("skip forward 30 seconds") == "ok"
    assert calls == [((-10,), {}), ((30,), {})]


def test_seek_wins_over_previous_track(monkeypatch):
    # "go back 10 seconds" must be a seek, not "previous track".
    seek = _spy(monkeypatch, "media_seek", "seek")
    media = _spy(monkeypatch, "media_control", "track")
    assert router.try_local("go back 10 seconds") == "seek"
    assert media == []
    assert seek == [((-10,), {})]


def test_next_still_changes_track(monkeypatch):
    calls = _spy(monkeypatch, "media_control", "ok")
    assert router.try_local("next song") == "ok"
    assert calls == [(("next",), {})]


# --------------------------------------------------------------------------- #
# clipboard
# --------------------------------------------------------------------------- #

def test_clipboard_copy_paste_cut(monkeypatch):
    calls = _spy(monkeypatch, "clipboard", "ok")
    assert router.try_local("copy") == "ok"
    assert router.try_local("paste") == "ok"
    assert router.try_local("cut that") == "ok"
    assert calls == [(("copy",), {}), (("paste",), {}), (("cut",), {})]


def test_clipboard_read(monkeypatch):
    calls = _spy(monkeypatch, "clipboard", "hello")
    assert router.try_local("read the clipboard") == "hello"
    assert router.try_local("what's in the clipboard") == "hello"
    assert calls == [(("read",), {}), (("read",), {})]


# --------------------------------------------------------------------------- #
# opening folders / paths (offline)
# --------------------------------------------------------------------------- #

def test_open_known_folder(monkeypatch):
    monkeypatch.setattr(desktop, "resolve_folder", lambda name: "/home/x/Downloads")
    calls = _spy(monkeypatch, "open_path", "Opening /home/x/Downloads.")
    assert router.try_local("open downloads") == "Opening /home/x/Downloads."
    assert calls == [(("/home/x/Downloads",), {})]


def test_open_folder_falls_back_to_app(monkeypatch):
    monkeypatch.setattr(desktop, "resolve_folder", lambda name: None)
    calls = _spy(monkeypatch, "open_app", "Opening Chrome.")
    assert router.try_local("open chrome") == "Opening Chrome."
    assert calls == [(("chrome",), {})]


def test_open_a_home_path(monkeypatch):
    monkeypatch.setattr(desktop, "resolve_folder", lambda name: None)
    calls = _spy(monkeypatch, "open_path", "Handing over.")
    assert router.try_local("open ~/notes.txt") == "Handing over."
    assert calls == [(("~/notes.txt",), {})]


def test_resolve_folder_real(tmp_path, monkeypatch):
    (tmp_path / "Downloads").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    assert desktop.resolve_folder("downloads") == str(tmp_path / "Downloads")
    assert desktop.resolve_folder("my downloads folder") == str(tmp_path / "Downloads")
    assert desktop.resolve_folder("no such folder") is None
