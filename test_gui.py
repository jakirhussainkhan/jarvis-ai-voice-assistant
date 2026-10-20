"""Smoke tests for the PyQt5 floating widget + tray (gui.py).

These run on the *offscreen* Qt platform so they never open a real window, and
are skipped entirely when PyQt5 is not installed. They verify the parts that are
easy to get wrong without a display: icon painting, widget construction and the
state label.
"""

import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtCore import QPoint, Qt        # noqa: E402
from PyQt5.QtGui import QCloseEvent, QPixmap        # noqa: E402
from PyQt5.QtWidgets import QApplication, QSizeGrip   # noqa: E402

import gui                                 # noqa: E402
import voice                               # noqa: E402
from memory import Memory                  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture(autouse=True)
def _reset_voice_state(monkeypatch):
    # Never let a worker test open a real microphone through barge-in.
    monkeypatch.setattr(voice, "_BARGE_ENABLED", False)
    voice.set_level_sink(None)
    voice.set_mic_muted(False)
    voice._stop_listening.clear()
    voice._stop_speaking.clear()
    yield
    voice.set_level_sink(None)
    voice.set_mic_muted(False)
    voice._stop_listening.clear()
    voice._stop_speaking.clear()


class _FakeHotkeyListener:
    """Stand-in for ``pynput.keyboard.GlobalHotKeys``.

    The real listener grabs the keyboard system-wide, which must never happen
    while the test suite runs, so every widget build gets this fake instead.
    """

    def __init__(self, mapping):
        self.mapping = mapping
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _fake_hotkey(monkeypatch):
    """Replace the pynput listener factory for every GUI test."""
    def fake_make(callback):
        return _FakeHotkeyListener({key: callback for key in gui.HOTKEYS})

    monkeypatch.setattr(gui, "_make_hotkey_listener", fake_make)


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
    widget.show()
    widget.close()                 # as the ✕ button / window manager would
    assert widget._quitting is False
    assert widget.isVisible() is False


def test_close_to_tray_keeps_the_app_alive(app):
    """The ✕ button hides the card; it never quits Jarvis."""
    widget = gui.JarvisWidget()
    widget.show()

    widget._close_to_tray()

    assert widget.isVisible() is False
    assert widget._quitting is False


def test_halt_for_hide_stops_the_worker_and_forces_idle(monkeypatch):
    """The ✕ path stops the worker, clears the voice events, and goes Idle."""
    calls = []

    class FakeSignal:
        def disconnect(self, slot):
            calls.append(("disconnect", slot))

    class FakeWorker:
        def __init__(self):
            self.state_changed = FakeSignal()
            self.stops = 0

        def stop(self):
            self.stops += 1
            calls.append("worker.stop")

    class Stub:
        def set_state(self, state):
            calls.append(("state", state))

    stub = Stub()
    worker = FakeWorker()
    stub.worker = worker
    monkeypatch.setattr(gui.voice, "stop_listening",
                        lambda: calls.append("listen"))
    monkeypatch.setattr(gui.voice, "stop_speaking",
                        lambda: calls.append("speak"))

    gui.JarvisWidget._halt_for_hide(stub)

    assert worker.stops == 1
    assert ("disconnect", stub.set_state) in calls
    assert calls[-1] == ("state", gui.IDLE)
    assert "listen" in calls
    assert "speak" in calls


def test_close_event_halts_the_session_before_hiding():
    """The window close path ends the turn first, then hides to the tray."""
    calls = []

    class Stub:
        _quitting = False
        tray = None

        def _halt_for_hide(self):
            calls.append("halt")

        def hide(self):
            calls.append("hide")

    event = QCloseEvent()
    gui.JarvisWidget.closeEvent(Stub(), event)

    assert calls == ["halt", "hide"]
    assert event.isAccepted() is False, "close must be ignored (hide to tray)"


def test_real_quit_is_allowed_to_close(app):
    """Only a real quit (tray menu) lets the window actually close."""
    widget = gui.JarvisWidget()
    widget._quitting = True
    widget.close()
    assert widget._quitting is True


