"""Floating ChatGPT-Voice-style desktop widget + system tray for Jarvis (PyQt5).

The window is a frameless, rounded dark card with a breathing gradient orb in the
centre, a live waveform underneath it, a modern status line and quiet window
controls. It deliberately gets out of the way: the audio + agent work runs on a
QThread, the app lives in the system tray, and closing the card hides it instead
of quitting.

While Jarvis speaks, a :class:`voice.BargeInMonitor` watches the microphone. If
you start talking over it, the reply is cut short and the widget returns to
"Listening..." -- no Stop button required.

Controls:
  card          left-click = talk          drag = move
                right-click = menu         Esc = hide to tray
                – / ✕ (top right) = minimise / hide to tray
  buttons       Mic = mute/unmute           Stop = cut the reply
  tray icon     left-click = talk           right-click = menu
"""

import math
import sys
from collections import deque

from PyQt5.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRect,
                          QRectF, Qt, QThread, QTimer, QUrl, pyqtProperty,
                          pyqtSignal)
from PyQt5.QtGui import (QBrush, QColor, QDesktopServices, QIcon,
                         QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                         QRadialGradient)
from PyQt5.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                             QLineEdit, QMenu, QPushButton, QSizeGrip,
                             QSystemTrayIcon, QVBoxLayout, QWidget)

import config
import startup
import voice
from config import CHAT_ON, CHAT_TIMEOUT
from icon_art import render_icon
from main import enable_offline_mode, handle, is_exit, is_shutdown
from memory import Memory
from voice import BargeInMonitor, listen, speak
import wake

# What Jarvis says when a continuous session ends (you said an exit word, went
# quiet, or pressed Stop).
_SESSION_END = "Session over. See you soon, sir."
# Explicit "close yourself" / "shut down Jarvis" commands quit the app itself.
_FAREWELL = "Goodbye, sir"

IDLE = "Idle"
LISTENING = "Listening..."
THINKING = "Thinking..."
SPEAKING = "JARVIS is speaking..."
MUTED = "Muted"

# State palette: (outer glow, core light). Dark-card friendly, saturated enough
# to read at a glance without feeling like a toy.
_COLORS = {
    IDLE:      (QColor(56, 189, 248), QColor(186, 230, 253)),
    LISTENING: (QColor(52, 211, 153), QColor(167, 243, 208)),
    THINKING:  (QColor(251, 191, 36), QColor(253, 230, 138)),
    SPEAKING:  (QColor(167, 139, 250), QColor(221, 214, 254)),
    MUTED:     (QColor(100, 116, 139), QColor(148, 163, 184)),
}

_BUTTON_STYLE = """
QPushButton {
    color:#E5E7EB; background:rgba(255,255,255,16);
    border:1px solid rgba(255,255,255,26);
    border-radius:16px; padding:7px 16px;
    font-size:12px; font-weight:600;
}
QPushButton:hover { background:rgba(255,255,255,32); }
QPushButton:pressed { background:rgba(255,255,255,48); }
QPushButton:disabled {
    color:rgba(229,231,235,90); background:rgba(255,255,255,8);
    border-color:rgba(255,255,255,14);
}
QPushButton:checked {
    color:#FEE2E2; background:rgba(229,72,77,220);
    border-color:rgba(255,160,160,140);
}
"""

_WINDOW_BUTTON_STYLE = """
QPushButton {
    color:#9CA3AF; background:transparent; border:none;
    border-radius:12px; font-size:15px; font-weight:700; padding:0px;
}
QPushButton:hover { background:rgba(255,255,255,28); color:#F9FAFB; }
QPushButton#closeButton:hover { background:#E5484D; color:#FFFFFF; }
"""


def make_icon(size=64):
    """Return the modern glowing-orb/J icon at ``size`` px as a ``QIcon``."""
    return QIcon(QPixmap.fromImage(render_icon(size)))


