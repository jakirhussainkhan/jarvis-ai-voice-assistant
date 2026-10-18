"""Tests for the readiness self-check (doctor.py). Read-only, no system access."""

import doctor


def test_report_lists_tools_and_text_mode(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: None)
    text = doctor.report()
    assert "Jarvis readiness check" in text
    assert "JARVIS_TEXT" in text                 # how to test without a mic
    assert "arecord" in text                     # required tool shown
    assert "xclip" in text                       # missing optional tools listed
    # Never suggests running sudo: the user asked for a sudo-free assistant.
    assert "sudo apt" not in text and "sudo install" not in text
    assert "--offline" in text and "--gui" in text


def test_report_flags_a_missing_api_key(monkeypatch):
    monkeypatch.setattr(doctor, "API_KEY", None)
    assert "MISSING" in doctor.report()


def test_report_flags_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: "/usr/bin/" + tool)
    assert "Wayland" in doctor.report()