# --------------------------------------------------------------------------- #
# frameless resize (QSizeGrip + edge dragging)
# --------------------------------------------------------------------------- #

def test_window_has_a_resize_grip(app):
    widget = gui.JarvisWidget()
    assert isinstance(widget.size_grip, QSizeGrip)
    # The frameless card must no longer be locked to a single size.
    assert widget.minimumWidth() < widget.maximumWidth()
    assert widget.minimumHeight() < widget.maximumHeight()


def test_resize_edge_detection(app):
    widget = gui.JarvisWidget()
    widget.resize(380, 560)

    assert widget._edge_at(QPoint(2, 2)) == {"left", "top"}
    assert widget._edge_at(QPoint(378, 558)) == {"right", "bottom"}
    assert widget._edge_at(QPoint(2, 280)) == {"left"}
    assert widget._edge_at(QPoint(190, 280)) == set()
    assert widget._cursor_for_edges({"left", "top"}) == Qt.SizeFDiagCursor
    assert widget._cursor_for_edges({"right", "top"}) == Qt.SizeBDiagCursor
    assert widget._cursor_for_edges({"left"}) == Qt.SizeHorCursor
    assert widget._cursor_for_edges({"bottom"}) == Qt.SizeVerCursor
    assert widget._cursor_for_edges(set()) == Qt.ArrowCursor


def test_edge_drag_resizes_the_window(app):
    widget = gui.JarvisWidget()
    widget.resize(380, 560)
    widget._resize_edges = {"right", "bottom"}
    widget._resize_origin = QPoint(1000, 1000)
    widget._resize_start = widget.geometry()

    widget._apply_resize(QPoint(1060, 1040))

    assert widget.width() == 440
    assert widget.height() == 600


def test_resize_never_shrinks_below_minimum(app):
    widget = gui.JarvisWidget()
    widget.resize(380, 560)
    widget._resize_edges = {"right", "bottom"}
    widget._resize_origin = QPoint(1000, 1000)
    widget._resize_start = widget.geometry()

    widget._apply_resize(QPoint(0, 0))

    assert widget.width() == widget.minimumWidth()
    assert widget.height() == widget.minimumHeight()


# --------------------------------------------------------------------------- #
# global wake hotkey (Ctrl+Shift+J / Ctrl+Alt+J)
# --------------------------------------------------------------------------- #

def test_global_hotkey_is_registered_on_start(app):
    widget = gui.JarvisWidget()
    listener = widget._hotkey
    assert listener is not None
    assert listener.started is True
    assert gui.HOTKEY == "<ctrl>+<shift>+j"
    assert gui.ALT_HOTKEY == "<ctrl>+<alt>+j"
    assert gui.HOTKEY in listener.mapping
    assert gui.ALT_HOTKEY in listener.mapping


def test_global_hotkey_triggers_a_cycle_from_idle(app):
    widget = gui.JarvisWidget()
    triggered = []
    widget.trigger = lambda: triggered.append(True)

    widget.hotkey_pressed.emit()          # what the pynput thread emits

    assert triggered == [True]


def test_global_hotkey_restores_the_existing_window(app):
    """The hotkey shows the existing card -- it never builds a second one."""
    widget = gui.JarvisWidget()
    tray = widget.tray
    triggered = []
    widget.trigger = lambda: triggered.append(True)

    widget.hide()
    assert widget.isVisible() is False

    widget.hotkey_pressed.emit()

    assert widget.isVisible() is True        # same widget, brought forward
    assert triggered == [True]
    assert widget.tray is tray               # no duplicate tray/instance


def test_global_hotkey_unmutes_before_triggering(app):
    widget = gui.JarvisWidget()
    widget.set_mic_muted(True)
    assert voice.is_mic_muted() is True
    triggered = []
    widget.trigger = lambda: triggered.append(True)

    widget.hotkey_pressed.emit()

    assert voice.is_mic_muted() is False
    assert triggered == [True]


