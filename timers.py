"""In-process timers / reminders for Jarvis.

A timer runs on a daemon thread and speaks through whatever speaker is
registered (``voice.speak`` in normal use). Kept tiny and defensive: a timer
thread must never crash the main loop.
"""

import threading

# Seconds a single timer/reminder is allowed to run for (guards bad input).
_MAX_SECONDS = 24 * 3600

_speaker = None
_timers = []


def _default_speaker(text):
    try:
        from voice import speak
        speak(text)
    except Exception:
        print(f"⏰ {text}", flush=True)


def set_speaker(fn):
    """Register the callable used to announce a fired timer."""
    global _speaker
    _speaker = fn


def _say(text):
    speaker = _speaker or _default_speaker
    try:
        speaker(text)
    except Exception:
        print(f"⏰ {text}", flush=True)


def set_timer(seconds, label="Timer"):
    """Start a countdown that speaks when it ends."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "How many seconds should the timer run for?"
    if seconds <= 0:
        return "Give me a positive number of seconds."
    seconds = min(seconds, _MAX_SECONDS)

    def fire():
        _say(f"{label}: time's up.")

    thread = threading.Timer(seconds, fire)
    thread.daemon = True
    thread.start()
    _timers.append(thread)
    return f"Timer set for {seconds:g} seconds."


def set_reminder(text, minutes=5):
    """Remind the user about ``text`` after ``minutes``."""
    text = (text or "").strip()
    if not text:
        return "What should I remind you about?"
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        return "How many minutes from now?"
    if minutes <= 0:
        return "Give me a positive number of minutes."

    def fire():
        _say(f"Reminder: {text}")

    thread = threading.Timer(minutes * 60.0, fire)
    thread.daemon = True
    thread.start()
    _timers.append(thread)
    return f"I'll remind you in {minutes:g} minutes."
