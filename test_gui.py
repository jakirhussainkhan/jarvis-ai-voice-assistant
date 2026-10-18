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
    monkeypatch.setattr(gui, "listen", lambda: "hello")
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