class Orb(QWidget):
    """The glowing gradient orb: click to talk, drag to move the window.

    Idle uses a looping :class:`QPropertyAnimation` on the ``pulse`` property to
    breathe. Listening/Speaking stop the breathing animation and instead react to
    live loudness (``set_energy``) with a phase-driven wave halo.
    """

    clicked = pyqtSignal()
    menu_requested = pyqtSignal(object)

    def __init__(self, diameter=180, parent=None):
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._state = IDLE
        self._phase = 0.0
        self._energy = 0.0
        self._pulse = 1.0
        self._press = None
        self._dragged = False
        self.setCursor(Qt.PointingHandCursor)

        self._breathe = QPropertyAnimation(self, b"pulse", self)
        self._breathe.setDuration(2600)
        self._breathe.setStartValue(0.94)
        self._breathe.setEndValue(1.06)
        self._breathe.setEasingCurve(QEasingCurve.InOutSine)
        self._breathe.setLoopCount(-1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)              # ~30 fps wave motion
        self._start_breathe()

    # -- animated breathing property ------------------------------------- #
    def _get_pulse(self):
        return self._pulse

    def _set_pulse(self, value):
        self._pulse = float(value)
        self.update()

    pulse = pyqtProperty(float, fget=_get_pulse, fset=_set_pulse)

    def _start_breathe(self):
        if self._breathe.state() != QPropertyAnimation.Running:
            self._breathe.start()

    def _tick(self):
        # Thinking pulses a little faster than the other active states.
        speed = 0.22 if self._state == THINKING else 0.13
        self._phase = (self._phase + speed) % (2 * math.pi)
        self.update()

    def set_state(self, state):
        self._state = state
        if state == IDLE:
            self._start_breathe()
        else:
            self._breathe.stop()
            self._pulse = 1.0
        self.update()

    def set_energy(self, value):
        """Live loudness, 0..1, drives the glow radius and brightness."""
        try:
            self._energy = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self._energy = 0.0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width, height = self.width(), self.height()
        center = QPointF(width / 2.0, height / 2.0)
        # Leave room inside the widget for the bloom and the wave rings, so the
        # glow never gets clipped into a visible square at the widget's edge.
        base_radius = min(width, height) / 2.0 * 0.72
        glow, core = _COLORS.get(self._state, _COLORS[IDLE])

        if self._state == IDLE:
            scale = self._pulse
        elif self._state == LISTENING:
            scale = 1.0 + 0.035 * math.sin(self._phase * 1.4) + 0.12 * self._energy
        elif self._state == SPEAKING:
            scale = 1.0 + 0.030 * math.sin(self._phase * 2.2) + 0.10 * self._energy
        elif self._state == THINKING:
            scale = 1.0 + 0.020 * math.sin(self._phase * 2.8)
        else:
            scale = 1.0
        radius = base_radius * scale

        # --- ambient bloom ------------------------------------------------- #
        # Fade the bloom to zero by the widget edge; otherwise the clipped
        # gradient shows up as a faint square behind the orb.
        halo_radius = min(base_radius * (1.60 + 0.25 * self._energy),
                          min(width, height) / 2.0 - 1.0)
        halo = QRadialGradient(center, halo_radius)
        halo.setColorAt(0.0, QColor(glow.red(), glow.green(), glow.blue(), 80))
        halo.setColorAt(0.55, QColor(glow.red(), glow.green(), glow.blue(), 26))
        halo.setColorAt(1.0, QColor(glow.red(), glow.green(), glow.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(center, halo_radius, halo_radius)

        # --- travelling wave rings (Listening / Speaking) ------------------- #
        if self._state in (LISTENING, SPEAKING):
            painter.setBrush(Qt.NoBrush)
            max_ring = min(width, height) / 2.0 - 1.5
            for offset, alpha in ((0.045, 95), (0.105, 55), (0.165, 28)):
                ring = radius * (1.0 + offset + 0.020 * math.sin(
                    self._phase + offset * 9.0))
                ring = min(ring, max_ring)
                painter.setPen(QPen(
                    QColor(glow.red(), glow.green(), glow.blue(), alpha),
                    max(1.0, radius * 0.020)))
                painter.drawEllipse(center, ring, ring)
            painter.setPen(Qt.NoPen)

        # --- the orb body --------------------------------------------------- #
        body = QRadialGradient(
            QPointF(center.x() - radius * 0.10, center.y() - radius * 0.12),
            radius * 1.25)
        body.setColorAt(0.0, core.lighter(112))
        body.setColorAt(0.55, glow)
        body.setColorAt(1.0, glow.darker(155))
        painter.setBrush(QBrush(body))
        painter.drawEllipse(center, radius, radius)

        # --- inner highlight (glass) ---------------------------------------- #
        highlight_center = QPointF(center.x() - radius * 0.24,
                                   center.y() - radius * 0.28)
        highlight_radius = radius * (0.46 + 0.10 * self._energy)
        highlight = QRadialGradient(highlight_center, highlight_radius)
        highlight.setColorAt(0.0, QColor(255, 255, 255, 125))
        highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(highlight))
        painter.drawEllipse(highlight_center, highlight_radius, highlight_radius)

        # --- muted badge ---------------------------------------------------- #
        if self._state == MUTED:
            badge_radius = radius * 0.28
            bx = center.x() + radius * 0.58
            by = center.y() + radius * 0.58
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(22, 26, 34, 235)))
            painter.drawEllipse(QPointF(bx, by), badge_radius, badge_radius)
            slash = QPen(QColor(248, 113, 113), max(2.0, radius * 0.085))
            slash.setCapStyle(Qt.RoundCap)
            painter.setPen(slash)
            offset = badge_radius * 0.5
            painter.drawLine(QPointF(bx - offset, by - offset),
                             QPointF(bx + offset, by + offset))

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


# Backwards-compatible name: older code and tests called the orb "Reactor".
Reactor = Orb


