"""Tests for the readiness self-check (doctor.py). Read-only, no system access."""

import pytest

import doctor


@pytest.fixture(autouse=True)
def _ollama_is_offline(monkeypatch):
    """Keep the readiness tests deterministic and off the network."""
    monkeypatch.setattr(doctor, "_ollama_running", lambda: False)


def test_report_lists_tools_and_text_mode(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: None)
    text = doctor.report(system="Linux")
    assert "Jarvis readiness check" in text
    assert "JARVIS_TEXT" in text                 # how to test without a mic
    assert "arecord" in text                     # required tool shown
    assert "xclip" in text                       # missing optional tools listed
    # It prints a copy-paste install command for the user to run themselves...
    assert "sudo apt install" in text
    # ...and says plainly that Jarvis never runs it for you.
    assert "never runs this" in text
    assert "--offline" in text and "--gui" in text


def test_report_uses_portaudio_on_windows(monkeypatch):
    """Windows must not be told that arecord (an ALSA tool) is required."""
    text = doctor.report(system="Windows")

    assert "Platform: Windows" in text
    assert "Microphone:" in text                 # PortAudio status, never arecord
    assert "arecord" not in text
    assert "sudo apt install" not in text
    assert "pycaw" in text                       # volume needs it on Windows
    assert "--quit" in text                      # how to stop the tray app


def test_windows_report_flags_missing_sounddevice(monkeypatch):
    real = doctor._has_module
    monkeypatch.setattr(
        doctor, "_has_module",
        lambda name: False if name == "sounddevice" else real(name))
    assert "MISSING sounddevice" in doctor.report(system="Windows")


def test_report_flags_a_missing_api_key(monkeypatch):
    monkeypatch.setattr(doctor, "API_KEY", None)
    assert "MISSING" in doctor.report()


def test_report_flags_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: "/usr/bin/" + tool)
    assert "Wayland" in doctor.report(system="Linux")


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


# --------------------------------------------------------------------------- #
# optional Ollama backend
# --------------------------------------------------------------------------- #

def test_report_reports_ollama_running(monkeypatch):
    monkeypatch.setattr(doctor, "_ollama_running", lambda: True)
    assert "Ollama (Local AI): OK (Running)" in doctor.report()


def test_report_reports_ollama_not_running():
    assert ("Ollama (Local AI): Not running "
            "(Optional - used only if USE_LOCAL_LLM=True)") in doctor.report()


def test_ollama_probe_uses_the_server_root(monkeypatch):
    monkeypatch.setattr(doctor, "OLLAMA_BASE_URL", "http://localhost:11434/v1")
    assert doctor._ollama_root_url() == "http://localhost:11434"
