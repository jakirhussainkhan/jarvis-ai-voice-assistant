"""Floating 'Arc Reactor' desktop widget + system-tray for Jarvis (PyQt5).

Click the reactor (or the tray icon) to talk; the audio + agent work runs on a
QThread so the UI never freezes. The app keeps running in the background: closing
the floating widget hides it to the system tray instead of quitting.

The reactor is alive: it reacts to your voice while it listens (the mic RMS) and
moves in time with its own speech (the TTS loudness envelope). A mic button mutes
and unmutes the microphone at any time, and a Stop button cuts a reply short.

Controls:
  floating widget   left-click = talk   ·   drag = move
                    right-click = menu   ·   Esc = hide to tray
                    Mic button = mute/unmute   ·   Stop = cut the reply
  tray icon         left-click = talk   ·   right-click = menu
                    menu: Talk / Mute mic / Show-Hide / Quit
"""

import math
import sys

from PyQt5.QtCore import QPointF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QIcon, QPainter, QPen, QPixmap,
                         QRadialGradient)
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMenu,
                             QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget)

import voice
from config import CHAT_ON, CHAT_TIMEOUT
from main import handle, is_exit
from memory import Memory
from voice import listen, speak
import wake

# What Jarvis says when a continuous session ends (you said an exit word, went
# quiet, or pressed Stop).
_SESSION_END = "Session over. See you soon, sir."

IDLE = "Idle"
LISTENING = "Listening..."
THINKING = "Thinking..."
SPEAKING = "Speaking..."
MUTED = "Muted"

# glow colour per state: (outer glow, core)
_COLORS = {
    IDLE:      (QColor(0, 140, 255), QColor(120, 205, 255)),
    LISTENING: (QColor(0, 220, 140), QColor(140, 255, 210)),
    THINKING:  (QColor(255, 170, 0), QColor(255, 225, 150)),
    SPEAKING:  (QColor(170, 90, 255), QColor(220, 180, 255)),
    MUTED:     (QColor(80, 92, 112), QColor(140, 155, 180)),
}

_BUTTON_STYLE = """
QPushButton {
    color:#dff2ff; background:rgba(20,40,70,190);
    border:1px solid rgba(150,235,255,120);
    border-radius:14px; padding:5px 12px;
    font-size:12px; font-weight:600;
}
QPushButton:hover { background:rgba(30,70,120,230); }
QPushButton:disabled {
    color:rgba(220,240,255,80); background:rgba(20,40,70,80);
    border-color:rgba(150,235,255,40);
}
QPushButton:checked {
    background:rgba(200,70,70,220); border-color:rgba(255,160,160,190);
}
"""


