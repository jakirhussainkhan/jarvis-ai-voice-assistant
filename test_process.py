"""Tests for the shared Windows ``subprocess`` console-hiding helper."""

import os
import subprocess
from types import SimpleNamespace

import pytest

from platform_adapters import common


def test_hidden_process_kwargs_is_empty_off_windows():
    if os.name == "nt":
        pytest.skip("Windows has console windows to hide")
    assert common.hidden_process_kwargs() == {}


def test_hidden_process_kwargs_hides_console_on_windows():
    if os.name != "nt":
        pytest.skip("CREATE_NO_WINDOW only exists on Windows")
    kwargs = common.hidden_process_kwargs()
    assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    info = kwargs["startupinfo"]
    assert isinstance(info, subprocess.STARTUPINFO)
    assert info.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert info.wShowWindow == subprocess.SW_HIDE


def test_common_run_and_spawn_pass_the_hidden_flags(monkeypatch):
    if os.name != "nt":
        pytest.skip("the flags only apply on Windows")

    captured = {}

    def fake_run(_args, **kwargs):
        captured["run"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok\n")

    def fake_popen(_args, **kwargs):
        captured["spawn"] = kwargs
        return SimpleNamespace(pid=1)

    monkeypatch.setattr(common.shutil, "which", lambda _tool: "cmd.exe")
    monkeypatch.setattr(common.subprocess, "run", fake_run)
    monkeypatch.setattr(common.subprocess, "Popen", fake_popen)

    assert common.run(["cmd.exe"]) == (0, "ok")
    assert common.spawn(["cmd.exe"]) is True

    for key in ("run", "spawn"):
        assert captured[key]["creationflags"] == subprocess.CREATE_NO_WINDOW
        assert captured[key]["startupinfo"].wShowWindow == subprocess.SW_HIDE
