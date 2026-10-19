"""Tests for the single-instance lock + wake socket (instance.py, main.py).

Everything runs inside a temporary state directory (``JARVIS_STATE_DIR``), so
the suite never touches ``~/.jarvis`` or a real running Jarvis.
"""

import os
import sys
import time

import pytest

import instance
import main


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    """Point the lock/socket at a throwaway directory for every test."""
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    yield tmp_path


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