def _draw_reactor_face(painter, center, radius, glow, core_light,
                       energy=1.0, breathe=1.0):
    """Paint the arc-reactor face: the one source of the Iron Man look.

    Shared by the static launcher icon (:func:`make_icon`) and the live Orb
    (``Reactor.paintEvent``), so they can never drift apart.

    ``glow`` tints the halo of light around the core; ``core_light`` is the
    colour the coil light cools towards (both are the widget's per-state
    colours, so the face still signals Listening/Thinking/Speaking/Muted).
    ``energy`` (0..1) brightens the light and breath, ``breathe`` scales the
    glow and core radius for the resting shimmer. Everything is drawn inside
    ``radius`` and is transparent outside, so corners stay clean at any size.
    """
    import math
    from PyQt5.QtGui import QPainterPath

    painter.setPen(Qt.NoPen)

    # --- backplate: the recessed carbon disc the whole reactor sits on ------
    plate = QRadialGradient(center, radius)
    plate.setColorAt(0.0, QColor(24, 34, 43))
    plate.setColorAt(0.75, QColor(13, 20, 27))
    plate.setColorAt(1.0, QColor(3, 6, 10))
    painter.setBrush(QBrush(plate))
    painter.drawEllipse(center, radius, radius)

    # a whisper of bloom just inside the rim so the metal reads as "powered"
    bloom = QRadialGradient(center, radius * 0.85)
    bloom.setColorAt(0.0, QColor(glow.red(), glow.green(), glow.blue(), 28))
    bloom.setColorAt(1.0, QColor(glow.red(), glow.green(), glow.blue(), 0))
    painter.setBrush(QBrush(bloom))
    painter.drawEllipse(center, radius * 0.85, radius * 0.85)

    # --- titanium housing: an annulus with a machined bevel ------------------
    # The gradient radius spans the whole disc, so each colour stop is a
    # fraction of `radius`; the ring runs from 0.60 to 0.98. Dark gunmetal:
    # the glowing slots must be the brightest thing in the icon.
    housing = QRadialGradient(center, radius)
    for stop, color in (
        (0.60, QColor(57, 66, 78)),     # inner bore: shadowed metal
        (0.645, QColor(107, 122, 140)), # bevel catches the light
        (0.70, QColor(43, 51, 61)),     # flat of the ring: dark
        (0.92, QColor(38, 45, 54)),     # flat of the ring
        (0.965, QColor(92, 103, 116)),  # outer bevel
        (0.99, QColor(20, 26, 34)),     # dark outer edge
    ):
        housing.setColorAt(stop, color)

    housing_path = QPainterPath()
    housing_path.addEllipse(center, radius * 0.98, radius * 0.98)
    housing_path.addEllipse(center, radius * 0.60, radius * 0.60)
    painter.setBrush(QBrush(housing))
    painter.drawPath(housing_path)

    # --- ten coil slots cut into the housing, glowing white-hot to tint ------
    slot_path = QPainterPath()
    half_angle = math.radians(10.5)           # wide slots, thin dark separators
    inner, outer = radius * 0.615, radius * 0.96
    # Walk the inner rail left-to-right, then the outer rail right-to-left,
    # so each polygon goes around its sector exactly once.
    for segment in range(10):
        base = math.radians(segment * 36.0) - math.pi / 2.0
        corners = []
        for level, sides in ((inner, (-1, 1)), (outer, (1, -1))):
            for side in sides:
                theta = base + side * half_angle
                corners.append(QPointF(
                    center.x() + level * math.cos(theta),
                    center.y() + level * math.sin(theta)))
        slot_path.moveTo(corners[0])
        for corner in corners[1:]:
            slot_path.lineTo(corner)
        slot_path.closeSubpath()

    slot_gradient = QRadialGradient(center, outer)
    slot_gradient.setColorAt(inner / outer - 0.001, QColor(238, 250, 255))
    slot_gradient.setColorAt(0.72, core_light)
    slot_gradient.setColorAt(1.0, glow)
    painter.fillPath(slot_path, QBrush(slot_gradient))
    # two slim translucent strokes just kiss the slot edges with light; wide
    # ones would flood the gaps and wash the ring into a uniform band
    painter.setBrush(Qt.NoBrush)      # drawPath strokes AND fills: no fill wanted
    for width, alpha in ((0.038, 66), (0.016, 120)):
        halo_pen = QPen(QColor(glow.red(), glow.green(), glow.blue(), alpha),
                        max(0.8, radius * 2 * width))
        painter.setPen(halo_pen)
        painter.drawPath(slot_path)
    painter.setPen(Qt.NoPen)

    # thin machining seams; dim, so they read as shadow lines not chrome
    rim_pen = QPen(QColor(158, 186, 208, 120))
    rim_pen.setWidthF(max(0.8, radius * 0.020))
    painter.setPen(rim_pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(center, radius * 0.618, radius * 0.618)
    painter.drawEllipse(center, radius * 0.975, radius * 0.975)
    painter.setPen(Qt.NoPen)

    # --- the palladium core: a solid light disc with a glow halo -------------
    glow_radius = radius * (0.58 + 0.10 * energy) * breathe
    halo = QRadialGradient(center, glow_radius)
    base = max(0.60, min(1.0, 0.65 + 0.35 * energy))
    halo.setColorAt(0.0, QColor(core_light.red(), core_light.green(),
                                core_light.blue(), int(235 * base)))
    halo.setColorAt(0.42, QColor(glow.red(), glow.green(), glow.blue(),
                                 int(130 * base)))
    halo.setColorAt(1.0, QColor(glow.red(), glow.green(), glow.blue(), 0))
    painter.setBrush(QBrush(halo))
    painter.drawEllipse(center, glow_radius, glow_radius)

    core_radius = radius * (0.37 + 0.05 * energy) * breathe
    core = QRadialGradient(center, core_radius)
    core.setColorAt(0.0, QColor(255, 255, 255))
    core.setColorAt(0.70, QColor(core_light.red(), core_light.green(),
                                 core_light.blue()).lighter(115))
    core.setColorAt(1.0, core_light)
    painter.setBrush(QBrush(core))
    painter.drawEllipse(center, core_radius, core_radius)

    # a single specular glint, top-left, like glass over the reactor
    glint = QPointF(center.x() - core_radius * 0.30,
                    center.y() - core_radius * 0.30)
    glint_gradient = QRadialGradient(glint, core_radius * 0.40)
    glint_gradient.setColorAt(0.0, QColor(255, 255, 255, 110))
    glint_gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(QBrush(glint_gradient))
    painter.drawEllipse(glint, core_radius * 0.40, core_radius * 0.40)


def make_icon(size=64):
    """Paint the Iron Man arc-reactor as a QIcon (window, tray and menu PNGs).

    The static face of :func:`_draw_reactor_face` at full energy: a machined
    titanium housing with ten glowing coil segments around a white-hot core,
    all over a dark recessed backplate. Everything is drawn inside the
    proportional ``radius`` margin so the housing is never clipped, and the
    corners stay transparent so it sits cleanly on any panel.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    center = QPointF(size / 2.0, size / 2.0)
    # Keep a proportional margin so the housing is not clipped at large sizes.
    radius = size / 2.0 - max(1.5, size * 0.04)
    _draw_reactor_face(painter, center, radius,
                       glow=QColor(60, 180, 255), core_light=QColor(160, 222, 255),
                       energy=1.0)
    painter.end()
    return QIcon(pixmap)


class Reactor(QWidget):
    """The circular arc-reactor button. Click to talk, drag to move.

    The glow breathes gently while idle and grows/quickens with ``set_energy``,
    which the worker feeds from the live mic level (listening) or the TTS
    loudness envelope (speaking).
    """

    clicked = pyqtSignal()
    menu_requested = pyqtSignal(object)

    def __init__(self, diameter=150, parent=None):
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self._state = IDLE
        self._phase = 0.0
        self._energy = 0.0
        self._press = None
        self._dragged = False
        self.setCursor(Qt.PointingHandCursor)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._pulse)
        self._timer.start(33)              # ~30 fps glow pulse

    def _pulse(self):
        # Faster shimmer while thinking; the resting pace otherwise.
        speed = 0.14 if self._state == THINKING else 0.06
        self._phase = (self._phase + speed) % (2 * math.pi)
        self.update()

    def set_state(self, state):
        self._state = state
        self.update()

    def set_energy(self, value):
        """Live loudness, 0..1, drives the size/brightness of the glow."""
        try:
            self._energy = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self._energy = 0.0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = min(center.x(), center.y()) - 2
        # Quiet: the gentle breathe. Loud: a bigger, springier pulse.
        breathe = 1.0 + (0.03 + 0.18 * self._energy) * math.sin(self._phase)
        glow, core_light = _COLORS.get(self._state, _COLORS[IDLE])

        # The Iron Man face: gunmetal housing, ten glowing coil slots and a
        # white-hot core -- tinted by the state colour, energy-driven.
        _draw_reactor_face(painter, center, radius,
                           glow=glow, core_light=core_light,
                           energy=self._energy, breathe=breathe)

        # muted badge: a small dark disc with a slash
        if self._state == MUTED:
            badge_r = radius * 0.28
            bx = center.x() + radius * 0.60
            by = center.y() + radius * 0.60
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(22, 26, 34, 235)))
            painter.drawEllipse(QPointF(bx, by), badge_r, badge_r)
            slash = QPen(QColor(255, 90, 90), max(2.0, radius * 0.09))
            slash.setCapStyle(Qt.RoundCap)
            painter.setPen(slash)
            off = badge_r * 0.5
            painter.drawLine(QPointF(bx - off, by - off), QPointF(bx + off, by + off))
        painter.end()

    # -- click vs drag ---------------------------------------------------- #
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.globalPos()
            self._dragged = False
        event.accept()

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPos() - self._press
            if not self._dragged and delta.manhattanLength() > 6:
                self._dragged = True
            if self._dragged:
                window = self.window()
                window.move(window.pos() + delta)
                self._press = event.globalPos()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self.menu_requested.emit(event.globalPos())
        elif event.button() == Qt.LeftButton and not self._dragged:
            self.clicked.emit()
        self._press = None
        self._dragged = False
        event.accept()


class JarvisWorker(QThread):
    """Runs listen -> try_local/run_agent -> speak off the GUI thread.

    In continuous-chat mode (``chat=True``) it does that *in a loop*: after each
    reply it opens the microphone again, so a normal conversation flows without
    clicking. It leaves on any of: an exit word, silence (you went quiet for the
    chat timeout), the mic being muted, or the Stop button.
    """

    state_changed = pyqtSignal(str)
    message = pyqtSignal(str)
    heard = pyqtSignal(str)
    level = pyqtSignal(float)

    def __init__(self, memory, parent=None, chat=False, chat_timeout=CHAT_TIMEOUT):
        super().__init__(parent)
        self.memory = memory
        self.chat = chat
        self.chat_timeout = chat_timeout

    def run(self):
        # The level sink is a module global in voice; only one worker runs at a
        # time, so emitting our signal from it is safe (Qt queues it to the GUI).
        voice.set_level_sink(self.level.emit)
        first_turn = True                     # the wake click listens normally
        voice._stop_speaking.clear()          # a stale Stop must not end this chat
        try:
            while True:
                self.state_changed.emit(LISTENING)
                # Talking back and forth should feel natural: the wait between
                # turns is the chat timeout, so Jarvis stays with you instead of
                # going to sleep after 30 s.
                user = listen(None if first_turn else self.chat_timeout)
                first_turn = False
                if not user:
                    # You went quiet (or pressed Stop / muted the mic): end the
                    # conversation instead of grabbing the microphone again.
                    return
                # listen() already prints the "👤 <user>" line to stdout and speak()
                # prints "🤖 <reply>" exactly once, so we only drive the state
                # colours here.
                self.heard.emit(user)

                if is_exit(user):
                    # A spoken exit word must end the session by voice, too --
                    # no need to reach for the terminal.
                    self.state_changed.emit(SPEAKING)
                    speak(_SESSION_END)
                    return

                self.state_changed.emit(THINKING)
                # main.handle() is the single dispatch path: it resolves any pending
                # confirmation, then tries the router and finally the agent.
                reply, _kind, _tokens = handle(user, self.memory)

                self.state_changed.emit(SPEAKING)
                speak(reply)          # prints "🤖 <reply>" exactly once
                if voice._stop_speaking.is_set():
                    # Stop cut the reply short: leave the conversation rather
                    # than immediately waiting for the next sentence.
                    return
        except Exception as exc:                      # never kill the UI
            self.message.emit(f"⚠️ {exc}")
        finally:
            voice.set_level_sink(None)
            self.level.emit(0.0)
            self.state_changed.emit(IDLE)


class JarvisTray(QSystemTrayIcon):
    """Tray icon so Jarvis can live in the background."""

    def __init__(self, widget):
        super().__init__(make_icon(64), widget)
        self.widget = widget
        self.setToolTip("Jarvis — click to talk")

        menu = QMenu()
        talk = menu.addAction("Talk")
        talk.triggered.connect(widget.trigger)
        self.mute_action = menu.addAction("Mute mic")
        self.mute_action.setCheckable(True)
        self.mute_action.triggered.connect(widget.toggle_mute)
        self.wake_action = menu.addAction("Hey Jarvis")
        self.wake_action.setCheckable(True)
        self.wake_action.setChecked(widget._wake is not None)
        self.wake_action.triggered.connect(widget.toggle_wake)
        self.chat_action = menu.addAction("Continuous chat")
        self.chat_action.setCheckable(True)
        self.chat_action.setChecked(widget._chat)
        self.chat_action.triggered.connect(widget.toggle_chat)
        self.toggle_action = menu.addAction("Hide window")
        self.toggle_action.triggered.connect(widget.toggle_visible)
        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(widget.quit)
        self.setContextMenu(menu)

        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.widget.trigger()

    def sync_toggle(self, visible):
        self.toggle_action.setText("Hide window" if visible else "Show window")

    def sync_mute(self, muted):
        self.mute_action.setChecked(muted)

    def sync_wake(self, on):
        self.wake_action.setChecked(on)

    def sync_chat(self, on):
        self.chat_action.setChecked(on)


class JarvisWidget(QWidget):
    """The floating window: reactor + status + mic/stop buttons."""

    # Emitted by the wake-word thread (never touch Qt from that thread).
    wake_heard = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        # Transparent top-level window. WA_TranslucentBackground asks the
        # compositor for an ARGB surface; WA_NoSystemBackground stops Qt from
        # erasing that surface with an opaque system colour (the usual cause of
        # the black box on X11/Cinnamon in Linux Mint). The stylesheet makes
        # the widget and its children paint nothing behind the reactor.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.setWindowTitle("Jarvis")
        self.setWindowIcon(make_icon(64))

        self.memory = Memory()
        self.worker = None
        self._drag_offset = None
        self._quitting = False
        self._chat = CHAT_ON
        self._menu = QMenu(self)
        self._wake = None
        # The wake-word listener runs on its own thread, so it signals us instead
        # of touching Qt: Qt then delivers it here on the GUI thread.
        self.wake_heard.connect(self._on_wake_heard)

        self.reactor = Reactor(150)
        self.reactor.clicked.connect(self.trigger)
        self.reactor.menu_requested.connect(self._show_menu)

        self.status = QLabel(IDLE)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet(
            "color:#dff2ff; background:transparent;"
            "font-size:13px; font-weight:600;"
        )

        self.transcript = QLabel("")
        self.transcript.setAlignment(Qt.AlignCenter)
        self.transcript.setMinimumWidth(190)
        self.transcript.setStyleSheet(
            "color:#9fc4e8; background:transparent; font-size:11px;"
        )

        self.mic_button = QPushButton("🎤 Mic On")
        self.mic_button.setCheckable(True)
        self.mic_button.setCursor(Qt.PointingHandCursor)
        self.mic_button.setStyleSheet(_BUTTON_STYLE)
        self.mic_button.clicked.connect(self.toggle_mute)

        self.stop_button = QPushButton("⏹ Stop")
        self.stop_button.setCursor(Qt.PointingHandCursor)
        self.stop_button.setStyleSheet(_BUTTON_STYLE)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        buttons.addWidget(self.mic_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(4)
        layout.addWidget(self.reactor, alignment=Qt.AlignCenter)
        layout.addWidget(self.status, alignment=Qt.AlignCenter)
        layout.addWidget(self.transcript, alignment=Qt.AlignCenter)
        layout.addLayout(buttons)

        self._build_menu()
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = JarvisTray(self)
            self.tray.show()

        # Started last so both menus already exist and can show the right state.
        self._start_wake()

        self.resize(self.sizeHint())
        self._center()

    def _build_menu(self):
        talk = self._menu.addAction("Talk")
        talk.triggered.connect(self.trigger)
        self.mute_action = self._menu.addAction("Mute mic")
        self.mute_action.setCheckable(True)
        self.mute_action.triggered.connect(self.toggle_mute)
        self.wake_action = self._menu.addAction("Hey Jarvis")
        self.wake_action.setCheckable(True)
        self.wake_action.setChecked(self._wake is not None)
        self.wake_action.triggered.connect(self.toggle_wake)
        self.chat_action = self._menu.addAction("Continuous chat")
        self.chat_action.setCheckable(True)
        self.chat_action.setChecked(self._chat)
        self.chat_action.triggered.connect(self.toggle_chat)
        self.toggle_action = self._menu.addAction("Hide to tray")
        self.toggle_action.triggered.connect(self.toggle_visible)
        self._menu.addSeparator()
        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(self.quit)

    def _show_menu(self, global_pos):
        self._menu.exec_(global_pos)

    def _center(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())

    def set_state(self, state):
        self.status.setText(state)
        self.reactor.set_state(state)
        # Stop can cut a reply short *or* abandon a listen that is still waiting
        # for you to speak, so it is live whenever Jarvis is busy.
        self.stop_button.setEnabled(state in (LISTENING, SPEAKING))
        if state == IDLE:
            self.reactor.set_energy(0.0)
            # Jarvis is done with the microphone, so let "Hey Jarvis" have it back.
            self._resume_wake()
        if self.tray is not None:
            self.tray.setToolTip(f"Jarvis — {state}")

    # -- mic mute --------------------------------------------------------- #
    def toggle_mute(self, _checked=False):
        """Mute/unmute the microphone and reflect it everywhere."""
        self.set_mic_muted(not voice.is_mic_muted())

    def set_mic_muted(self, muted):
        muted = voice.set_mic_muted(muted)
        self.mic_button.setChecked(muted)
        self.mic_button.setText("🔇 Muted" if muted else "🎤 Mic On")
        self.mute_action.setChecked(muted)
        if self.tray is not None:
            self.tray.sync_mute(muted)
        if muted:
            # Muting the mic must also silence the wake word -- no mic, no audio.
            if self._wake is not None:
                self._wake.pause()
            self.set_state(MUTED)
        elif self.status.text() == MUTED:
            self.set_state(IDLE)
        else:
            # A cycle already moved the label off "Muted" (e.g. muted mid-listen),
            # so set_state(IDLE) above will not run: hand the mic back here.
            self._resume_wake()

    # -- stop ------------------------------------------------------------- #
    def _stop(self):
        """Abandon a listen that is waiting, and cut off any reply."""
        voice.stop_listening()
        voice.stop_speaking()

    # -- worker signals --------------------------------------------------- #
    def _on_heard(self, text):
        self.transcript.setText(text if len(text) <= 34 else text[:31] + "…")

    def _flash(self, text):
        self.transcript.setText(text)
        QTimer.singleShot(2000, lambda: self.transcript.setText(""))

    # -- wake word ("Hey Jarvis") ----------------------------------------- #
    def _start_wake(self):
        """Begin listening for the wake word, if it is installed and enabled."""
        if self._wake is not None or not wake.enabled():
            return
        listener = wake.Listener(self._wake_heard)
        listener.start()
        self._wake = listener
        self._sync_wake(True)

    def _wake_heard(self):
        """Called on the wake-word thread the moment it hears "Hey Jarvis"."""
        # Never touch Qt from that thread; hop onto the GUI thread instead.
        self.wake_heard.emit()

    def _on_wake_heard(self):
        """GUI-thread slot: run a normal cycle, exactly as if clicked."""
        self.trigger()

    def _resume_wake(self):
        """Hand the microphone back to the wake listener, but only when idle."""
        if self._wake is None or voice.is_mic_muted():
            return
        if self.worker is not None and self.worker.isRunning():
            return
        self._wake.resume()

    def toggle_wake(self, _checked=False):
        """Turn the wake word off or on at runtime (menu action)."""
        if self._wake is not None:
            self._wake.stop()
            self._wake = None
            self._sync_wake(False)
            self._flash("Wake word off")
            return
        if not wake.available():
            self._sync_wake(False)
            self._flash(f"Wake word: {wake.install_hint()}")
            return
        self._start_wake()
        self._flash("Wake word on — say “Hey Jarvis”")

    def toggle_chat(self, _checked=False):
        """Switch continuous mode on or off, starting with the next session."""
        self._chat = not self._chat
        action = getattr(self, "chat_action", None)
        if action is not None:
            action.setChecked(self._chat)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.sync_chat(self._chat)
        if self._chat:
            self._flash("Continuous chat on — just talk to me")
        else:
            self._flash("Continuous chat off")

    def _sync_wake(self, on):
        """Reflect the wake-word state in both menus."""
        action = getattr(self, "wake_action", None)
        if action is not None:
            action.setChecked(on)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.sync_wake(on)

    def trigger(self):
        """Start a listen/process/speak session (ignored if already busy).

        With continuous mode on, the session keeps going: Jarvis opens the mic
        again after every reply and only stops when you go quiet, say an exit
        word, press Stop or mute the mic.
        """
        # Give the microphone up first: the wake listener and listen() must never
        # hold it at the same time.
        if self._wake is not None:
            self._wake.pause()
        if voice.is_mic_muted():
            self._flash("Mic is muted — click “Mic On” to talk")
            self._resume_wake()
            return
        if self.worker is not None and self.worker.isRunning():
            # A cycle is already running: leave the mic with it.
            return
        if not self.isVisible():
            self.show()
        self.worker = JarvisWorker(self.memory, self, chat=self._chat)
        self.worker.state_changed.connect(self.set_state)
        self.worker.message.connect(self._log)
        self.worker.heard.connect(self._on_heard)
        self.worker.level.connect(self.reactor.set_energy)
        self.worker.start()

    def toggle_visible(self):
        """Show or hide the floating widget (staying alive in the tray)."""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def quit(self):
        """Really exit: stop the tray and the Qt event loop."""
        self._quitting = True
        if self._wake is not None:
            self._wake.stop()
        voice.stop_speaking()
        if self.tray is not None:
            self.tray.hide()
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(5000)
        QApplication.quit()

    @staticmethod
    def _log(text):
        print(text, flush=True)

    # -- window drag (background area) ------------------------------------ #
    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._show_menu(event.globalPos())
            return
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()                      # hide to tray, don't quit
        elif event.key() == Qt.Key_M:
            self.toggle_mute()
        elif event.key() == Qt.Key_Space:
            self._stop()
        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self.tray is not None:
            self.tray.sync_toggle(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.tray is not None:
            self.tray.sync_toggle(False)

    def closeEvent(self, event):
        """Closing the widget hides it to the tray instead of quitting."""
        if self._quitting or self.tray is None:
            if self.worker is not None and self.worker.isRunning():
                self.worker.wait(5000)
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "Jarvis", "Still running in the tray. Click the icon to talk.",
            QSystemTrayIcon.Information, 3000,
        )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Jarvis")
    app.setWindowIcon(make_icon(64))
    # Keep running after the floating window is hidden/closed -> tray lives on.
    app.setQuitOnLastWindowClosed(False)
    widget = JarvisWidget()
    widget.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
