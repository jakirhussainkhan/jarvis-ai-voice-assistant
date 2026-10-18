"""Tests for the readiness self-check (doctor.py). Read-only, no system access."""

import doctor


def test_report_lists_tools_and_text_mode(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: None)
    text = doctor.report()
    assert "Jarvis readiness check" in text
    assert "JARVIS_TEXT" in text                 # how to test without a mic
    assert "arecord" in text                     # required tool shown
    assert "xclip" in text                       # missing optional tools listed
    # It prints a copy-paste install command for the user to run themselves...
    assert "sudo apt install" in text
    # ...and says plainly that Jarvis never runs it for you.
    assert "never runs this" in text
    assert "--offline" in text and "--gui" in text


def test_report_flags_a_missing_api_key(monkeypatch):
    monkeypatch.setattr(doctor, "API_KEY", None)
    assert "MISSING" in doctor.report()


def test_report_flags_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: "/usr/bin/" + tool)
    assert "Wayland" in doctor.report()


# --------------------------------------------------------------------------- #
# wake word ("Hey Jarvis") status (v1.0.5)
# --------------------------------------------------------------------------- #

def test_report_explains_how_to_get_the_wake_word(monkeypatch):
    import wake

    monkeypatch.setattr(wake, "available", lambda: False)
    text = doctor.report()

    assert "Wake word: OFF" in text
    assert "pip install openwakeword" in text


def test_report_shows_the_wake_word_listening(monkeypatch):
    import wake

    monkeypatch.delenv("JARVIS_WAKE_MODEL", raising=False)
    monkeypatch.setattr(wake, "available", lambda: True)
    monkeypatch.setattr(wake, "enabled", lambda: True)

    assert 'listening for "hey jarvis"' in doctor.report()


def test_report_shows_the_wake_word_switched_off(monkeypatch):
    import wake

    monkeypatch.setattr(wake, "available", lambda: True)
    monkeypatch.setattr(wake, "enabled", lambda: False)

    assert "switched off" in doctor.report()