def test_global_hotkey_stops_cleanly_on_quit(app):
    widget = gui.JarvisWidget()
    listener = widget._hotkey

    widget.quit()

    assert listener.stopped is True
    assert widget._hotkey is None


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
    assert widget.orb._state == gui.MUTED

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


def test_orb_paints_with_any_energy(app):
    orb = gui.Orb(80)
    for value in (0.0, 0.5, 1.0, 2.0, -1.0, "nonsense"):
        orb.set_energy(value)
        assert 0.0 <= orb._energy <= 1.0
        canvas = QPixmap(80, 80)
        canvas.fill(Qt.transparent)
        orb.render(canvas)                 # exercises paintEvent, must not raise

    orb.set_state(gui.MUTED)               # the muted badge path too
    canvas = QPixmap(80, 80)
    canvas.fill(Qt.transparent)
    orb.render(canvas)


def test_on_heard_truncates_long_phrases(app):
    widget = gui.JarvisWidget()
    widget._on_heard("please play the song")
    assert widget.transcript.text() == "please play the song"

    widget._on_heard("x" * 100)
    assert len(widget.transcript.text()) <= 34
    assert widget.transcript.text().endswith("…")


def test_worker_clears_the_level_sink_when_done(app, monkeypatch):
    monkeypatch.setattr(gui, "listen", lambda *args, **kwargs: "")
    worker = gui.JarvisWorker(Memory())
    levels = []
    worker.level.connect(levels.append)

    worker.run()                            # run inline; nothing blocks

    assert voice._level_sink is None, "the sink must not outlive the worker"
    assert 0.0 in levels