class Waveform(QWidget):
    """A live 13-bar waveform driven by ``voice.set_level_sink``.

    A short rolling history makes the bars scroll rather than twitch. Idle keeps
    a barely-there ripple so the card never looks frozen.
    """

    BARS = 13

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(58)
        self.setMinimumWidth(240)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._bars = deque([0.0] * self.BARS, maxlen=self.BARS)
        self._level = 0.0
        self._phase = 0.0
        self._state = IDLE

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)              # ~25 fps bar motion

    def set_level(self, value):
        """Feed one 0..1 loudness sample (mic RMS or TTS envelope)."""
        try:
            self._level = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self._level = 0.0

    def set_state(self, state):
        self._state = state

    def _tick(self):
        self._phase += 0.55
        # Modulate with a travelling wave so neighbouring bars differ, but keep a
        # healthy floor (60%) so the bars read as a waveform rather than dots.
        wave = abs(math.sin(self._phase))
        value = self._level * (0.60 + 0.40 * wave)
        if self._state == IDLE:
            # A quiet idle shimmer; never zero, never distracting.
            value = max(value, 0.035 + 0.030 * abs(math.sin(self._phase * 0.6)))
        self._bars.append(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width, height = self.width(), self.height()
        count = len(self._bars)
        gap = 6.0
        bar_width = max(3.0, (width - gap * (count - 1)) / count)
        total_width = bar_width * count + gap * (count - 1)
        x = (width - total_width) / 2.0
        glow, core = _COLORS.get(self._state, _COLORS[IDLE])

        painter.setPen(Qt.NoPen)
        for value in self._bars:
            bar_height = max(4.0, (height - 10.0) * value)
            rect = QRectF(x, (height - bar_height) / 2.0, bar_width, bar_height)
            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            gradient.setColorAt(0.0, core)
            gradient.setColorAt(1.0, glow)
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(rect, bar_width / 2.0, bar_width / 2.0)
            x += bar_width + gap

        painter.end()


class JarvisWorker(QThread):
    """Runs listen -> try_local/run_agent -> speak off the GUI thread.

    In continuous-chat mode (``chat=True``) it does that *in a loop*: after each
    reply it opens the microphone again, so a normal conversation flows without
    clicking. It leaves on any of: an exit word, silence (you went quiet for the
    chat timeout), the mic being muted, or the Stop button.

    While a reply plays, barge-in is armed: starting to talk over Jarvis cuts the
    speech and returns to listening instead of ending the session.
    """

    state_changed = pyqtSignal(str)
    message = pyqtSignal(str)
    heard = pyqtSignal(str)
    level = pyqtSignal(float)
    mic_changed = pyqtSignal(bool)
    exit_requested = pyqtSignal()

    def __init__(self, memory, parent=None, chat=False, chat_timeout=CHAT_TIMEOUT):
        super().__init__(parent)
        self.memory = memory
        self.chat = chat
        self.chat_timeout = chat_timeout
        self._barged_in = False

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
                # going to sleep after 30 s. If the previous turn was cut short
                # by barge-in, tell listen() so a pure "stop" is not dispatched.
                was_barge = self._barged_in
                self._barged_in = False
                user = listen(None if first_turn else self.chat_timeout,
                              barge=was_barge)
                first_turn = False
                if not user:
                    # You went quiet (or pressed Stop / muted the mic): end the
                    # conversation instead of grabbing the microphone again.
                    return
                # listen() already prints the "👤 <user>" line to stdout and speak()
                # prints "🤖 <reply>" exactly once, so we only drive the state
                # colours here.
                self.heard.emit(user)

                if is_shutdown(user):
                    # Explicit "close yourself" / "shut down Jarvis" closes the
                    # app, not just the conversation.
                    self.state_changed.emit(SPEAKING)
                    speak(_FAREWELL)
                    self.exit_requested.emit()
                    return
                if is_exit(user):
                    # A spoken exit word ends the session by voice, but the app
                    # stays available in the tray.
                    self.state_changed.emit(SPEAKING)
                    speak(_SESSION_END)
                    return

                self.state_changed.emit(THINKING)
                # main.handle() is the single dispatch path: it resolves any pending
                # confirmation, then tries the router and finally the agent.
                reply, _kind, _tokens = handle(user, self.memory)
                # A local command can mute/unmute the mic (media playback mutes
                # it), so keep the GUI button and tray in sync.
                self.mic_changed.emit(voice.is_mic_muted())

                self.state_changed.emit(SPEAKING)
                if self._speak_with_barge_in(reply):
                    # The user talked over the reply: go straight back to
                    # listening for the new request instead of ending the session.
                    continue
                if voice._stop_speaking.is_set():
                    # Stop cut the reply short: leave the conversation rather
                    # than immediately waiting for the next sentence.
                    return
        except Exception as exc:                      # never kill the UI
            self.message.emit(f"⚠️ {exc}")
        finally:
            voice.set_level_sink(None)
            self.level.emit(0.0)
            # A voice/media command may have muted the mic: leave the card in
            # the muted state instead of pretending it is ready to listen.
            self.state_changed.emit(MUTED if voice.is_mic_muted() else IDLE)

    # -- stop ------------------------------------------------------------- #
    def stop(self):
        """Ask the loop to abandon listening/speaking and leave.

        Safe to call from the GUI thread: both helpers only set the same
        threading events the Stop button uses, so the worker ends at its next
        checkpoint instead of being terminated abruptly.
        """
        voice.stop_listening()
        voice.stop_speaking()

    def _barge_monitor(self, on_barge):
        """Build the monitor (a seam tests can replace without a microphone)."""
        return BargeInMonitor(on_barge=on_barge)

    def _speak_with_barge_in(self, reply):
        """Speak ``reply``; return True if the user interrupted it by speaking."""
        self._barged_in = False

        def on_barge():
            self._barged_in = True
            voice.stop_speaking()
            # Update the UI immediately rather than waiting for speak() to return.
            self.state_changed.emit(LISTENING)

        monitor = self._barge_monitor(on_barge)
        try:
            monitor.start()
        except Exception:
            pass
        try:
            speak(reply)
        finally:
            monitor.stop()
        return self._barged_in


class ApiKeyDialog(QDialog):
    """Modal first-run wizard for the user's own Groq API key.

    The key is masked while typing and is never logged. ``Save & Start`` writes
    it to the per-user ``.env`` through :func:`config.save_api_key`, which uses
    mode ``0600``. Closing the dialog (or choosing ``Continue offline``) leaves
    the key unset; the caller then starts JARVIS in offline/local mode.
    """

    CONSOLE_URL = "https://console.groq.com/keys"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.saved_path = None
        self.setWindowTitle("JARVIS Setup")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setStyleSheet(
            "QDialog { background:#1E1E1E; }"
            "QLabel { color:#E5E7EB; background:transparent; }"
            "QLineEdit { color:#F9FAFB; background:rgba(255,255,255,12);"
            " border:1px solid rgba(255,255,255,40); border-radius:8px;"
            " padding:9px; font-size:13px; }"
            "QLineEdit:focus { border-color:#38BDF8; }"
            "QPushButton { color:#E5E7EB; background:rgba(255,255,255,16);"
            " border:1px solid rgba(255,255,255,26); border-radius:8px;"
            " padding:8px 14px; font-size:12px; font-weight:600; }"
            "QPushButton:hover { background:rgba(255,255,255,32); }"
            "QPushButton#saveButton { color:#0B1220; background:#38BDF8;"
            " border-color:#38BDF8; }"
            "QPushButton#saveButton:hover { background:#7DD3FC; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel("JARVIS")
        heading.setStyleSheet(
            "color:#F9FAFB; font-size:20px; font-weight:800;"
            " letter-spacing:1.2px;"
        )
        layout.addWidget(heading)

        prompt = QLabel(
            "Welcome to JARVIS. Please enter your Groq API Key:"
        )
        prompt.setWordWrap(True)
        prompt.setStyleSheet("font-size:14px; font-weight:600;")
        layout.addWidget(prompt)

        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText("gsk_...")
        self.key_input.setClearButtonEnabled(True)
        layout.addWidget(self.key_input)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color:#FCA5A5; font-size:11px;")
        layout.addWidget(self.error_label)

        self.console_button = QPushButton("Get a free key")
        self.console_button.setToolTip(self.CONSOLE_URL)
        self.console_button.clicked.connect(self._open_console)

        self.offline_button = QPushButton("Continue offline")
        self.offline_button.clicked.connect(self.reject)

        self.save_button = QPushButton("Save & Start")
        self.save_button.setObjectName("saveButton")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.console_button)
        buttons.addStretch(1)
        buttons.addWidget(self.offline_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self.key_input.returnPressed.connect(self._save)

    def _open_console(self):
        """Open the Groq key page in the user's default browser."""
        QDesktopServices.openUrl(QUrl(self.CONSOLE_URL))

    def api_key(self):
        """Return the trimmed key currently in the field."""
        return self.key_input.text().strip()

    def _save(self):
        """Validate and persist the key, keeping the dialog open on failure."""
        key = self.api_key()
        if not config.is_api_key_configured(key):
            self.error_label.setText(
                "Please enter a valid Groq API key, or continue offline."
            )
            self.key_input.setFocus()
            return
        try:
            self.saved_path = config.save_api_key(key)
        except (OSError, ValueError) as exc:
            self.error_label.setText(f"Could not save the key: {exc}")
            return
        self.accept()


def run_setup_wizard():
    """Show the modal setup dialog when no API key is configured.

    Returns ``True`` if a usable key is available afterwards. A dismissed
    dialog switches the process to offline/local mode and returns ``False``.
    """
    if config.is_api_key_configured():
        return True
    dialog = ApiKeyDialog()
    accepted = dialog.exec_() == QDialog.Accepted
    if accepted and config.is_api_key_configured():
        return True
    enable_offline_mode()
    return False


class JarvisTray(QSystemTrayIcon):
    """Tray icon so Jarvis can live in the background."""

    def __init__(self, widget):
        super().__init__(make_icon(64), widget)
        self.widget = widget
        self.setToolTip("JARVIS — click to talk")

        menu = QMenu()
        talk = menu.addAction("Talk")
        talk.triggered.connect(widget.trigger)
        self.mute_action = menu.addAction("Mute mic")
        self.mute_action.setCheckable(True)
        self.mute_action.triggered.connect(widget.toggle_mute)
        self.wake_action = menu.addAction("Hey JARVIS")
        self.wake_action.setCheckable(True)
        self.wake_action.setChecked(widget._wake is not None)
        self.wake_action.triggered.connect(widget.toggle_wake)
        self.chat_action = menu.addAction("Continuous chat")
        self.chat_action.setCheckable(True)
        self.chat_action.setChecked(widget._chat)
        self.chat_action.triggered.connect(widget.toggle_chat)
        self.autostart_action = menu.addAction("Start on Boot")
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(startup.is_autostart_enabled())
        self.autostart_action.triggered.connect(widget.toggle_autostart)
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

    def sync_autostart(self, on):
        self.autostart_action.setChecked(on)


# Global wake hotkeys: press either from any application to talk to Jarvis.
# Ctrl+Shift+J is the documented default; Ctrl+Alt+J is accepted too because it
# was requested and some layouts treat Ctrl+Shift+J oddly.
HOTKEY = "<ctrl>+<shift>+j"
ALT_HOTKEY = "<ctrl>+<alt>+j"
HOTKEYS = (HOTKEY, ALT_HOTKEY)

# How close (in px) the pointer must be to a window edge before a drag resizes
# the frameless card instead of moving it.
_RESIZE_MARGIN = 8


def _make_hotkey_listener(callback):
    """Build the global-hotkey listener, importing ``pynput`` lazily.

    Kept as a module-level seam so tests can substitute a fake listener (the
    real one grabs the keyboard system-wide, which must never happen in a test).
    Raises ``ImportError`` when ``pynput`` is not installed; the caller degrades
    gracefully.
    """
    from pynput import keyboard
    return keyboard.GlobalHotKeys({key: callback for key in HOTKEYS})


class JarvisWidget(QWidget):
    """The floating rounded card: orb + waveform + status + mic/stop controls."""

    # Emitted by the wake-word thread (never touch Qt from that thread).
    wake_heard = pyqtSignal()
    # Emitted by the pynput global-hotkey thread (never touch Qt from it either).
    hotkey_pressed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        # Transparent top-level window. WA_TranslucentBackground asks the
        # compositor for an ARGB surface; WA_NoSystemBackground stops Qt from
        # erasing that surface with an opaque system colour. paintEvent() then
        # draws the rounded dark card, so the corners really are transparent.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        # Scope the transparency to the card itself: a bare rule would also make
        # the context menu and any child dialogs transparent.
        self.setObjectName("jarvisCard")
        self.setStyleSheet("#jarvisCard { background: transparent; }")
        self.setWindowTitle("JARVIS")
        self.setWindowIcon(make_icon(64))
        # Frameless windows have no system resize border, so the card resizes
        # through the QSizeGrip in the corner and the custom edge dragging in the
        # mouse handlers below.
        self.resize(380, 560)
        self.setMinimumSize(320, 440)
        self.setMouseTracking(True)

        self.memory = Memory()
        self.worker = None
        self._drag_offset = None
        # Edge-resize drag state: which edges are grabbed, where the drag began
        # and the geometry it began from.
        self._resize_edges = set()
        self._resize_origin = None
        self._resize_start = None
        self._quitting = False
        self._chat = CHAT_ON
        self._menu = QMenu(self)
        self._wake = None
        # The wake-word listener runs on its own thread, so it signals us instead
        # of touching Qt: Qt then delivers it here on the GUI thread.
        self.wake_heard.connect(self._on_wake_heard)
        self._hotkey = None
        self.wake_server = None
        self.hotkey_pressed.connect(self._on_hotkey)

        self._build_ui()
        self._build_menu()
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = JarvisTray(self)
            self.tray.show()

        # Started last so both menus already exist and can show the right state.
        self._start_wake()
        self._start_hotkey()
        self._center()

    # -- layout ----------------------------------------------------------- #
    def _build_ui(self):
        header = QHBoxLayout()
        header.setSpacing(4)
        brand = QLabel("JARVIS")
        brand.setStyleSheet(
            "color:#F9FAFB; background:transparent; font-size:17px;"
            "font-weight:800; letter-spacing:1.2px;"
        )
        header.addWidget(brand)
        header.addStretch(1)

        self.minimize_button = self._window_button("–", "minimizeButton")
        self.minimize_button.setToolTip("Minimise")
        self.minimize_button.clicked.connect(self._minimize)
        self.close_button = self._window_button("✕", "closeButton")
        self.close_button.setToolTip("Hide to tray")
        self.close_button.clicked.connect(self._close_to_tray)
        header.addWidget(self.minimize_button)
        header.addWidget(self.close_button)

        self.orb = Orb(230)
        self.orb.clicked.connect(self.trigger)
        self.orb.menu_requested.connect(self._show_menu)
        # Backwards-compatible alias: older code called the orb the "reactor".
        self.reactor = self.orb

        self.waveform = Waveform()
        self.waveform.setFixedWidth(300)

        self.status = QLabel(IDLE)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet(
            "color:#F3F4F6; background:transparent;"
            "font-size:15px; font-weight:600; letter-spacing:0.2px;"
        )

        self.transcript = QLabel("")
        self.transcript.setAlignment(Qt.AlignCenter)
        self.transcript.setMinimumWidth(240)
        self.transcript.setStyleSheet(
            "color:#9CA3AF; background:transparent; font-size:11px;"
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
        layout.setContentsMargins(22, 16, 22, 18)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addStretch(1)
        layout.addWidget(self.orb, alignment=Qt.AlignCenter)
        layout.addWidget(self.waveform, alignment=Qt.AlignCenter)
        layout.addSpacing(4)
        layout.addWidget(self.status, alignment=Qt.AlignCenter)
        layout.addWidget(self.transcript, alignment=Qt.AlignCenter)
        layout.addStretch(1)
        layout.addLayout(buttons)

        # Frameless card: a visible QSizeGrip in the bottom-right corner resizes
        # the window; the custom edge dragging below covers the rest of the
        # border.
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch(1)
        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(16, 16)
        self.size_grip.setToolTip("Resize")
        grip_row.addWidget(self.size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        layout.addLayout(grip_row)

    def _window_button(self, text, name):
        button = QPushButton(text)
        button.setObjectName(name)
        button.setFixedSize(28, 28)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setStyleSheet(_WINDOW_BUTTON_STYLE)
        return button

    def paintEvent(self, event):
        """Paint the rounded dark card behind all the child widgets."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 26.0, 26.0)

        card = QLinearGradient(rect.topLeft(), rect.bottomRight())
        card.setColorAt(0.0, QColor("#25262B"))
        card.setColorAt(0.45, QColor("#1E1E1E"))
        card.setColorAt(1.0, QColor("#141518"))
        painter.fillPath(path, QBrush(card))

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        painter.drawPath(path)
        painter.end()

    def _build_menu(self):
        talk = self._menu.addAction("Talk")
        talk.triggered.connect(self.trigger)
        self.mute_action = self._menu.addAction("Mute mic")
        self.mute_action.setCheckable(True)
        self.mute_action.triggered.connect(self.toggle_mute)
        self.wake_action = self._menu.addAction("Hey JARVIS")
        self.wake_action.setCheckable(True)
        self.wake_action.setChecked(self._wake is not None)
        self.wake_action.triggered.connect(self.toggle_wake)
        self.chat_action = self._menu.addAction("Continuous chat")
        self.chat_action.setCheckable(True)
        self.chat_action.setChecked(self._chat)
        self.chat_action.triggered.connect(self.toggle_chat)
        self.autostart_action = self._menu.addAction("Start on Boot")
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(startup.is_autostart_enabled())
        self.autostart_action.triggered.connect(self.toggle_autostart)
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

    def _minimize(self):
        """Minimise the card; the tray keeps Jarvis running."""
        self.showMinimized()

    def _close_to_tray(self):
        """X button: hide the card to the tray (the app keeps running).

        Delegates to :meth:`close`, which routes through :meth:`closeEvent` so
        the window-manager close and the ✕ button behave identically.
        """
        self.close()

    # -- state ------------------------------------------------------------ #
    def set_state(self, state):
        self.status.setText(state)
        self.orb.set_state(state)
        self.waveform.set_state(state)
        # Stop can cut a reply short *or* abandon a listen that is still waiting
        # for you to speak, so it is live whenever Jarvis is busy.
        self.stop_button.setEnabled(state in (LISTENING, SPEAKING))
        if state == IDLE:
            self.orb.set_energy(0.0)
            self.waveform.set_level(0.0)
            # Jarvis is done with the microphone, so let "Hey Jarvis" have it back.
            self._resume_wake()
        if self.tray is not None:
            self.tray.setToolTip(f"JARVIS — {state}")

    def _on_level(self, value):
        """Feed one live loudness sample to the orb and the waveform."""
        self.orb.set_energy(value)
        self.waveform.set_level(value)

    # -- mic mute --------------------------------------------------------- #
    def toggle_mute(self, _checked=False):
        """Mute/unmute the microphone and reflect it everywhere."""
        self.set_mic_muted(not voice.is_mic_muted())

    def _sync_mic_ui(self, muted):
        """Reflect the real voice mute state without changing it (worker signal)."""
        self.mic_button.setChecked(muted)
        self.mic_button.setText("🔇 Muted" if muted else "🎤 Mic On")
        action = getattr(self, "mute_action", None)
        if action is not None:
            action.setChecked(muted)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.sync_mute(muted)

    def set_mic_muted(self, muted):
        muted = voice.set_mic_muted(muted)
        self._sync_mic_ui(muted)
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
    def _halt_for_hide(self):
        """Cancel listening/speaking and force the card back to Idle.

        Hiding to the tray must not leave a worker holding the microphone or a
        reply playing invisibly. The worker's later state signal is detached so
        its final MUTED/IDLE update cannot overwrite the forced Idle state after
        the window is already hidden.
        """
        worker = self.worker
        if worker is not None:
            try:
                worker.state_changed.disconnect(self.set_state)
            except (TypeError, RuntimeError):
                # Already disconnected, or the C++ object is gone: either way
                # there is nothing left to detach.
                pass
            # The worker's final state signal is detached above, so hand the
            # microphone back to the wake listener explicitly once it ends.
            # (No-op while muted: _resume_wake checks the mute flag.)
            finished = getattr(worker, "finished", None)
            if finished is not None:
                try:
                    finished.connect(self._resume_wake)
                except (TypeError, RuntimeError):
                    pass
            stop = getattr(worker, "stop", None)
            if callable(stop):
                stop()
        # Always clear both events, even with no worker: a timed-out listen may
        # still be inside voice.listen() and must not keep the mic open.
        voice.stop_listening()
        voice.stop_speaking()
        self.set_state(IDLE)

    def _stop(self):
        """Abandon a listen that is waiting, and cut off any reply."""
        worker = self.worker
        stop = getattr(worker, "stop", None)
        if callable(stop):
            stop()
        else:
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

    # -- global hotkey (Ctrl+Shift+J) ------------------------------------- #
    def _start_hotkey(self):
        """Start the global Ctrl+Shift+J hotkey (best effort).

        The listener runs on its own thread; its callback only emits a signal so
        Qt performs the actual work on the GUI thread. A missing ``pynput`` (or a
        system that refuses the grab) must never stop the GUI from starting.
        """
        if self._hotkey is not None:
            return
        try:
            listener = _make_hotkey_listener(self._on_hotkey_signal)
            listener.start()
        except Exception as exc:                       # optional dependency
            print(f"[gui] global hotkey unavailable: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            return
        self._hotkey = listener

    def _stop_hotkey(self):
        """Stop and release the global hotkey listener."""
        listener = self._hotkey
        self._hotkey = None
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:
            pass

    def _on_hotkey_signal(self):
        """Called on the pynput thread: hop onto the GUI thread."""
        # Never touch Qt from that thread; let Qt deliver it on the GUI thread.
        self.hotkey_pressed.emit()

    def _on_hotkey(self):
        """GUI-thread slot: wake Jarvis from Idle, hidden or Mute.

        This only brings the *existing* window forward -- it never builds a new
        widget, tray icon or hotkey listener, so pressing the hotkey can never
        spawn a second Jarvis.
        """
        if voice.is_mic_muted():
            # The hotkey is an explicit "talk to me" gesture, so it unmutes.
            self.set_mic_muted(False)
        # Restore the card if it was hidden or minimised, then start a cycle.
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()
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
        self._flash("Wake word on — say “Hey JARVIS”")

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

    def toggle_autostart(self, _checked=False):
        """Install or remove the per-user login entry for JARVIS."""
        enable = not startup.is_autostart_enabled()
        try:
            startup.set_autostart(enable)
        except (OSError, ImportError) as exc:
            self._sync_autostart(startup.is_autostart_enabled())
            message = f"Auto-start error: {exc}"
            self._flash(message)
            if self.tray is not None:
                self.tray.showMessage(
                    "JARVIS", message, QSystemTrayIcon.Warning, 3000,
                )
            return

        self._sync_autostart(enable)
        message = "Auto-start enabled" if enable else "Auto-start disabled"
        self._flash(message)
        if self.tray is not None:
            self.tray.showMessage(
                "JARVIS", message, QSystemTrayIcon.Information, 2500,
            )

    def _sync_autostart(self, enabled):
        """Reflect the real auto-start state in both menus."""
        action = getattr(self, "autostart_action", None)
        if action is not None:
            action.setChecked(enabled)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.sync_autostart(enabled)

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
        self.worker.level.connect(self._on_level)
        self.worker.mic_changed.connect(self._sync_mic_ui)
        self.worker.exit_requested.connect(self.quit)
        self.worker.start()

    def toggle_visible(self):
        """Show or hide the floating widget (staying alive in the tray)."""
        if self.isVisible():
            self._halt_for_hide()
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def quit(self):
        """Really exit: stop the tray and the Qt event loop."""
        self._quitting = True
        self._stop_hotkey()
        if self.wake_server is not None:
            self.wake_server.stop()
            self.wake_server = None
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

    # -- window drag + resize (frameless) --------------------------------- #
    def _edge_at(self, pos):
        """Return the set of window edges/corners under ``pos`` (empty = none).

        Used both to change the cursor on hover and to decide whether a press
        starts a resize instead of a move.
        """
        margin = _RESIZE_MARGIN
        edges = set()
        if pos.x() <= margin:
            edges.add("left")
        elif pos.x() >= self.width() - margin:
            edges.add("right")
        if pos.y() <= margin:
            edges.add("top")
        elif pos.y() >= self.height() - margin:
            edges.add("bottom")
        return edges

    @staticmethod
    def _cursor_for_edges(edges):
        """The resize cursor that matches a set of grabbed edges."""
        if edges in ({"left", "top"}, {"right", "bottom"}):
            return Qt.SizeFDiagCursor
        if edges in ({"right", "top"}, {"left", "bottom"}):
            return Qt.SizeBDiagCursor
        if edges in ({"left"}, {"right"}):
            return Qt.SizeHorCursor
        if edges in ({"top"}, {"bottom"}):
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._show_menu(event.globalPos())
            return
        if event.button() == Qt.LeftButton:
            edges = self._edge_at(event.pos())
            if edges:
                # Near an edge/corner: resize rather than move.
                self._resize_edges = edges
                self._resize_origin = event.globalPos()
                self._resize_start = self.geometry()
            else:
                self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._resize_edges:
            self._apply_resize(event.globalPos())
        elif self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
        else:
            # Hovering (no button): show the matching resize cursor.
            self.setCursor(self._cursor_for_edges(self._edge_at(event.pos())))
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        self._resize_edges = set()
        self._resize_origin = None
        self._resize_start = None
        self.setCursor(Qt.ArrowCursor)
        event.accept()

    def _apply_resize(self, global_pos):
        """Resize the window by the drag delta, honouring the minimum size."""
        start = self._resize_start
        origin = self._resize_origin
        if start is None or origin is None:
            return
        dx = global_pos.x() - origin.x()
        dy = global_pos.y() - origin.y()
        geo = QRect(start)
        if "left" in self._resize_edges:
            geo.setLeft(start.left() + dx)
        if "right" in self._resize_edges:
            geo.setRight(start.right() + dx)
        if "top" in self._resize_edges:
            geo.setTop(start.top() + dy)
        if "bottom" in self._resize_edges:
            geo.setBottom(start.bottom() + dy)
        # Never shrink below the minimum: keep the dragged edge under the cursor.
        if geo.width() < self.minimumWidth():
            if "left" in self._resize_edges:
                geo.setLeft(geo.right() - self.minimumWidth() + 1)
            else:
                geo.setRight(geo.left() + self.minimumWidth() - 1)
        if geo.height() < self.minimumHeight():
            if "top" in self._resize_edges:
                geo.setTop(geo.bottom() - self.minimumHeight() + 1)
            else:
                geo.setBottom(geo.top() + self.minimumHeight() - 1)
        self.setGeometry(geo)

    def leaveEvent(self, event):
        if not self._resize_edges:
            self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._halt_for_hide()            # same clean Idle state as the ✕
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
        """Close-to-tray: the X hides the card, it never quits the app.

        Only a real quit (``Quit`` in the tray/menu, which sets ``_quitting``)
        lets the window actually close. Everything else ignores the event and
        hides to the tray, keeping the tray icon available to reopen or exit.
        """
        if self._quitting:
            self._stop_hotkey()
            if self.worker is not None and self.worker.isRunning():
                self.worker.wait(5000)
            event.accept()
            return
        event.ignore()
        # The ✕ hides to the tray, so end any active turn first: no invisible
        # listening, no reply playing from a hidden window, and the orb is blue
        # (Idle) the next time the card is opened.
        self._halt_for_hide()
        self.hide()
        if self.tray is not None:
            self.tray.show()               # make sure the icon stays visible
            self.tray.showMessage(
                "JARVIS", "Still running in the tray. Click the icon to talk.",
                QSystemTrayIcon.Information, 3000,
            )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")
    app.setWindowIcon(make_icon(64))
    # Keep running after the floating window is hidden/closed -> tray lives on.
    app.setQuitOnLastWindowClosed(False)
    configured = run_setup_wizard()
    widget = JarvisWidget()
    if not configured:
        widget._flash("Offline mode — add a Groq API key to enable AI")

    # Let a second launch / `main.py --wake` wake this instance instead of
    # spawning a duplicate tray icon. The socket thread only emits a signal, so
    # Qt runs the real work on the GUI thread.
    import instance
    server = instance.WakeServer(widget.hotkey_pressed.emit)
    server.start()
    widget.wake_server = server

    widget.show()
    try:
        return app.exec_()
    finally:
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
