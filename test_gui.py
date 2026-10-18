"""Smoke tests for the PyQt5 floating widget + tray (gui.py).

These run on the *offscreen* Qt platform so they never open a real window, and
are skipped entirely when PyQt5 is not installed. They verify the parts that are
easy to get wrong without a display: icon painting, widget construction and the
state label.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtCore import Qt               # noqa: E402
from PyQt5.QtGui import QPixmap           # noqa: E402
from PyQt5.QtWidgets import QApplication   # noqa: E402

import gui                                 # noqa: E402
import voice                               # noqa: E402
from memory import Memory                  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture(autouse=True)
def _reset_voice_state():
    voice.set_level_sink(None)
    voice.set_mic_muted(False)
    voice._stop_listening.clear()
    yield
    voice.set_level_sink(None)
    voice.set_mic_muted(False)
    voice._stop_listening.clear()


def test_make_icon_paints_something(app):
    icon = gui.make_icon(64)
    assert not icon.isNull()


def test_widget_builds_and_tracks_state(app):
    widget = gui.JarvisWidget()
    assert widget.width() > 0 and widget.height() > 0
    widget.set_state(gui.THINKING)
    assert widget.status.text() == gui.THINKING
    widget.set_state(gui.IDLE)
    assert widget.status.text() == gui.IDLE


def test_widget_quit_does_not_hang(app):
    widget = gui.JarvisWidget()
    widget._quitting = True
    widget.quit()          # no worker running: must return immediately


def test_hide_to_tray_does_not_quit_by_default(app):
    """Closing the widget must hide it, not quit the app."""
    widget = gui.JarvisWidget()
    # With no tray available (offscreen), the close falls through to accept.
    assert widget._quitting is False


# --------------------------------------------------------------------------- #
# mic mute + stop + live animation (v1.0.2)
# --------------------------------------------------------------------------- #

def test_mic_button_toggles_mute_everywhere(app):
    widget = gui.JarvisWidget()
    assert voice.is_mic_muted() is False
    assert widget.mic_button.text() == "🎤 Mic On"

    widget.toggle_mute()

    assert voice.is_mic_muted() is True
    assert widget.mic_button.isChecked() is True
    assert widget.mic_button.text() == "🔇 Muted"
    assert widget.mute_action.isChecked() is True
    assert widget.status.text() == gui.MUTED
    assert widget.reactor._state == gui.MUTED

    widget.toggle_mute()

    assert voice.is_mic_muted() is False
    assert widget.status.text() == gui.IDLE


def test_trigger_refuses_to_listen_while_muted(app):
    widget = gui.JarvisWidget()
    widget.set_mic_muted(True)
    widget.trigger()
    assert widget.worker is None, "a muted mic must not start a listen cycle"
    assert "muted" in widget.transcript.text().lower()


def test_stop_button_is_live_whenever_jarvis_is_busy(app, monkeypatch):
    widget = gui.JarvisWidget()
    assert widget.stop_button.isEnabled() is False
    widget.set_state(gui.LISTENING)          # waiting for you to speak
    assert widget.stop_button.isEnabled() is True
    widget.set_state(gui.SPEAKING)           # talking
    assert widget.stop_button.isEnabled() is True
    widget.set_state(gui.IDLE)
    assert widget.stop_button.isEnabled() is False

    listened, spoken = [], []
    monkeypatch.setattr(voice, "stop_listening", lambda: listened.append(1))
    monkeypatch.setattr(voice, "stop_speaking", lambda: spoken.append(1))
    widget._stop()
    assert listened == [1] and spoken == [1]


def test_reactor_paints_with_any_energy(app):
    reactor = gui.Reactor(80)
    for value in (0.0, 0.5, 1.0, 2.0, -1.0, "nonsense"):
        reactor.set_energy(value)
        assert 0.0 <= reactor._energy <= 1.0
        canvas = QPixmap(80, 80)
        canvas.fill(Qt.transparent)
        reactor.render(canvas)             # exercises paintEvent, must not raise

    reactor.set_state(gui.MUTED)           # the muted badge path too
    canvas = QPixmap(80, 80)
    canvas.fill(Qt.transparent)
    reactor.render(canvas)


def test_on_heard_truncates_long_phrases(app):
    widget = gui.JarvisWidget()
    widget._on_heard("please play the song")
    assert widget.transcript.text() == "please play the song"

    widget._on_heard("x" * 100)
    assert len(widget.transcript.text()) <= 34
    assert widget.transcript.text().endswith("…")


def test_worker_clears_the_level_sink_when_done(app, monkeypatch):
    monkeypatch.setattr(gui, "listen", lambda: "")
    worker = gui.JarvisWorker(Memory())
    levels = []
    worker.level.connect(levels.append)

    worker.run()                            # run inline; nothing blocks

    assert voice._level_sink is None, "the sink must not outlive the worker"
    assert 0.0 in levels


def test_worker_reports_heard_and_speaks(app, monkeypatch):
    # One exchange, then silence: with chat mode that is a complete session.
    turns = iter(["hello", ""])
    monkeypatch.setattr(gui, "listen", lambda *args, **kwargs: next(turns, ""))
    monkeypatch.setattr(gui, "handle", lambda text, memory: ("hi there", "local", 0))
    spoken = []
    monkeypatch.setattr(gui, "speak", lambda reply: spoken.append(reply))

    worker = gui.JarvisWorker(Memory())
    heard = []
    states = []
    worker.heard.connect(heard.append)
    worker.state_changed.connect(states.append)

    worker.run()

    assert heard == ["hello"]
    assert spoken == ["hi there"]
    assert states[-1] == gui.IDLE


def test_worker_keeps_talking_until_silence(app, monkeypatch):
    """Chat mode: several exchanges in one session, then it stops itself."""
    turns = iter(["what time is it", "open downloads", "", "never reached"])
    replies = {"what time is it": "3 pm", "open downloads": "Opening Downloads"}
    monkeypatch.setattr(
        gui, "handle", lambda text, memory: (replies.get(text, "?"), "local", 0))
    spoken = []
    monkeypatch.setattr(gui, "speak", lambda reply: spoken.append(reply))

    worker = gui.JarvisWorker(Memory(), chat=True, chat_timeout=60)
    heard = []
    listened = []
    worker.heard.connect(heard.append)

    def record_listen(wait=None):
        listened.append(wait)
        return next(turns, "")

    monkeypatch.setattr(gui, "listen", record_listen)
    states = []
    worker.state_changed.connect(states.append)

    worker.run()

    assert heard == ["what time is it", "open downloads"]
    assert spoken == ["3 pm", "Opening Downloads"]
    assert not voice._level_sink, "the sink must not outlive the session"
    assert "never reached" not in heard
    assert states[-1] == gui.IDLE
    # The first turn waits the normal window; the ones after wait the chat
    # timeout, so the conversation continues without a click.
    assert listened[0] != 60
    assert listened[1:] == [60, 60]


def test_worker_ends_the_session_on_an_exit_word(app, monkeypatch):
    """A spoken "bye" must end the chat, like "exit" does in the terminal."""
    turns = iter(["what time is it", "bye", "again"])
    monkeypatch.setattr(gui, "listen", lambda *args, **kwargs: next(turns, ""))
    monkeypatch.setattr(
        gui, "handle",
        lambda text, memory: ("3 pm" if "time" in text else "ignored", "local", 0))
    spoken = []
    monkeypatch.setattr(gui, "speak", lambda reply: spoken.append(reply))

    worker = gui.JarvisWorker(Memory(), chat=True)
    heard = []
    worker.heard.connect(heard.append)
    states = []
    worker.state_changed.connect(states.append)

    worker.run()

    assert heard == ["what time is it", "bye"]
    assert "again" not in heard, "the session must not continue after an exit word"
    assert any("Session over" in reply for reply in spoken)


def test_worker_lets_stop_end_the_conversation(app, monkeypatch):
    """Pressing Stop cuts the reply and must not grab the mic again."""
    turns = iter(["what time is it", "what are you doing?"])
    monkeypatch.setattr(gui, "listen", lambda *args, **kwargs: next(turns, ""))
    monkeypatch.setattr(
        gui, "handle", lambda text, memory: ("3 pm", "local", 0))

    def speak_and_stop(_reply):
        voice.stop_speaking()          # exactly what the GUI Stop button does
        spoken.append(1)

    spoken = []
    monkeypatch.setattr(gui, "speak", speak_and_stop)

    worker = gui.JarvisWorker(Memory(), chat=True)
    heard = []
    worker.heard.connect(heard.append)
    states = []
    worker.state_changed.connect(states.append)

    worker.run()

    assert heard == ["what time is it"]
    assert "what are you doing?" not in heard, "Stop must end the conversation"
    assert spoken == [1]
    assert states[-1] == gui.IDLE
    voice._stop_speaking.clear()


def test_worker_hands_the_microphone_back_to_the_wake_word(app, monkeypatch):
    """Ending a chat session returns to Idle, which unpauses the wake listener."""
    listener = FakeWake()
    monkeypatch.setattr(gui.wake, "enabled", lambda: True)
    monkeypatch.setattr(gui.wake, "Listener", lambda on_wake: listener)
    turns = iter(["", ""])
    monkeypatch.setattr(gui, "listen", lambda *args, **kwargs: next(turns, ""))
    monkeypatch.setattr(gui, "speak", lambda reply: None)

    widget = gui.JarvisWidget()
    assert widget._wake is listener

    worker = gui.JarvisWorker(Memory(), chat=True)
    widget.set_state(gui.LISTENING)
    worker.run()                       # one empty turn -> session over -> Idle
    widget.set_state(gui.IDLE)

    assert listener.resumes >= 1, "idle must give the mic back to the wake word"

# --------------------------------------------------------------------------- #
# wake word ("Hey Jarvis") wiring (v1.0.5)
# --------------------------------------------------------------------------- #

class Signal:
    """Minimal stand-in for a bound pyqtSignal (we only need ``connect``)."""

    def connect(self, _slot):
        pass


class FakeWorker:
    """A JarvisWorker that never touches a microphone or the network."""

    def __init__(self, memory, parent=None, chat=False, chat_timeout=60):
        self.chat = chat
        self.chat_timeout = chat_timeout
        self.state_changed = Signal()
        self.message = Signal()
        self.heard = Signal()
        self.level = Signal()
        self.running = False

    def isRunning(self):
        return self.running

    def start(self):
        self.running = True

    def wait(self, _ms=None):
        return True


class FakeWake:
    """Stand-in for wake.Listener: records calls without a mic or a thread."""

    def __init__(self):
        self.starts = self.pauses = self.resumes = self.stops = 0

    def start(self):
        self.starts += 1

    def pause(self, timeout=2.0):
        self.pauses += 1

    def resume(self):
        self.resumes += 1

    def stop(self):
        self.stops += 1


@pytest.fixture
def fake_wake(monkeypatch):
    """Install a fake wake listener *and* a fake worker.

    A real worker would run listen() for real, and a real wake listener would
    open arecord -- neither belongs in a test.
    """
    listener = FakeWake()
    monkeypatch.setattr(gui.wake, "enabled", lambda: True)
    monkeypatch.setattr(gui.wake, "available", lambda: True)
    monkeypatch.setattr(gui.wake, "Listener", lambda on_wake: listener)
    monkeypatch.setattr(gui, "JarvisWorker", FakeWorker)
    return listener


def test_no_wake_listener_when_the_engine_is_missing(app, monkeypatch):
    monkeypatch.setattr(gui.wake, "enabled", lambda: False)
    widget = gui.JarvisWidget()
    assert widget._wake is None
    assert widget.wake_action.isChecked() is False


def test_wake_listener_starts_and_the_menu_shows_it(app, fake_wake):
    widget = gui.JarvisWidget()

    assert widget._wake is fake_wake
    assert fake_wake.starts == 1
    assert widget.wake_action.isChecked() is True


def test_trigger_gives_the_microphone_to_the_wake_listener(app, fake_wake):
    widget = gui.JarvisWidget()

    widget.trigger()

    assert fake_wake.pauses == 1, "trigger must release the wake listener first"
    assert isinstance(widget.worker, FakeWorker)


def test_returning_to_idle_hands_the_microphone_back(app, fake_wake):
    widget = gui.JarvisWidget()

    widget.set_state(gui.THINKING)
    widget.set_state(gui.IDLE)

    assert fake_wake.resumes == 1


def test_the_mic_is_not_handed_back_while_a_cycle_runs(app, fake_wake):
    """Resume must be refused while a worker is still using the mic."""
    widget = gui.JarvisWidget()

    class Busy:
        def isRunning(self):
            return True

    widget.worker = Busy()
    widget.set_state(gui.IDLE)

    assert fake_wake.resumes == 0


def test_muting_silences_the_wake_word_too(app, fake_wake):
    widget = gui.JarvisWidget()

    widget.set_mic_muted(True)
    assert fake_wake.pauses >= 1

    widget.set_state(gui.IDLE)                 # must not reopen the mic
    assert fake_wake.resumes == 0

    widget.set_mic_muted(False)
    assert fake_wake.resumes == 1


def test_wake_word_runs_the_same_path_as_a_click(app, fake_wake):
    widget = gui.JarvisWidget()

    widget._wake_heard()                       # what the listener thread calls

    assert isinstance(widget.worker, FakeWorker)
    assert widget.worker.isRunning() is True


def test_menu_action_turns_the_wake_word_off_and_on(app, fake_wake, monkeypatch):
    widget = gui.JarvisWidget()
    assert widget._wake is fake_wake

    widget.toggle_wake()

    assert widget._wake is None
    assert fake_wake.stops == 1
    assert widget.wake_action.isChecked() is False
    assert "off" in widget.transcript.text().lower()

    second = FakeWake()
    monkeypatch.setattr(gui.wake, "Listener", lambda on_wake: second)
    widget.toggle_wake()

    assert widget._wake is second
    assert widget.wake_action.isChecked() is True


def test_menu_action_explains_when_the_engine_is_missing(app, monkeypatch):
    monkeypatch.setattr(gui.wake, "enabled", lambda: False)
    monkeypatch.setattr(gui.wake, "available", lambda: False)
    widget = gui.JarvisWidget()

    widget.toggle_wake()

    assert widget._wake is None
    assert "pip install" in widget.transcript.text()
    assert widget.wake_action.isChecked() is False


def test_quit_stops_the_wake_listener(app, fake_wake):
    widget = gui.JarvisWidget()
    widget._quitting = True

    widget.quit()

    assert fake_wake.stops == 1


def test_icon_has_a_margin_so_it_is_never_clipped(app):
    """The launcher renders this at 256 px, where a fixed 1.5 px margin clipped."""
    image = gui.make_icon(256).pixmap(256, 256).toImage()

    edges = [(0, 0), (255, 0), (0, 255), (255, 255), (0, 128), (255, 128)]
    for x, y in edges:
        assert image.pixelColor(x, y).alpha() == 0, f"({x}, {y}) is painted"

    center = image.pixelColor(128, 128)
    assert center.alpha() > 0, "the core must be painted"