def test_worker_returns_to_idle_when_capture_crashes(app, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("microphone unavailable")

    monkeypatch.setattr(gui, "listen", boom)
    worker = gui.JarvisWorker(Memory())
    states, messages = [], []
    worker.state_changed.connect(states.append)
    worker.message.connect(messages.append)

    worker.run()                            # run inline; nothing blocks

    assert states[-1] == gui.IDLE, "a capture crash must release the orb"
    assert any("microphone unavailable" in message for message in messages)


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

    def record_listen(wait=None, **kwargs):
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
        self.mic_changed = Signal()
        self.exit_requested = Signal()
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
    """The launcher renders this at 256 px, so the tile must not touch the edge."""
    image = gui.make_icon(256).pixmap(256, 256).toImage()

    edges = [(0, 0), (255, 0), (0, 255), (255, 255), (0, 128), (255, 128)]
    for x, y in edges:
        assert image.pixelColor(x, y).alpha() == 0, f"({x}, {y}) is painted"

    center = image.pixelColor(128, 128)
    assert center.alpha() > 0, "the orb must be painted"


def test_icon_is_a_dark_tile_with_a_glowing_orb_and_j(app):
    """The new mark: charcoal rounded tile, azure orb, bright stylised J."""
    image = gui.make_icon(256).pixmap(256, 256).toImage()

    # The tile edge is dark; the orb core is bright and blue-dominant.
    tile = image.pixelColor(128, 12)
    assert tile.alpha() == 255 and tile.value() < 90, \
        f"the tile must stay dark, got {tile.name()}"
    core = image.pixelColor(128, 128)
    assert core.blue() > 180 and core.blue() > core.red() + 40, \
        f"the orb must glow azure, got {core.name()}"

    # The J stem is a bright stroke over the orb, so its darkest channel is much
    # higher than the orb immediately beside it.
    stem = image.pixelColor(151, 120)
    beside = image.pixelColor(128, 120)
    assert min(stem.red(), stem.green(), stem.blue()) > \
        min(beside.red(), beside.green(), beside.blue()) + 40, \
        f"the J must read against the orb, stem {stem.name()} vs {beside.name()}"


def test_orb_listening_glows_green(app):
    """The live orb takes its colour from the state, not the icon's azure."""
    orb = gui.Orb(150)
    orb.set_state(gui.LISTENING)
    orb.set_energy(0.6)
    orb._phase = 0.0
    image = orb.grab().toImage()

    sample = image.pixelColor(75, 25)
    assert sample.green() > sample.blue() + 20 and sample.green() > sample.red(), \
        f"the listening orb must glow green, got {sample.name()}"


def test_orb_muted_greys_out(app):
    """Muted must stop reading as any active colour."""
    orb = gui.Orb(150)
    orb.set_state(gui.MUTED)
    image = orb.grab().toImage()

    sample = image.pixelColor(75, 25)
    saturation = (max(sample.red(), sample.green(), sample.blue())
                  - min(sample.red(), sample.green(), sample.blue()))
    assert saturation < 60, f"muted orb must be desaturated, got {sample.name()}"


# --------------------------------------------------------------------------- #
# modern UI: rounded card, window controls, breathing, waveform
# --------------------------------------------------------------------------- #

def test_card_is_frameless_rounded_and_always_on_top(app):
    widget = gui.JarvisWidget()
    assert widget.windowFlags() & Qt.FramelessWindowHint
    assert widget.windowFlags() & Qt.WindowStaysOnTopHint
    assert widget.testAttribute(Qt.WA_TranslucentBackground)

    canvas = QPixmap(widget.size())
    canvas.fill(Qt.transparent)
    widget.render(canvas)
    image = canvas.toImage()

    assert image.pixelColor(0, 0).alpha() == 0, "the corner must be rounded away"
    assert image.pixelColor(widget.width() - 1,
                            widget.height() - 1).alpha() == 0
    assert image.pixelColor(20, widget.height() // 2).alpha() > 0, \
        "the dark card must be painted inside the rounded corners"


def test_window_has_minimise_and_close_controls(app):
    widget = gui.JarvisWidget()

    assert widget.minimize_button.objectName() == "minimizeButton"
    assert widget.close_button.objectName() == "closeButton"
    assert widget.minimize_button.isEnabled()
    assert widget.close_button.isEnabled()
    assert widget.autostart_action.text() == "Start on Boot"
    assert widget.autostart_action.isCheckable()
    if widget.tray is not None:
        assert widget.tray.autostart_action.text() == "Start on Boot"
        assert widget.tray.autostart_action.isCheckable()

    widget._minimize()      # must be safe on the offscreen platform


def test_toggle_autostart_updates_both_menus_and_notifies(monkeypatch):
    """The menu action installs the login entry and confirms via the tray."""
    class FakeAction:
        def __init__(self):
            self.checked = None

        def setChecked(self, value):
            self.checked = value

    class FakeTray:
        def __init__(self):
            self.synced = []
            self.messages = []

        def sync_autostart(self, value):
            self.synced.append(value)

        def showMessage(self, *args):
            self.messages.append(args)

    class Stub:
        def __init__(self):
            self.tray = FakeTray()
            self.autostart_action = FakeAction()
            self.flashes = []

        _sync_autostart = gui.JarvisWidget._sync_autostart

        def _flash(self, text):
            self.flashes.append(text)

    state = {"enabled": False}
    monkeypatch.setattr(gui.startup, "is_autostart_enabled",
                        lambda: state["enabled"])

    def fake_set(enable):
        state["enabled"] = enable

    monkeypatch.setattr(gui.startup, "set_autostart", fake_set)

    stub = Stub()
    gui.JarvisWidget.toggle_autostart(stub)

    assert state["enabled"] is True
    assert stub.autostart_action.checked is True
    assert stub.tray.synced == [True]
    assert stub.flashes[-1] == "Auto-start enabled"
    assert any(message[1] == "Auto-start enabled"
               for message in stub.tray.messages)

    gui.JarvisWidget.toggle_autostart(stub)

    assert state["enabled"] is False
    assert stub.autostart_action.checked is False
    assert stub.flashes[-1] == "Auto-start disabled"


def test_toggle_autostart_reports_failures_and_restores_the_check(app,
                                                                 monkeypatch):
    class FakeAction:
        def __init__(self):
            self.checked = None

        def setChecked(self, value):
            self.checked = value

    class Stub:
        def __init__(self):
            self.tray = None
            self.autostart_action = FakeAction()
            self.flashes = []

        _sync_autostart = gui.JarvisWidget._sync_autostart

        def _flash(self, text):
            self.flashes.append(text)

    monkeypatch.setattr(gui.startup, "is_autostart_enabled", lambda: False)

    def fail(_enable):
        raise PermissionError("read-only home")

    monkeypatch.setattr(gui.startup, "set_autostart", fail)

    stub = Stub()
    gui.JarvisWidget.toggle_autostart(stub)

    assert stub.autostart_action.checked is False
    assert "read-only home" in stub.flashes[-1]


def test_orb_breathes_when_idle_and_stops_when_active(app):
    orb = gui.Orb(120)
    assert orb._breathe.state() == gui.QPropertyAnimation.Running

    orb.set_state(gui.LISTENING)
    assert orb._breathe.state() == gui.QPropertyAnimation.Stopped

    orb.set_state(gui.IDLE)
    assert orb._breathe.state() == gui.QPropertyAnimation.Running


def test_waveform_reacts_to_level_and_paints(app):
    waveform = gui.Waveform()
    waveform.resize(286, 58)
    waveform.set_level(1.0)
    for _ in range(8):
        waveform._tick()
    assert max(waveform._bars) > 0.2, "loud audio must raise the bars"

    canvas = QPixmap(286, 58)
    canvas.fill(Qt.transparent)
    waveform.render(canvas)             # exercises paintEvent, must not raise
    image = canvas.toImage()
    assert any(image.pixelColor(x, 29).alpha() > 0
               for x in range(0, 286, 8)), "the bars must be visible"

    waveform.set_state(gui.SPEAKING)
    waveform.set_level(0.0)
    for _ in range(4):
        waveform._tick()
    assert waveform._bars[-1] < 0.5, "the bars must fall back towards rest"


def test_live_level_feeds_both_the_orb_and_the_waveform(app):
    widget = gui.JarvisWidget()
    widget._on_level(0.8)
    assert widget.orb._energy == 0.8
    assert widget.waveform._level == 0.8


# --------------------------------------------------------------------------- #
# barge-in: talking over a reply
# --------------------------------------------------------------------------- #

def _pcm(amplitude, frames=None):
    """One capture chunk of a constant S16_LE amplitude."""
    frames = voice._CHUNK_FRAMES if frames is None else frames
    return struct.pack("<h", int(amplitude)) * frames


def test_barge_in_needs_sustained_speech(app):
    monitor = voice.BargeInMonitor(threshold=1000, hold=0.3)
    loud, quiet = _pcm(3000), _pcm(20)

    assert monitor.required == 3, "0.3 s at 0.1 s chunks is three chunks"
    assert monitor.feed(quiet) is False
    assert monitor.feed(loud) is False
    assert monitor.feed(quiet) is False       # a pause resets the run
    assert monitor.feed(loud) is False
    assert monitor.feed(loud) is False
    assert monitor.feed(loud) is True
    assert monitor.detected is True


def test_barge_in_ignores_echo_and_short_noise(app):
    monitor = voice.BargeInMonitor(threshold=2600, hold=0.5)

    assert monitor.feed(_pcm(1800)) is False   # speaker echo stays below it
    assert monitor.feed(_pcm(3200)) is False   # one loud chunk is not enough
    assert monitor.feed(_pcm(200)) is False    # silence resets the count
    assert monitor.detected is False


def test_barge_in_defaults_are_sensitive_enough_for_normal_speech(app):
    import config

    assert voice._BARGE_THRESHOLD == config.BARGE_THRESHOLD == 1200
    assert voice._BARGE_HOLD == config.BARGE_HOLD == 0.3

    monitor = voice.BargeInMonitor()
    assert monitor.threshold == 1200
    assert monitor.required == 3
    assert monitor.feed(_pcm(1300)) is False
    assert monitor.feed(_pcm(1300)) is False
    assert monitor.feed(_pcm(1300)) is True


def test_barge_in_can_be_explicitly_paused_for_media(app, monkeypatch):
    monkeypatch.setattr(voice, "_BARGE_ENABLED", True)
    monkeypatch.setattr(voice, "_ptt_active", lambda: False)
    voice.set_mic_muted(False)
    voice.pause_barge_in()

    monitor = voice.BargeInMonitor()
    assert monitor.start() is False
    assert monitor._thread is None
    assert voice.is_barge_in_paused() is True

    voice.resume_barge_in()
    assert voice.is_barge_in_paused() is False


def test_barge_in_is_disabled_in_push_to_talk(app, monkeypatch):
    monkeypatch.setattr(voice, "_BARGE_ENABLED", True)
    monkeypatch.setattr(voice, "_ptt_active", lambda: True)

    monitor = voice.BargeInMonitor()
    assert monitor.start() is False
    assert monitor._thread is None


def test_barge_in_is_disabled_while_the_mic_is_muted(app, monkeypatch):
    monkeypatch.setattr(voice, "_BARGE_ENABLED", True)
    monkeypatch.setattr(voice, "_ptt_active", lambda: False)
    voice.set_mic_muted(True)

    monitor = voice.BargeInMonitor()
    assert monitor.start() is False
    assert monitor._thread is None


def test_worker_returns_to_listening_after_barge_in(app, monkeypatch):
    """Talking over a reply continues the conversation instead of ending it."""
    turns = iter(["first", "second", ""])
    monkeypatch.setattr(gui, "listen", lambda *args, **kwargs: next(turns, ""))
    monkeypatch.setattr(
        gui, "handle",
        lambda text, memory: (f"reply to {text}", "local", 0))

    instances = []

    class FakeMonitor:
        def __init__(self, on_barge=None):
            self.on_barge = on_barge
            instances.append(self)

        def start(self):
            return True

        def stop(self):
            pass

    monkeypatch.setattr(gui, "BargeInMonitor", FakeMonitor)

    spoken = []

    def fake_speak(reply):
        voice._stop_speaking.clear()       # like the real _play_tts
        spoken.append(reply)
        if len(instances) == 1:
            instances[-1].on_barge()       # the user talks over the first reply

    monkeypatch.setattr(gui, "speak", fake_speak)

    worker = gui.JarvisWorker(Memory(), chat=True)
    heard, states = [], []
    worker.heard.connect(heard.append)
    worker.state_changed.connect(states.append)

    worker.run()

    assert heard == ["first", "second"]
    assert spoken == ["reply to first", "reply to second"]
    assert states.count(gui.LISTENING) >= 2
    assert states[-1] == gui.IDLE


def test_worker_syncs_mic_state_after_a_media_command(app, monkeypatch):
    """play_youtube mutes voice directly; the worker tells the GUI to update."""
    turns = iter(["play despacito", ""])
    monkeypatch.setattr(gui, "listen", lambda *a, **k: next(turns, ""))

    def fake_handle(_text, _memory):
        voice.set_mic_muted(True)
        return "Playing.", "local", 0

    monkeypatch.setattr(gui, "handle", fake_handle)
    monkeypatch.setattr(gui, "speak", lambda reply: None)

    worker = gui.JarvisWorker(Memory(), chat=True)
    mute_states, ui_states = [], []
    worker.mic_changed.connect(mute_states.append)
    worker.state_changed.connect(ui_states.append)

    worker.run()

    assert mute_states == [True]
    assert ui_states[-1] == gui.MUTED


def test_worker_explicit_shutdown_requests_app_quit(app, monkeypatch):
    monkeypatch.setattr(gui, "listen", lambda *a, **k: "close yourself")
    spoken = []
    monkeypatch.setattr(gui, "speak", spoken.append)

    worker = gui.JarvisWorker(Memory(), chat=True)
    exits = []
    worker.exit_requested.connect(lambda: exits.append(1))

    worker.run()

    assert spoken == ["Goodbye, sir"]
    assert exits == [1]

