"""Tests for the single-instance lock + wake socket (instance.py, main.py).

Everything runs inside a temporary state directory (``JARVIS_STATE_DIR``), so
the suite never touches ``~/.jarvis`` or a real running Jarvis.
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

import instance
import main


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    """Point the lock/socket at a throwaway directory for every test.

    macOS caps an AF_UNIX path at 104 bytes, and pytest's nested ``tmp_path``
    can be longer than that. When it would be, use a short directory under
    ``/tmp`` so the test exercises the real socket instead of skipping it.
    """
    directory = tmp_path
    if sys.platform == "darwin" and len(str(tmp_path / "jarvis_dev.sock")) > 100:
        directory = Path(tempfile.mkdtemp(prefix="jv-", dir="/tmp"))
    monkeypatch.setenv("JARVIS_STATE_DIR", str(directory))
    try:
        yield directory
    finally:
        if directory != tmp_path:
            shutil.rmtree(directory, ignore_errors=True)


# --------------------------------------------------------------------------- #
# lock file
# --------------------------------------------------------------------------- #

def test_acquire_writes_the_pid_and_releases(_state_dir):
    handle = instance.acquire()
    try:
        assert instance.lock_path().exists()
        assert str(os.getpid()) in instance.lock_path().read_text()
    finally:
        instance.release(handle)
    # Released -> a fresh acquire succeeds.
    again = instance.acquire()
    instance.release(again)


def test_second_launch_is_refused_while_locked(_state_dir):
    handle = instance.acquire()
    try:
        with pytest.raises(instance.AlreadyRunning):
            instance.acquire()
    finally:
        instance.release(handle)


def test_release_is_idempotent(_state_dir):
    handle = instance.acquire()
    instance.release(handle)
    instance.release(handle)          # must not raise
    instance.release(None)            # nor on a null handle


def test_frozen_and_source_use_separate_paths_and_ports(_state_dir, monkeypatch):
    """The packaged EXE and a checkout must not share lock/socket endpoints."""
    assert instance.lock_path().name == "jarvis_dev.lock"
    assert Path(instance.socket_path()).name == "jarvis_dev.sock"
    assert Path(instance.port_path()).name == "jarvis_dev.port"
    assert instance.wake_port() == 12346

    monkeypatch.setattr(instance.sys, "frozen", True, raising=False)

    assert instance.lock_path().name == "jarvis_exe.lock"
    assert Path(instance.socket_path()).name == "jarvis_exe.sock"
    assert Path(instance.port_path()).name == "jarvis_exe.port"
    assert instance.wake_port() == 12345


def test_frozen_and_source_locks_can_be_held_together(_state_dir, monkeypatch):
    """Both modes may run at once, but a second instance of one mode may not."""
    dev = instance.acquire()
    try:
        monkeypatch.setattr(instance.sys, "frozen", True, raising=False)
        exe = instance.acquire()
        try:
            with pytest.raises(instance.AlreadyRunning):
                instance.acquire()
        finally:
            instance.release(exe)
    finally:
        instance.release(dev)


# --------------------------------------------------------------------------- #
# wake socket
# --------------------------------------------------------------------------- #

def test_wake_server_delivers_the_message(_state_dir):
    seen = []
    server = instance.WakeServer(lambda: seen.append(True))
    assert server.start() is True
    try:
        assert instance.signal_wake() is True
        deadline = time.time() + 2.0
        while not seen and time.time() < deadline:
            time.sleep(0.01)
        assert seen, "the wake callback should have fired"
    finally:
        server.stop()
    # Stopping removes the socket; signalling afterwards fails cleanly.
    assert not os.path.exists(instance.socket_path())
    assert instance.signal_wake(timeout=0.2) is False


def test_signal_wake_without_a_listener_is_false(_state_dir):
    assert instance.signal_wake(timeout=0.2) is False


def test_dev_and_exe_wake_servers_are_independent(_state_dir, monkeypatch):
    """A wake signal must only reach the instance mode that sent it."""
    monkeypatch.setattr(instance.sys, "frozen", False, raising=False)
    seen = {"dev": 0, "exe": 0}
    dev = instance.WakeServer(lambda: seen.__setitem__("dev", seen["dev"] + 1))
    assert dev.start() is True
    monkeypatch.setattr(instance.sys, "frozen", True, raising=False)
    exe = instance.WakeServer(lambda: seen.__setitem__("exe", seen["exe"] + 1))
    assert exe.start() is True

    def wait_for(key):
        deadline = time.time() + 2.0
        while not seen[key] and time.time() < deadline:
            time.sleep(0.01)

    try:
        assert instance.signal_wake() is True
        wait_for("exe")
        assert seen == {"dev": 0, "exe": 1}

        monkeypatch.setattr(instance.sys, "frozen", False, raising=False)
        assert instance.signal_wake() is True
        wait_for("dev")
        assert seen == {"dev": 1, "exe": 1}
    finally:
        dev.stop()
        exe.stop()


def test_a_stale_pid_never_signals_an_unrelated_process(_state_dir):
    """A recycled PID must not be killed by the Windows fallback path."""
    instance.lock_path().write_text(str(os.getpid()), encoding="ascii")
    assert instance._is_jarvis_pid(os.getpid()) is False
    assert instance.signal_quit(timeout=0.1) is False
    assert instance._is_jarvis_pid(999_999_999) is False


def test_wake_server_stop_is_idempotent(_state_dir):
    server = instance.WakeServer(lambda: None)
    server.start()
    server.stop()
    server.stop()                     # must not raise


# --------------------------------------------------------------------------- #
# main.py integration
# --------------------------------------------------------------------------- #

def test_wake_flag_signals_the_running_instance(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--wake"])
    monkeypatch.setattr(instance, "signal_wake", lambda *a, **k: True)
    started = []
    monkeypatch.setattr(instance, "acquire",
                        lambda: started.append("acquire") or 0)

    main.main()

    assert "wake up" in capsys.readouterr().out
    assert started == [], "a second instance must not be started"


def test_second_gui_launch_wakes_instead_of_starting(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--gui"])
    monkeypatch.setattr(instance, "signal_wake", lambda *a, **k: True)
    handle = instance.acquire()       # pretend Jarvis is already running
    try:
        main.main()
    finally:
        instance.release(handle)
    assert "wake up" in capsys.readouterr().out


def test_second_launch_reports_when_wake_fails(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["main.py", "--gui"])
    monkeypatch.setattr(instance, "signal_wake", lambda *a, **k: False)
    handle = instance.acquire()
    try:
        main.main()
    finally:
        instance.release(handle)
    assert "already running" in capsys.readouterr().out
