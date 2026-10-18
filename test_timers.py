"""Tests for timers and reminders (timers.py)."""

import threading

import pytest

import timers


@pytest.fixture(autouse=True)
def _reset_speaker():
    timers.set_speaker(None)
    yield
    timers.set_speaker(None)


def test_timer_fires_and_speaks():
    said = []
    done = threading.Event()
    timers.set_speaker(lambda text: (said.append(text), done.set()))
    message = timers.set_timer(0.05, "Tea")
    assert "0.05" in message
    assert done.wait(2)
    assert said and "Tea" in said[0]


def test_reminder_fires_and_speaks():
    said = []
    done = threading.Event()
    timers.set_speaker(lambda text: (said.append(text), done.set()))
    timers.set_reminder("call mom", minutes=0.001)
    assert done.wait(2)
    assert said and "call mom" in said[0]


def test_timer_rejects_bad_input():
    assert "positive" in timers.set_timer(0).lower()
    assert "seconds" in timers.set_timer("abc").lower()


def test_reminder_requires_text():
    assert "what should i remind" in timers.set_reminder("").lower()
    assert "minutes" in timers.set_reminder("x", minutes=-1).lower()


def test_speaker_failure_is_swallowed():
    def boom(_text):
        raise RuntimeError("audio device gone")

    timers.set_speaker(boom)
    done = threading.Event()

    # A failing speaker must not propagate; the timer thread just prints.
    thread = threading.Timer(0.01, lambda: (timers._say("hi"), done.set()))
    thread.daemon = True
    thread.start()
    assert done.wait(2)
