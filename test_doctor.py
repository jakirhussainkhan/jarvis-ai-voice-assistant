"""Tests for the readiness self-check (doctor.py). Read-only, no system access."""

import doctor


def test_report_lists_tools_and_text_mode(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: None)
    text = doctor.report()
    assert "Jarvis readiness check" in text
    assert "JARVIS_TEXT" in text                 # how to test without a mic
    assert "sudo apt install" in text            # missing optional tools listed
    assert "arecord" in text                     # required tool shown


def test_report_flags_a_missing_api_key(monkeypatch):
    monkeypatch.setattr(doctor, "API_KEY", None)
    assert "MISSING" in doctor.report()


def test_report_flags_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: "/usr/bin/" + tool)
    assert "Wayland" in doctor.report()
