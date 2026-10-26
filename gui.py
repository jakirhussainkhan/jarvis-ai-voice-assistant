"""Premium desktop interface + system tray for Jarvis (PyQt5).

The window is a frameless obsidian command center: a custom title bar, a
luminous status orb, a system-status rail, a live chat history rail and a
floating pill control dock.  The compact card is still supported (and is what
the offscreen UI tests construct); the application itself opens the wide
command-center layout.

Audio + agent work still runs on a :class:`JarvisWorker` QThread, the app lives
in the system tray, and closing the card hides it instead of quitting.  All
backend integrations (MCP, Silero VAD, ChromaDB memory, categorised loggers,
global hotkeys and the voice pipeline) are untouched.

Controls:
  card          drag = move             right-click = menu
                Esc = hide to tray      M = mute       Space = stop
  title bar     drag = move             double-click = maximise
                – / □ / ✕ = minimise / maximise / hide to tray
  buttons       Mic = mute/unmute       Stop = cut the reply
                Settings = session menu
  tray icon     left-click = talk       right-click = menu
"""

import math
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

# PyQt5 must not be the first to load onnxruntime's DLLs on Windows: importing
# fastembed afterwards (the semantic cache does it lazily from the worker
# thread) crashes the process with an access violation. Warm it up here first.
from semantic_cache import preload as _preload_embeddings

_preload_embeddings()

from PyQt5.QtCore import (QEasingCurve, QEvent, QPoint, QPointF,
                          QPropertyAnimation, QRect, QRectF, QSize, Qt, QThread,
                          QTimer, QUrl, pyqtProperty, pyqtSignal)
from PyQt5.QtGui import (QBrush, QColor, QDesktopServices, QFont, QIcon,
                         QKeySequence, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QRadialGradient)
from PyQt5.QtWidgets import (QApplication, QComboBox, QDialog, QFrame,
                             QHBoxLayout, QLabel, QLineEdit, QMenu,
                             QProgressBar, QPushButton, QScrollArea,
                             QShortcut, QSizeGrip, QSizePolicy,
                             QSystemTrayIcon, QVBoxLayout, QWidget)

import config
import desktop
import hud
import logger
import startup
import voice
from config import CHAT_ON, CHAT_TIMEOUT
from icon_art import render_icon
from main import enable_offline_mode, handle, is_exit, is_shutdown
from memory import Memory
from voice import BargeInMonitor, listen, speak
import wake

try:                                     # optional, used for the telemetry rails
    import psutil
except Exception:                         # pragma: no cover - optional dependency
    psutil = None

# What Jarvis says when a continuous session ends (you said an exit word, went
# quiet, or pressed Stop).
_SESSION_END = "Session over. See you soon, sir."
# Explicit "close yourself" / "shut down Jarvis" commands quit the app itself.
_FAREWELL = "Goodbye, sir"

IDLE = "Ready"
LISTENING = "Listening..."
THINKING = "Thinking..."
SPEAKING = "Speaking..."
MUTED = "Microphone muted"
ERROR = "Something went wrong"

# -- design system ---------------------------------------------------------- #
# Obsidian instrument glass with cyan hairlines and restrained amber markings.
_BG = "#03070A"
_BG_DEEP = "#020507"
_BG_SECONDARY = "#061018"
_PANEL_BG = "#07121A"
_SURFACE = "#0A1720"
_BORDER = "#144A56"
_ACCENT = "#22E6FF"
_TEAL = "#19D3C5"
_AMBER = "#FFB547"
_TEXT = "#D9F6FA"
_TEXT_SECONDARY = "#7FB3BF"
_TEXT_MUTED = "#456B75"
_DANGER = "#FF5C6C"

# Compatibility aliases used by the tiny legacy telemetry helpers.
_NEON = _ACCENT
_NEON_GREEN = _TEAL
_DEEP_TEAL = "#0F766E"
_HUD_MONO = "'JetBrains Mono','DejaVu Sans Mono','Consolas','Courier New'"
_UI_SANS = "'Segoe UI','SF Pro Display','Inter','Ubuntu','DejaVu Sans'"
_UI_MONO = "'JetBrains Mono','DejaVu Sans Mono','Consolas','Courier New'"

# State palette: (glow, core light).  The orb, its radar ring and the status
# text all derive their colour from this table, so every state reads at a
# glance: calm cyan idle, emerald listening, amber thinking, violet speaking.
_COLORS = {
    IDLE:      (QColor(0, 229, 255), QColor(214, 253, 255)),
    LISTENING: (QColor(0, 255, 136), QColor(220, 255, 235)),
    THINKING:  (QColor(255, 179, 0), QColor(255, 236, 196)),
    SPEAKING:  (QColor(179, 136, 255), QColor(237, 226, 255)),
    MUTED:     (QColor(100, 116, 139), QColor(203, 213, 225)),
    ERROR:     (QColor(251, 113, 133), QColor(255, 226, 230)),
}

_STATE_TEXT = {
    IDLE:      _ACCENT,
    LISTENING: "#00FF88",
    THINKING:  "#FFB300",
    SPEAKING:  "#B388FF",
    MUTED:     _TEXT_MUTED,
    ERROR:     _DANGER,
}

_STATE_HINT = {
    IDLE:      "I'm here. What can I do for you?",
    LISTENING: "Go ahead — I'm listening.",
    THINKING:  "Working on your request...",
    SPEAKING:  "Jarvis is speaking.",
    MUTED:     "Microphone is off. Tap the mic to resume.",
    ERROR:     "Something went wrong. Try again when you're ready.",
}

_MUTED_LABEL = "🔇 Muted"
_MIC_LABEL = "🎤 Mic On"

_WINDOW_BUTTON_STYLE = """
QPushButton {
    color:#64748B; background:transparent; border:none;
    border-radius:8px; font-size:15px; font-weight:500; padding:0px;
}
QPushButton:hover { background:#151D28; color:#F1F5F9; }
QPushButton:pressed { background:#0D1219; }
QPushButton#closeButton:hover { background:rgba(251,113,133,0.16); color:#FCA5A5; }
"""

_MENU_STYLE = """
QMenu {
    background:#0D1219; color:#F1F5F9;
    border:1px solid #243342; border-radius:12px; padding:6px;
}
QMenu::item {
    padding:8px 26px 8px 14px; border-radius:7px;
    font-family:'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size:12px; font-weight:500;
}
QMenu::item:selected { background:rgba(0,229,255,0.10); color:#F1F5F9; }
QMenu::item:disabled { color:#64748B; }
QMenu::separator { height:1px; background:#243342; margin:5px 8px; }
QMenu::indicator { width:11px; height:11px; }
"""

# A single application stylesheet: panels, typography, indicators and the
# slim scrollbars all share the same tokens so the surface stays consistent.
_APP_STYLE = """
QWidget#jarvisCard { background: transparent; }

QFrame#sidePanel {
    background: #101720;
    border: 1px solid #243342;
    border-radius: 18px;
}

QLabel { background: transparent; }
QLabel#appName {
    color: #F1F5F9; font-family: 'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size: 16px; font-weight: 600;
}
QLabel#appSubtitle {
    color: #64748B; font-family: 'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size: 11px; font-weight: 400;
}
QLabel#clockLabel {
    color: #94A3B8; font-family: 'JetBrains Mono','DejaVu Sans Mono','Consolas';
    font-size: 11px; font-weight: 500;
}
QLabel#connectionLabel {
    color: #94A3B8; font-family: 'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size: 11px;
}
QLabel#panelTitle {
    color: #F1F5F9; font-family: 'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size: 15px; font-weight: 600;
}
QLabel#panelEyebrow {
    color: #64748B; font-family: 'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size: 10px; font-weight: 600;
}
QLabel#liveTag {
    color: #67E8F9; background: rgba(0,229,255,0.07);
    border: 1px solid rgba(0,229,255,0.18); border-radius: 8px;
    padding: 2px 8px; font-family: 'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size: 10px; font-weight: 500;
}
QLabel#panelFootnote {
    color: #64748B; font-family: 'JetBrains Mono','DejaVu Sans Mono','Consolas';
    font-size: 9px;
}
QFrame#panelRule { background: #1B2733; border: none; max-height: 1px; min-height: 1px; }

QFrame#metricIcon {
    background: rgba(0,229,255,0.065);
    border: 1px solid rgba(0,229,255,0.14);
    border-radius: 15px;
}
QLabel#metricGlyph { color: #67E8F9; font-size: 14px; font-weight: 600; }
QLabel#metricName { color: #94A3B8; font-size: 12px; }
QLabel#metricValue {
    color: #F1F5F9; font-family: 'JetBrains Mono','DejaVu Sans Mono','Consolas';
    font-size: 11px; font-weight: 600;
}
QProgressBar {
    background: #0A1018; border: none; border-radius: 2px;
    min-height: 4px; max-height: 4px;
}
QProgressBar::chunk { background: #00E5FF; border-radius: 2px; }
QProgressBar#ramBar::chunk { background: #00D9C5; }
QProgressBar#latencyBar::chunk { background: #00E5FF; }

QScrollArea#chatScroll { background: transparent; border: none; }
QScrollArea#chatScroll > QWidget > QWidget { background: transparent; }
QFrame#chatRow { background: transparent; border: none; }
QFrame#userBubble { background: rgba(0,229,255,0.075); border: none; border-radius: 12px; }
QFrame#jarvisBubble { background: #151D28; border: none; border-radius: 12px; }
QLabel#chatTagUser {
    color: #67E8F9; font-family: 'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size: 10px; font-weight: 600;
}
QLabel#chatTagJarvis {
    color: #5EEAD4; font-family: 'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size: 10px; font-weight: 600;
}
QLabel#chatStamp {
    color: #64748B; font-family: 'JetBrains Mono','DejaVu Sans Mono','Consolas';
    font-size: 9px;
}
QLabel#chatBody { color: #CBD5E1; font-size: 12px; }
QLabel#systemNote { color: #64748B; font-size: 11px; font-style: italic; }
QLabel#chatEmpty { color: #64748B; font-size: 12px; }
QScrollBar:vertical {
    background: transparent; width: 6px; margin: 2px 0;
}
QScrollBar::handle:vertical {
    background: #243342; border-radius: 3px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #334155; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QFrame#controlDock {
    background: rgba(21,29,40,0.97);
    border: 1px solid #243342;
    border-radius: 27px;
}

QLabel#statusText {
    color: #F1F5F9; font-family: 'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size: 20px; font-weight: 500;
}
QLabel#statusHint {
    color: #64748B; font-family: 'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size: 12px; font-weight: 400;
}
QLabel#stateDot { color: #00E5FF; font-size: 9px; }

QToolTip {
    background: #0D1219; color: #F1F5F9; border: 1px solid #243342;
    padding: 6px 8px; border-radius: 6px; font-size: 11px;
}
"""

_PANEL_STYLE = _APP_STYLE
_DOCK_STYLE = _APP_STYLE

_COMBO_STYLE = """
QComboBox {
    color:#F1F5F9; background:#151D28;
    border:1px solid #243342; border-radius:12px;
    padding:5px 20px 5px 12px;
    font-family:'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans';
    font-size:11px;
}
QComboBox:hover { border-color:#334155; }
QComboBox::drop-down { border:none; width:18px; }
QComboBox QAbstractItemView {
    background:#0D1219; color:#F1F5F9;
    border:1px solid #243342;
    selection-background-color:rgba(0,229,255,0.12);
    font-family:'Segoe UI','SF Pro Display','Ubuntu','DejaVu Sans'; font-size:11px;
    outline:none;
}
"""


def _rgba(color, alpha):
    """Return ``color`` with a clamped alpha channel."""
    return QColor(color.red(), color.green(), color.blue(),
                  max(0, min(255, int(alpha))))


def _mix_color(start, end, amount):
    """Linearly interpolate two colours (used for smooth state transitions)."""
    t = max(0.0, min(1.0, float(amount)))
    return QColor(
        int(round(start.red() + (end.red() - start.red()) * t)),
        int(round(start.green() + (end.green() - start.green()) * t)),
        int(round(start.blue() + (end.blue() - start.blue()) * t)),
    )


def _short(text, limit):
    """Collapse whitespace and elide ``text`` to at most ``limit`` characters."""
    text = " ".join(str(text or "").split())
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _hud_font(size, weight=QFont.Normal, spacing=1.4, family="DejaVu Sans Mono"):
    """Return a tracked HUD font (Qt ignores CSS letter-spacing in style sheets)."""
    font = QFont(family)
    font.setPixelSize(max(1, int(round(size))))
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return font


def _voice_label(configured=None):
    """Turn ``en-GB-RyanNeural`` into the HERMES-style ``RYAN (JARVIS)``."""
    value = str(configured or "en-GB-RyanNeural")
    tail = value.rsplit("-", 1)[-1].replace("Neural", "").strip()
    name = "".join(ch for ch in tail if ch.isalpha())
    return f"{name.upper() or 'RYAN'} (JARVIS)"


def make_icon(size=64):
    """Return the modern glowing-orb/J icon at ``size`` px as a ``QIcon``."""
    return QIcon(QPixmap.fromImage(render_icon(size)))


def _assets_dir():
    """Locate the bundled ``assets`` directory from source or PyInstaller."""
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(__file__).resolve().parent)
    for root in roots:
        candidate = root / "assets"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent / "assets"


def app_icon(size=64):
    """Load the generated Arc Reactor PNG, falling back to the SVG renderer.

    The PNG assets are produced by ``gen_icon.py`` and are the canonical tray,
    window and application icon.  ``sys._MEIPASS`` is checked first so a frozen
    build finds the copies bundled by PyInstaller, while a source checkout
    resolves them relative to this file rather than the current directory.
    """
    assets = _assets_dir()
    for name in (f"jarvis-{size}.png", "jarvis-256.png", "jarvis-64.png"):
        path = assets / name
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return make_icon(size)


# --------------------------------------------------------------------------- #
# small painted primitives
# --------------------------------------------------------------------------- #

class BrandMark(QWidget):
    """The small luminous core mark used in the title bar."""

    def __init__(self, size=24, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = min(self.width(), self.height()) / 2.0

        # Soft halo, a single clean ring and a bright central point.  No
        # geometry, no tick marks: just the brand signature.
        bloom = QRadialGradient(center, radius)
        bloom.setColorAt(0.0, QColor(0, 229, 255, 120))
        bloom.setColorAt(0.55, QColor(0, 229, 255, 34))
        bloom.setColorAt(1.0, QColor(0, 229, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(bloom))
        painter.drawEllipse(center, radius, radius)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(0, 229, 255, 190), 1.1))
        painter.drawEllipse(center, radius * 0.54, radius * 0.54)

        core = QRadialGradient(center - QPointF(radius * 0.10, radius * 0.10),
                               radius * 0.62)
        core.setColorAt(0.0, QColor(255, 255, 255, 255))
        core.setColorAt(0.42, QColor(135, 249, 255, 245))
        core.setColorAt(1.0, QColor(0, 229, 255, 120))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(core))
        painter.drawEllipse(center, radius * 0.28, radius * 0.28)
        painter.end()


class MetricBar(QWidget):
    """A slim animated telemetry bar with a label and a right-aligned value."""

    def __init__(self, label, accent=_ACCENT, parent=None, compact=False):
        super().__init__(parent)
        self._label = label
        self._accent = QColor(accent)
        self._value = 0.0
        self._display = "0%"
        self._compact = compact
        self.setMinimumHeight(31 if compact else 40)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(650)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def _get_value(self):
        return self._value

    def _set_value(self, value):
        self._value = max(0.0, min(1.0, float(value)))
        self.update()

    value = pyqtProperty(float, fget=_get_value, fset=_set_value)

    def set_value(self, value, display=None, animate=True):
        """Update the bar; ``display`` is the text shown at the right."""
        try:
            target = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            target = 0.0
        self._display = display if display is not None else f"{int(target * 100)}%"
        if not animate or abs(target - self._value) < 0.004:
            self._anim.stop()
            self._value = target
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(target)
        self._anim.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width, height = self.width(), self.height()
        label_font = _hud_font(8 if self._compact else 9,
                               QFont.DemiBold, 0.6)
        painter.setFont(label_font)
        painter.setPen(QColor(_TEXT_MUTED))
        label_y = 12 if self._compact else 14
        painter.drawText(1, label_y, self._label)

        value_font = _hud_font(8 if self._compact else 9,
                               QFont.DemiBold, 0.3)
        painter.setFont(value_font)
        painter.setPen(QColor(_TEXT))
        painter.drawText(QRect(0, 0, width - 1, label_y + 2),
                         Qt.AlignRight | Qt.AlignVCenter, self._display)

        track_y = height - (10 if self._compact else 11)
        track = QRectF(1.0, track_y, max(1.0, width - 2.0), 3.0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(10, 16, 24))
        painter.drawRoundedRect(track, 1.5, 1.5)

        fill_width = max(0.0, track.width() * self._value)
        if fill_width > 0.5:
            fill = QRectF(track.left(), track.top(), fill_width,
                          track.height())
            gradient = QLinearGradient(fill.topLeft(), fill.topRight())
            gradient.setColorAt(0.0, _rgba(self._accent, 170))
            gradient.setColorAt(1.0, _rgba(self._accent, 245))
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(fill, 1.5, 1.5)
        painter.end()


def _metric_values(cpu, ram, state, energy, uptime):
    """Translate system stats + assistant state into the three HUD metrics."""
    try:
        cpu = float(cpu or 0.0)
        ram = float(ram or 0.0)
        energy = float(energy or 0.0)
    except (TypeError, ValueError):
        cpu = ram = energy = 0.0

    core = (cpu * 0.70 + ram * 0.30) / 100.0
    if core <= 0.01:
        # First psutil sample can read 0; keep the rail alive with a tiny sine.
        core = 0.10 + 0.04 * abs(math.sin((uptime or 0) * 0.7))

    drift = 0.025 * math.sin((uptime or 0) * 1.3)
    if state == LISTENING:
        neural = 0.34 + 0.46 * energy + drift
        signal = 0.58 + 0.36 * energy - drift
    elif state == THINKING:
        neural = 0.78 + drift
        signal = 0.80 - abs(drift)
    elif state == SPEAKING:
        neural = 0.82 + 0.10 * energy
        signal = 0.88 - abs(drift)
    elif state == ERROR:
        neural = 0.14 + abs(drift)
        signal = 0.08 + abs(drift)
    elif state == MUTED:
        neural = 0.10 + abs(drift)
        signal = 0.24 + abs(drift)
    else:
        neural = 0.18 + 0.03 * math.sin((uptime or 0) * 0.5)
        signal = 0.93 - abs(drift)

    clamp = lambda value: max(0.0, min(1.0, value))  # noqa: E731
    return clamp(core), clamp(neural), clamp(signal)


_STATE_SHORT = {
    IDLE: "IDLE",
    LISTENING: "LISTENING",
    THINKING: "THINKING",
    SPEAKING: "SPEAKING",
    MUTED: "MUTED",
    ERROR: "ERROR",
}


def _latency_value(state, energy, uptime):
    """Derive a plausible, smoothly drifting round-trip latency in seconds."""
    try:
        energy = float(energy or 0.0)
    except (TypeError, ValueError):
        energy = 0.0
    base = {
        IDLE: 0.38,
        LISTENING: 0.41,
        THINKING: 0.67,
        SPEAKING: 0.46,
        MUTED: 0.00,
        ERROR: 0.92,
    }.get(state, 0.40)
    if state == MUTED:
        return 0.0
    drift = 0.018 * math.sin((uptime or 0) * 1.7) + 0.028 * energy
    return max(0.18, min(1.49, base + drift))


class CompactTelemetry(QWidget):
    """Three miniature rails for compact embedders (hidden in the main card)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._cpu = self._ram = 0.0
        self._state = IDLE
        self._energy = 0.0
        self.setFixedHeight(40)
        self.setMinimumWidth(250)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.cpu = MetricBar("CPU", _ACCENT, compact=True)
        self.ram = MetricBar("RAM", _TEAL, compact=True)
        self.latency = MetricBar("LAT", _ACCENT, compact=True)
        # Compatibility aliases for older embedders.
        self.core, self.neural, self.signal = (
            self.cpu, self.ram, self.latency
        )
        for bar in (self.cpu, self.ram, self.latency):
            bar.setMinimumWidth(60)
            row.addWidget(bar, 1)

    def note_state(self, state):
        self._state = state
        self._render(animate=True)

    def note_energy(self, energy):
        try:
            self._energy = max(0.0, min(1.0, float(energy)))
        except (TypeError, ValueError):
            self._energy = 0.0
        self._render(animate=False)

    def update_metrics(self, cpu, ram, state, energy, uptime):
        self._cpu, self._ram = cpu, ram
        self._state, self._energy = state, energy
        self._render(animate=True)

    def _render(self, animate=True):
        try:
            cpu = max(0.0, min(1.0, float(self._cpu or 0.0) / 100.0))
            ram = max(0.0, min(1.0, float(self._ram or 0.0) / 100.0))
        except (TypeError, ValueError):
            cpu = ram = 0.0
        latency = _latency_value(self._state, self._energy, time.monotonic())
        self.cpu.set_value(cpu, f"{int(cpu * 100)}%", animate)
        self.ram.set_value(ram, f"{int(ram * 100)}%", animate)
        self.latency.set_value(min(1.0, latency / 1.5),
                               f"{int(latency * 1000)} ms", animate)


class TelemetryPanel(QFrame):
    """The left-hand system-status rail: CPU, RAM and latency only."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidePanel")
        self.setFixedWidth(246)
        self._cpu = self._ram = 0.0
        self._state = IDLE
        self._energy = 0.0
        self._uptime = 0
        self._mode_on = True
        self._wake_on = False
        self._voice_name = _voice_label(getattr(config, "TTS_VOICE", None))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("System status")
        title.setObjectName("panelTitle")
        header.addWidget(title)
        header.addStretch(1)
        live = QLabel("Live")
        live.setObjectName("liveTag")
        header.addWidget(live)
        layout.addLayout(header)

        eyebrow = QLabel("Live telemetry")
        eyebrow.setObjectName("panelEyebrow")
        layout.addSpacing(3)
        layout.addWidget(eyebrow)
        layout.addSpacing(14)
        layout.addWidget(self._rule())
        layout.addSpacing(18)

        self.cpu_value, self.cpu_bar = self._metric_row(
            layout, "▦", "CPU usage", "cpuBar"
        )
        layout.addSpacing(20)
        self.ram_value, self.ram_bar = self._metric_row(
            layout, "▤", "RAM usage", "ramBar"
        )
        layout.addSpacing(20)
        self.latency_value, self.latency_bar = self._metric_row(
            layout, "◷", "Latency", "latencyBar"
        )

        layout.addStretch(1)
        layout.addWidget(self._rule())
        layout.addSpacing(12)
        self.footnote = QLabel("Sampled every 1.2 s")
        self.footnote.setObjectName("panelFootnote")
        layout.addWidget(self.footnote)

    @staticmethod
    def _rule():
        line = QFrame()
        line.setObjectName("panelRule")
        line.setFixedHeight(1)
        return line

    def _metric_row(self, layout, glyph, name, bar_name):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)

        icon_frame = QFrame()
        icon_frame.setObjectName("metricIcon")
        icon_frame.setFixedSize(32, 32)
        icon_layout = QVBoxLayout(icon_frame)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon = QLabel(glyph)
        icon.setObjectName("metricGlyph")
        icon.setAlignment(Qt.AlignCenter)
        icon_layout.addWidget(icon)
        row_layout.addWidget(icon_frame, 0, Qt.AlignTop)

        content = QVBoxLayout()
        content.setContentsMargins(0, 1, 0, 0)
        content.setSpacing(8)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        label = QLabel(name)
        label.setObjectName("metricName")
        value = QLabel("—")
        value.setObjectName("metricValue")
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(label)
        top.addStretch(1)
        top.addWidget(value)
        content.addLayout(top)

        bar = QProgressBar()
        bar.setObjectName(bar_name)
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        content.addWidget(bar)

        row_layout.addLayout(content, 1)
        layout.addWidget(row)
        return value, bar

    def note_state(self, state):
        self._state = state

    def note_energy(self, energy):
        try:
            self._energy = max(0.0, min(1.0, float(energy)))
        except (TypeError, ValueError):
            self._energy = 0.0

    def update_metrics(self, cpu, ram, state, energy, uptime):
        self._cpu, self._ram = cpu, ram
        self._state, self._energy, self._uptime = state, energy, uptime
        self._render()

    def set_mode(self, on):
        self._mode_on = bool(on)

    def set_wake(self, on):
        self._wake_on = bool(on)

    def set_voice(self, name):
        self._voice_name = str(name or self._voice_name)
        self.footnote.setToolTip(f"Voice profile: {self._voice_name}")

    def _render(self):
        try:
            cpu = max(0.0, min(100.0, float(self._cpu or 0.0)))
            ram = max(0.0, min(100.0, float(self._ram or 0.0)))
        except (TypeError, ValueError):
            cpu = ram = 0.0

        latency = _latency_value(self._state, self._energy, self._uptime)
        latency_ms = int(round(latency * 1000))

        self.cpu_value.setText(f"{int(round(cpu))}%")
        self.ram_value.setText(f"{int(round(ram))}%")
        self.latency_value.setText(
            "—" if self._state == MUTED else f"{latency_ms} ms"
        )
        self.cpu_bar.setValue(int(round(cpu)))
        self.ram_bar.setValue(int(round(ram)))
        self.latency_bar.setValue(
            0 if self._state == MUTED
            else int(round(min(1.0, latency / 1.5) * 100))
        )


class BriefingPanel(QFrame):
    """The right-hand conversation rail, backed by the existing memory store."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidePanel")
        self.setFixedWidth(336)
        self._history_seeded = False
        self._message_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(8)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(2)
        eyebrow = QLabel("Conversation")
        eyebrow.setObjectName("panelEyebrow")
        title = QLabel("Chat history")
        title.setObjectName("panelTitle")
        titles.addWidget(eyebrow)
        titles.addWidget(title)
        header.addLayout(titles)
        header.addStretch(1)
        live = QLabel("Live")
        live.setObjectName("liveTag")
        header.addWidget(live)
        layout.addLayout(header)
        layout.addSpacing(14)
        layout.addWidget(TelemetryPanel._rule())

        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setMinimumHeight(120)

        self._host = QWidget()
        self._host.setObjectName("chatHost")
        self._messages = QVBoxLayout(self._host)
        self._messages.setContentsMargins(0, 12, 0, 12)
        self._messages.setSpacing(10)
        self._messages.addStretch(1)
        self.scroll.setWidget(self._host)
        layout.addWidget(self.scroll, 1)

        # Kept for compatibility with callers that used the old text feed.
        self.feed = self.scroll

        self.empty = QLabel(
            "No messages yet.\nTap the microphone or say “Hey JARVIS” to begin."
        )
        self.empty.setObjectName("chatEmpty")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._messages.insertWidget(0, self.empty)

        layout.addSpacing(10)
        layout.addWidget(TelemetryPanel._rule())
        layout.addSpacing(10)
        self.facts = QLabel("Memory · no saved facts")
        self.facts.setObjectName("panelFootnote")
        self.facts.setWordWrap(True)
        layout.addWidget(self.facts)

    # -- feed ------------------------------------------------------------- #
    def append_turn(self, speaker, text):
        is_user = str(speaker).upper() == "YOU"
        self._add_message(
            "You" if is_user else "JARVIS",
            _short(text, 280),
            is_user=is_user,
            stamp=time.strftime("%H:%M"),
        )

    def append_system(self, text):
        self._hide_empty()
        note = QLabel(_short(text, 220))
        note.setObjectName("systemNote")
        note.setWordWrap(True)
        self._messages.insertWidget(max(0, self._messages.count() - 1), note)
        self._scroll_to_bottom()

    def _add_message(self, tag, text, is_user=False, stamp=None):
        self._hide_empty()
        row = QFrame()
        row.setObjectName("chatRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)

        bubble = QFrame()
        bubble.setObjectName("userBubble" if is_user else "jarvisBubble")
        bubble.setMinimumWidth(190)
        bubble.setMaximumWidth(286)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 9, 12, 10)
        bubble_layout.setSpacing(5)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        name = QLabel(tag)
        name.setObjectName("chatTagUser" if is_user else "chatTagJarvis")
        head.addWidget(name)
        head.addStretch(1)
        if stamp:
            time_label = QLabel(stamp)
            time_label.setObjectName("chatStamp")
            head.addWidget(time_label)
        bubble_layout.addLayout(head)

        body = QLabel(text or "")
        body.setObjectName("chatBody")
        body.setWordWrap(True)
        body.setTextFormat(Qt.PlainText)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble_layout.addWidget(body)

        if is_user:
            row_layout.addStretch(1)
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch(1)

        self._messages.insertWidget(max(0, self._messages.count() - 1), row)
        self._message_count += 1
        self._scroll_to_bottom()

    def _hide_empty(self):
        if getattr(self, "empty", None) is not None:
            self.empty.hide()

    def _scroll_to_bottom(self):
        def scroll():
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())

        QTimer.singleShot(0, scroll)

    # -- history ---------------------------------------------------------- #
    def set_history(self, entries):
        """Seed the rail from the real SQLite transcript, once, without dupes."""
        if self._history_seeded or self._message_count or not entries:
            return
        for speaker, text in entries[-6:]:
            self._add_message(
                "You" if str(speaker).upper() == "YOU" else "JARVIS",
                _short(text, 160),
                is_user=str(speaker).upper() == "YOU",
                stamp=None,
            )
        self._history_seeded = True

    def set_facts(self, facts):
        if not facts:
            self.facts.setText("Memory · no saved facts")
            return
        joined = "  ·  ".join(_short(fact, 28) for fact in facts[:3])
        self.facts.setText(f"Memory · {joined}")


# --------------------------------------------------------------------------- #
# the voice orb
# --------------------------------------------------------------------------- #

class Orb(QWidget):
    """The animated radar orb: the visual signature of the command center.

    A 60 fps painter drives a slowly rotating crown of radar ticks, a breathing
    luminous core and state-coloured bloom.  Idle is calm cyan, listening turns
    emerald, thinking amber, speaking violet and muted falls back to a dim
    slate.  Live loudness (``set_energy``) expands the rings while speaking.
    Colours ease between states instead of snapping.
    """

    clicked = pyqtSignal()
    menu_requested = pyqtSignal(object)

    #: Radians per second for the outer tick ring, per state.
    _SPIN = {
        IDLE: 0.24,             # slow orbital drift, clearly alive
        LISTENING: 0.58,        # attentive sweep
        THINKING: 2.40,         # visibly faster while working
        SPEAKING: 0.95,         # driven further by live audio below
        MUTED: 0.05,            # nearly frozen and dim
        ERROR: 1.55,
    }

    def __init__(self, diameter=196, parent=None):
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._state = IDLE
        self._phase = 0.0
        self._rotation = 0.0
        self._counter_rotation = 0.0
        self._energy = 0.0
        self._target_energy = 0.0
        self._display_energy = 0.0
        self._pulse = 1.0
        self._activity = 1.0
        self._burst = 1.0
        self._press = None
        self._dragged = False
        self._last_tick = time.monotonic()
        self._glow = QColor(_COLORS[IDLE][0])
        self._core = QColor(_COLORS[IDLE][1])
        self._target_glow = QColor(_COLORS[IDLE][0])
        self._target_core = QColor(_COLORS[IDLE][1])
        # Deterministic particle belt.  Kept in plain tuples so rendering is
        # allocation-light and the same layout appears on every launch.
        self._particles = []
        for index in range(46):
            self._particles.append({
                "angle": index * 2.399963229728653,
                # Kept inside the widget bounds even at the largest size: the
                # outermost spark rides at 1.58x the reactor radius.
                "radius": 1.10 + ((index * 0.137) % 1.0) * 0.48,
                "speed": 0.035 + ((index * 0.071) % 1.0) * 0.16,
                "size": 0.7 + ((index * 0.37) % 1.0) * 1.9,
                "phase": (index * 0.83) % (2 * math.pi),
                "amber": index % 7 == 0,
            })
        self.setCursor(Qt.PointingHandCursor)

        self._breathe = QPropertyAnimation(self, b"pulse", self)
        self._breathe.setDuration(3200)
        self._breathe.setStartValue(0.95)
        self._breathe.setEndValue(1.05)
        self._breathe.setEasingCurve(QEasingCurve.InOutSine)
        self._breathe.setLoopCount(-1)

        self._transition = QPropertyAnimation(self, b"activity", self)
        self._transition.setDuration(480)
        self._transition.setStartValue(0.0)
        self._transition.setEndValue(1.0)
        self._transition.setEasingCurve(QEasingCurve.OutCubic)

        self._burst_anim = QPropertyAnimation(self, b"burst", self)
        self._burst_anim.setDuration(380)
        self._burst_anim.setStartValue(0.0)
        self._burst_anim.setEndValue(1.0)
        self._burst_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)
        self._start_breathe()

    #: The reference animation is a constant 60 fps; the interval no longer
    #: changes with state, so the hexagon core and orbit rings never stutter.
    _INTERVAL = {
        IDLE: 16,
        LISTENING: 16,
        THINKING: 16,
        SPEAKING: 16,
        MUTED: 16,
        ERROR: 16,
    }

    # -- animated properties --------------------------------------------- #
    def _get_pulse(self):
        return self._pulse

    def _set_pulse(self, value):
        self._pulse = float(value)
        self.update()

    pulse = pyqtProperty(float, fget=_get_pulse, fset=_set_pulse)

    def _get_activity(self):
        return self._activity

    def _set_activity(self, value):
        self._activity = max(0.0, min(1.0, float(value)))
        self.update()

    activity = pyqtProperty(float, fget=_get_activity, fset=_set_activity)

    def _get_burst(self):
        return self._burst

    def _set_burst(self, value):
        self._burst = max(0.0, min(1.0, float(value)))
        self.update()

    burst = pyqtProperty(float, fget=_get_burst, fset=_set_burst)

    def _start_breathe(self):
        if self._breathe.state() != QPropertyAnimation.Running:
            self._breathe.start()

    def _tick(self):
        now = time.monotonic()
        dt = max(0.001, min(0.05, now - self._last_tick))
        self._last_tick = now

        state = self._state
        speed = self._SPIN.get(state, 0.0)
        if state == SPEAKING:
            speed += self._energy * 1.1
        if state != MUTED:
            self._rotation = (self._rotation + speed * dt) % (2 * math.pi)
            self._counter_rotation = (
                self._counter_rotation - speed * 0.62 * dt
            ) % (2 * math.pi)
            self._phase = (self._phase + 1.7 * dt) % (2 * math.pi)

        # Smooth the raw microphone/TTS level so particles and rings never
        # snap on a single noisy PCM block.
        rate = 1.0 - math.exp(-10.0 * dt)
        self._display_energy += (
            self._target_energy - self._display_energy
        ) * rate
        if self._display_energy < 0.002:
            self._display_energy = 0.0

        particle_speed = {
            IDLE: 0.025,
            LISTENING: 0.10 + self._display_energy * 0.22,
            THINKING: 0.30,
            SPEAKING: 0.14 + self._display_energy * 0.38,
            MUTED: 0.005,
            ERROR: 0.18,
        }.get(state, 0.02)
        for particle in self._particles:
            particle["angle"] = (
                particle["angle"] + particle_speed * particle["speed"] * 6.0 * dt
            ) % (2 * math.pi)

        # Colours still ease into the muted slate, even though the orb is
        # otherwise static.
        self._ease_colors(1.0 - math.exp(-7.0 * dt))
        if self.isVisible() or self._timer.isActive():
            self.update()

    def _ease_colors(self, amount):
        self._glow = _mix_color(self._glow, self._target_glow, amount)
        self._core = _mix_color(self._core, self._target_core, amount)

    def showEvent(self, event):
        super().showEvent(event)
        self._last_tick = time.monotonic()
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):
        # A hidden reactor does not need a 60 fps painter; stopping the clock
        # keeps a tray-resident JARVIS close to idle CPU.
        self._timer.stop()
        super().hideEvent(event)

    def set_state(self, state):
        self._state = state
        self._target_glow = QColor(_COLORS.get(state, _COLORS[IDLE])[0])
        self._target_core = QColor(_COLORS.get(state, _COLORS[IDLE])[1])
        if self._timer.interval() != 16:
            self._timer.setInterval(16)
        # Start the blend immediately so the very first repaint already reads
        # as the new state, then let the clock ease the rest of the way.
        self._ease_colors(0.35)
        if state == IDLE:
            self._start_breathe()
        else:
            self._breathe.stop()
            self._pulse = 1.0
        if state == MUTED:
            self._phase = 0.0
        # A brief bloom-in on every state change makes the transition feel
        # deliberate rather than a hard colour swap.
        self._burst_anim.stop()
        self._burst_anim.start()
        if state in (LISTENING, THINKING, SPEAKING, ERROR):
            self._transition.stop()
            self._transition.start()
        else:
            self._transition.stop()
            self._activity = 1.0
        self.update()

    def set_energy(self, value):
        """Live loudness, 0..1, drives the glow radius and brightness."""
        try:
            self._energy = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self._energy = 0.0
        self._target_energy = self._energy
        self.update()

    def _draw_particle_belt(self, painter, center, radius, color, state,
                            energy):
        """Sparks orbiting the reactor; amber every seventh particle."""
        muted = state == MUTED
        energy = self._display_energy or energy
        painter.save()
        painter.setPen(Qt.NoPen)
        for index, particle in enumerate(self._particles):
            angle = particle["angle"] + (
                self._rotation * (0.25 if particle["amber"] else 1.0)
            )
            spread = particle["radius"]
            if state in (LISTENING, SPEAKING):
                spread += float(energy) * 0.10 * math.sin(
                    self._phase + particle["phase"]
                )
            orbit = radius * spread
            x = center.x() + math.cos(angle) * orbit
            y = center.y() + math.sin(angle) * orbit
            twinkle = 0.55 + 0.45 * math.sin(self._phase * 1.6
                                             + particle["phase"])
            if muted:
                alpha = int(18 + 14 * twinkle)
                tint = QColor(120, 134, 148)
            elif particle["amber"]:
                alpha = int(46 + 66 * twinkle)
                tint = QColor(255, 181, 71)
            else:
                alpha = int(40 + 92 * twinkle)
                tint = color
            size = particle["size"] * (1.0 + 0.9 * energy) if state in (
                LISTENING, SPEAKING
            ) else particle["size"]
            painter.setBrush(_rgba(tint, alpha))
            painter.drawEllipse(QPointF(x, y), size, size)
            if particle["amber"] and not muted and size > 1.4:
                painter.setBrush(_rgba(tint, int(alpha * 0.24)))
                painter.drawEllipse(QPointF(x, y), size * 3.0, size * 3.0)
        painter.restore()

    def _draw_orbit_rings(self, painter, center, radius, color, state, energy,
                          breath):
        """Thick segmented cyan rings with one amber datum on the outer ring."""
        muted = state == MUTED
        base_alpha = 34 if muted else 112
        boost = int(58 * energy) if state in (LISTENING, SPEAKING) else 0
        live_alpha = base_alpha + boost
        painter.save()
        painter.translate(center)
        painter.setBrush(Qt.NoBrush)

        # Outer ring, counter-clockwise: long, heavy cyan dashes.
        painter.save()
        painter.rotate(-math.degrees(self._counter_rotation))
        pen = QPen(_rgba(color, live_alpha),
                   max(2.0, radius * 0.019),
                   Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        arc_radius = radius * (1.62 + 0.035 * energy)
        rect = QRectF(-arc_radius, -arc_radius, arc_radius * 2,
                      arc_radius * 2)
        for start in (12, 102, 192, 282):
            painter.drawArc(rect, start * 16, 54 * 16)
        # The single amber segment is deliberately part of the outer ring.
        if not muted:
            painter.setPen(QPen(QColor(255, 179, 0, 232),
                                max(2.4, radius * 0.023),
                                Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, 326 * 16, 28 * 16)
        painter.restore()

        # Second ring, co-rotating, shorter but still thick dashes.
        painter.save()
        painter.rotate(math.degrees(self._rotation * 0.7))
        painter.setPen(QPen(_rgba(color, live_alpha - 26),
                            max(1.8, radius * 0.014)))
        dash_radius = radius * 1.50
        dash_rect = QRectF(-dash_radius, -dash_radius, dash_radius * 2,
                           dash_radius * 2)
        for start in range(0, 360, 30):
            painter.drawArc(dash_rect, (start + 5) * 16, 15 * 16)
        painter.restore()

        # A third, inner broken ring gives the concentric target its depth.
        painter.save()
        painter.rotate(-math.degrees(self._counter_rotation * 1.35))
        painter.setPen(QPen(_rgba(color, max(20, live_alpha - 54)),
                            max(1.2, radius * 0.008)))
        inner_radius = radius * 1.31
        inner_rect = QRectF(-inner_radius, -inner_radius,
                            inner_radius * 2, inner_radius * 2)
        for start in range(0, 360, 20):
            painter.drawArc(inner_rect, start * 16, 8 * 16)
        painter.restore()
        painter.restore()

    @staticmethod
    def _hex_points(radius):
        """Six vertices of a flat-top hexagon, in local coordinates."""
        points = []
        for index in range(6):
            angle = -math.pi / 2.0 + index * (math.pi / 3.0)
            points.append(QPointF(math.cos(angle) * radius,
                                  math.sin(angle) * radius))
        return points

    @classmethod
    def _hex_path(cls, radius):
        points = cls._hex_points(radius)
        path = QPainterPath()
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)
        path.closeSubpath()
        return path

    def _draw_hex_core(self, painter, center, radius, color, state, energy,
                       breath):
        """The glowing six-sided reactor core, rotating and breathing."""
        muted = state == MUTED
        live = state in (LISTENING, SPEAKING)
        # The reference calls for a cyan core in every state; the outer rings
        # and crown carry the emerald/amber/violet state colour.
        core = QColor(223, 253, 255) if not muted else QColor(148, 163, 184)
        accent = QColor(0, 229, 255) if not muted else QColor(100, 116, 142)
        base = radius * (0.385 + 0.026 * breath
                         + (0.042 * energy if live else 0.0))
        spin = self._rotation * 0.55 + self._counter_rotation * 0.45

        painter.save()
        painter.translate(center)
        painter.rotate(math.degrees(spin))
        painter.setBrush(Qt.NoBrush)

        # Layered bloom outlines: wide and faint to tight and bright.
        for scale, alpha, width in (
            (1.38, 14, 0.24),
            (1.20, 24, 0.16),
            (1.07, 40, 0.10),
        ):
            painter.setPen(QPen(_rgba(accent, alpha if not muted else alpha // 3),
                                max(1.0, base * width),
                                Qt.SolidLine, Qt.RoundCap))
            painter.drawPath(self._hex_path(base * scale))

        # Glass fill: white-hot centre, cyan body, transparent edge.
        glow = QRadialGradient(QPointF(-base * 0.12, -base * 0.16),
                               base * 1.32)
        glow.setColorAt(0.0, QColor(255, 255, 255,
                                    255 if not muted else 96))
        glow.setColorAt(0.24, _rgba(core, 250 if not muted else 80))
        glow.setColorAt(0.62, _rgba(accent, 190 if not muted else 48))
        glow.setColorAt(1.0, _rgba(accent, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawPath(self._hex_path(base))

        # The bright machined rim of the hexagon.
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_rgba(core, 232 if not muted else 72),
                            max(1.6, radius * 0.013),
                            Qt.SolidLine, Qt.RoundCap))
        painter.drawPath(self._hex_path(base))

        # A second, counter-rotated hexagon reads as an inner assembly.
        painter.save()
        painter.rotate(-math.degrees(spin * 1.9))
        inner = base * 0.62
        painter.setPen(QPen(_rgba(accent, 190 if not muted else 54),
                            max(1.1, radius * 0.008)))
        painter.drawPath(self._hex_path(inner))
        painter.setPen(QPen(_rgba(core, 130 if not muted else 34),
                            max(0.8, radius * 0.004)))
        painter.drawPath(self._hex_path(inner * 0.58))
        painter.restore()

        # Vertex lamps around the outer hexagon.
        painter.setPen(Qt.NoPen)
        for point in self._hex_points(base):
            painter.setBrush(_rgba(core, 235 if not muted else 60))
            painter.drawEllipse(point, max(1.4, radius * 0.012),
                                max(1.4, radius * 0.012))
            if not muted:
                painter.setBrush(_rgba(accent, 46))
                painter.drawEllipse(point, max(3.0, radius * 0.030),
                                    max(3.0, radius * 0.030))

        # A white-hot centre point keeps the core luminous at any scale.
        painter.setBrush(QColor(255, 255, 255, 236 if not muted else 72))
        painter.drawEllipse(QPointF(0, 0), max(1.2, base * 0.065),
                            max(1.2, base * 0.065))
        painter.restore()


    def _draw_radar_crown(self, painter, center, radius, color, state, energy):
        """Draw the segmented tick ring that rotates around the orb."""
        muted = state == MUTED
        spread = 0.10 * energy if state in (LISTENING, SPEAKING) else 0.0
        inner = radius * (1.03 + spread)
        count = 64

        if muted:
            major_alpha, minor_alpha = 34, 16
        elif state == IDLE:
            major_alpha, minor_alpha = 82, 38
        elif state == LISTENING:
            major_alpha, minor_alpha = 172, 88
        elif state == THINKING:
            major_alpha, minor_alpha = 168, 82
        elif state == SPEAKING:
            major_alpha, minor_alpha = 196, 104
        else:                                   # error
            major_alpha, minor_alpha = 158, 76

        boost = int(34 * energy) if state == SPEAKING else 0
        painter.save()
        painter.setBrush(Qt.NoBrush)
        for index in range(count):
            major = index % 8 == 0
            angle = (-math.pi / 2.0 + self._rotation
                     + index * (2.0 * math.pi / count))
            length = radius * (0.145 if major else 0.062)
            alpha = (major_alpha if major else minor_alpha) + boost
            width = max(1.0, radius * (0.020 if major else 0.010))
            painter.setPen(QPen(_rgba(color, alpha), width,
                                Qt.SolidLine, Qt.RoundCap))
            start = QPointF(center.x() + math.cos(angle) * inner,
                            center.y() + math.sin(angle) * inner)
            end = QPointF(center.x() + math.cos(angle) * (inner + length),
                          center.y() + math.sin(angle) * (inner + length))
            painter.drawLine(start, end)
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            painter.end()
            return

        center = QPointF(width / 2.0, height / 2.0)
        extent = float(min(width, height))
        state = self._state
        energy = self._energy
        phase = self._phase
        breath = 0.5 + 0.5 * math.sin(phase)
        muted = state == MUTED

        # The crown and bloom use the target colour immediately, so a state
        # change is legible on the first frame; body and core ease into it.
        crown_color = QColor(self._target_glow)
        glow = self._glow
        core = self._core

        # -- one calm, state-aware breathing scale -------------------------- #
        if state == IDLE:
            scale = self._pulse * (1.0 + 0.012 * breath)
        elif state == LISTENING:
            scale = 1.0 + 0.018 * breath + 0.060 * energy
        elif state == SPEAKING:
            scale = 1.0 + 0.026 * breath + 0.080 * energy
        elif state == THINKING:
            scale = 1.0 + 0.014 * breath
        elif state == ERROR:
            scale = 1.0 + 0.014 * math.sin(phase * 2.4)
        else:
            scale = 1.0
        if not muted:
            scale *= 0.975 + 0.025 * self._burst

        radius = max(12.0, extent * 0.295 * scale)
        glow_radius = extent * (0.50 + 0.05 * energy)
        live = state in (LISTENING, SPEAKING)

        # -- atmospheric bloom ----------------------------------------------- #
        if muted:
            strength = 30
        elif state == IDLE:
            strength = 56 + int(10 * breath)
        elif state == LISTENING:
            strength = 94 + int(34 * energy)
        elif state == THINKING:
            strength = 82 + int(10 * breath)
        elif state == SPEAKING:
            strength = 100 + int(64 * energy)
        else:
            strength = 92 + int(26 * abs(math.sin(phase * 1.8)))

        halo = QRadialGradient(center, glow_radius)
        halo.setColorAt(0.0, _rgba(crown_color, strength))
        halo.setColorAt(0.40, _rgba(crown_color, int(strength * 0.46)))
        halo.setColorAt(0.70, _rgba(crown_color, int(strength * 0.17)))
        halo.setColorAt(1.0, _rgba(crown_color, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(center, glow_radius, glow_radius)

        # -- restrained orbit guides ----------------------------------------- #
        painter.setBrush(Qt.NoBrush)
        guide_alpha = 22 if muted else 34
        painter.setPen(QPen(_rgba(crown_color, guide_alpha), 1.0))
        painter.drawEllipse(center, radius * 1.34, radius * 1.34)
        painter.setPen(QPen(_rgba(crown_color, guide_alpha + 16), 1.1))
        painter.drawEllipse(center, radius * 1.16, radius * 1.16)

        # -- particle belt + counter-rotating orbit rings --------------------- #
        self._draw_particle_belt(painter, center, radius, crown_color, state,
                                 energy)
        self._draw_orbit_rings(painter, center, radius, crown_color, state,
                               energy, breath)

        # -- segmented radar crown (rotates with the state) ------------------ #
        self._draw_radar_crown(painter, center, radius, crown_color,
                               state, energy)

        # -- glass body ------------------------------------------------------- #
        body_center = QPointF(center.x() - radius * 0.16,
                              center.y() - radius * 0.18)
        body = QRadialGradient(body_center, radius * 1.30)
        body.setColorAt(0.0, QColor(8, 26, 35, 248))
        body.setColorAt(0.34, QColor(5, 17, 24, 250))
        body.setColorAt(0.66, QColor(3, 10, 16, 251))
        body.setColorAt(0.88, QColor(3, 8, 13, 252))
        body.setColorAt(1.0, QColor(2, 6, 10, 253))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(body))
        painter.drawEllipse(center, radius, radius)

        # A soft instrument rim: the glass picks up the live state colour,
        # while the centre stays dark enough for the hexagon to read.
        rim = QRadialGradient(center, radius * 1.01)
        rim.setColorAt(0.0, QColor(0, 0, 0, 0))
        rim.setColorAt(0.68, QColor(0, 0, 0, 0))
        rim.setColorAt(0.90, _rgba(crown_color, 58 if not muted else 20))
        rim.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(rim))
        painter.drawEllipse(center, radius, radius)

        # A soft glass rim, not a hard instrument edge.
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_rgba(core, 80 if not muted else 38),
                            max(0.9, radius * 0.012)))
        painter.drawEllipse(center, radius * 0.995, radius * 0.995)

        # -- glowing hexagon core -------------------------------------------- #
        self._draw_hex_core(painter, center, radius, crown_color, state,
                            energy, breath)

        # -- live state motion ------------------------------------------------- #
        if state == LISTENING:
            # A slow sonar ripple, softened by the microphone level.
            for index in range(2):
                progress = ((phase / (2 * math.pi)) + index * 0.5) % 1.0
                ripple = radius * (1.28 + 0.48 * progress)
                alpha = int((1.0 - progress) * (44 + 34 * energy))
                painter.setPen(QPen(_rgba(crown_color, alpha),
                                    max(0.9, radius * 0.010)))
                painter.drawEllipse(center, ripple, ripple)

        elif state == SPEAKING:
            # The voice itself pushes the bloom and rings outward.
            reach = 1.26 + 0.42 * energy
            painter.setPen(QPen(_rgba(crown_color, 58 + int(64 * energy)),
                                max(1.0, radius * 0.014)))
            painter.drawEllipse(center, radius * reach, radius * reach)
            for index in range(2):
                progress = ((phase / (2 * math.pi)) + index * 0.5) % 1.0
                ripple = radius * (1.30 + (0.55 + 0.25 * energy) * progress)
                alpha = int((1.0 - progress) * (40 + 90 * energy))
                painter.setPen(QPen(_rgba(crown_color, alpha),
                                    max(0.9, radius * 0.011)))
                painter.drawEllipse(center, ripple, ripple)

        elif state == THINKING:
            painter.save()
            painter.translate(center)
            painter.rotate(math.degrees(self._rotation))
            arc_rect = QRectF(-radius * 1.22, -radius * 1.22,
                              radius * 2.44, radius * 2.44)
            painter.setPen(QPen(_rgba(crown_color, 168),
                                max(1.4, radius * 0.022),
                                Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(arc_rect, 35 * 16, 92 * 16)
            painter.drawArc(arc_rect, 215 * 16, 52 * 16)
            painter.restore()

        elif state == ERROR:
            flash = abs(math.sin(phase * 1.8))
            painter.setPen(QPen(_rgba(crown_color, 70 + int(75 * flash)),
                                max(1.2, radius * 0.018)))
            painter.drawEllipse(center, radius * 1.25, radius * 1.25)

        elif state == MUTED:
            offset = radius * 0.26
            painter.setPen(QPen(QColor(148, 163, 184, 190),
                                max(1.6, radius * 0.028),
                                Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(
                QPointF(center.x() - offset, center.y() + offset),
                QPointF(center.x() + offset, center.y() - offset),
            )

        # -- soft specular highlight ----------------------------------------- #
        highlight_center = QPointF(center.x() - radius * 0.28,
                                   center.y() - radius * 0.34)
        highlight_radius = radius * (0.34 + 0.04 * energy)
        highlight = QRadialGradient(highlight_center, highlight_radius)
        highlight.setColorAt(0.0, QColor(255, 255, 255, 54))
        highlight.setColorAt(0.55, QColor(255, 255, 255, 12))
        highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(highlight))
        painter.drawEllipse(highlight_center, highlight_radius,
                            highlight_radius)
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
    """A segmented HUD equaliser driven by ``voice.set_level_sink``.

    Each tick appends one live loudness sample.  The painter turns that rolling
    history into dozens of tiny stacked segments above *and* below the centre
    line, so the deck reads like a hardware spectrum analyser rather than a
    single thin ribbon.
    """

    BARS = 58

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(44)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # Seed the history with a whisper of idle energy so the very first frame
        # already reads as a full-width spectrum rather than a half-drawn line.
        self._bars = deque([0.045] * self.BARS, maxlen=self.BARS)
        self._level = 0.0
        self._phase = 0.0
        self._state = IDLE

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)              # ~25 fps spectrum motion

    def set_level(self, value):
        """Feed one 0..1 loudness sample (mic RMS or TTS envelope)."""
        try:
            self._level = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self._level = 0.0

    def set_state(self, state):
        self._state = state
        self.update()

    def _tick(self):
        self._phase += 0.55
        wave = abs(math.sin(self._phase))
        value = self._level * (0.55 + 0.45 * wave)
        if self._state == IDLE:
            value = max(value, 0.080 + 0.070 * abs(math.sin(self._phase * 0.6)))
        elif self._state == MUTED:
            value = 0.0
        self._bars.append(max(0.0, min(1.0, value)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        if width <= 2 or height <= 2:
            painter.end()
            return

        glow, core = _COLORS.get(self._state, _COLORS[IDLE])
        muted = self._state == MUTED
        center_y = height / 2.0

        # A single faint baseline keeps the waveform anchored without the old
        # instrument grid.
        painter.setPen(QPen(_rgba(glow, 18 if not muted else 10), 1.0))
        painter.drawLine(2, int(center_y), width - 3, int(center_y))

        count = len(self._bars)
        left, right = 3.0, max(4.0, width - 4.0)
        step = (right - left) / max(1, count)
        bar_width = max(1.2, min(3.4, step * 0.50))
        amplitude = max(2.0, height / 2.0 - 3.0)

        top = QLinearGradient(0.0, 0.0, 0.0, center_y)
        top.setColorAt(0.0, _rgba(core, 225 if not muted else 90))
        top.setColorAt(0.62, _rgba(glow, 170 if not muted else 62))
        top.setColorAt(1.0, _rgba(glow, 55 if not muted else 22))
        bottom = QLinearGradient(0.0, center_y, 0.0, float(height))
        bottom.setColorAt(0.0, _rgba(glow, 55 if not muted else 22))
        bottom.setColorAt(0.62, _rgba(glow, 125 if not muted else 48))
        bottom.setColorAt(1.0, _rgba(core, 180 if not muted else 76))

        for index, raw in enumerate(self._bars):
            value = max(0.0, min(1.0, float(raw)))
            if self._state == IDLE:
                value = max(value, 0.055 + 0.045 * abs(
                    math.sin(index * 0.38 + self._phase * 0.5)
                ))
            if value <= 0.015:
                continue
            envelope = 0.50 + 0.50 * abs(
                math.sin(index * 0.52 + self._phase * 0.40)
            )
            energy = min(1.0, value * (0.65 + 0.55 * envelope))
            bar_height = max(1.0, amplitude * energy)
            x = left + index * step + (step - bar_width) / 2.0

            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(top))
            painter.drawRoundedRect(
                QRectF(x, center_y - bar_height, bar_width, bar_height),
                bar_width / 2.0, bar_width / 2.0,
            )
            skirt = bar_height * 0.38
            painter.setBrush(QBrush(bottom))
            painter.drawRoundedRect(
                QRectF(x, center_y, bar_width, max(1.0, skirt)),
                bar_width / 2.0, bar_width / 2.0,
            )
        painter.end()


# --------------------------------------------------------------------------- #
# control dock
# --------------------------------------------------------------------------- #

class PillButton(QPushButton):
    """A glass pill control for the HERMES command dock.

    The pill paints its own background, border, bloom and tracked uppercase
    label.  ``QPushButton.text()`` stays untouched so the existing mute/hotkey
    logic can keep using the historical labels.
    """

    def __init__(self, text="", kind="toggle", height=30, parent=None):
        super().__init__(text, parent)
        self._kind = kind
        self._height = height
        self._pressed_look = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFlat(True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setFixedHeight(height)
        self._font = _hud_font(7.4, QFont.DemiBold, 0.9)
        self.setFont(self._font)

    def _display_text(self):
        text = self.text().upper()
        return text

    def sizeHint(self):
        metrics = self.fontMetrics()
        width = metrics.horizontalAdvance(self._display_text()) + 30
        return QSize(max(38, width), self._height)

    def minimumSizeHint(self):
        return self.sizeHint()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        if width <= 2 or height <= 2:
            painter.end()
            return

        enabled = self.isEnabled()
        checked = self.isChecked()
        hover = self.underMouse()
        down = self.isDown()
        is_stop = self._kind == "stop"
        is_settings = self._kind == "settings"

        if not enabled:
            accent = QColor(100, 116, 139)
            ink = QColor(148, 163, 184)
        elif is_stop:
            accent = QColor(148, 163, 184)
            ink = QColor(241, 245, 249)
        elif checked:
            accent = QColor(0, 229, 255)
            ink = QColor(224, 254, 255)
        else:
            accent = QColor(0, 217, 197)
            ink = QColor(203, 213, 225)

        rect = QRectF(0.8, 0.8, width - 1.6, height - 1.6)
        radius = rect.height() / 2.0

        # Glass fill, stronger when checked or hovered.
        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        if checked and enabled:
            fill.setColorAt(0.0, _rgba(accent, 52))
            fill.setColorAt(0.48, _rgba(accent, 24))
            fill.setColorAt(1.0, QColor(10, 18, 26, 200))
        elif hover and enabled:
            fill.setColorAt(0.0, _rgba(accent, 30))
            fill.setColorAt(1.0, QColor(10, 17, 24, 190))
        else:
            fill.setColorAt(0.0, _rgba(accent, 16))
            fill.setColorAt(1.0, QColor(10, 15, 22, 170))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(rect, radius, radius)

        # Outer bloom around a live toggle.
        if (checked or hover or is_stop) and enabled:
            bloom = QRadialGradient(QPointF(width / 2.0, height / 2.0),
                                    width / 2.0)
            bloom.setColorAt(0.0, _rgba(accent, 30 if hover else 18))
            bloom.setColorAt(0.72, _rgba(accent, 8))
            bloom.setColorAt(1.0, _rgba(accent, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(bloom))
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2),
                                    radius + 2, radius + 2)

        border_alpha = 150 if (checked or hover) and enabled else 58
        if not enabled:
            border_alpha = 34
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_rgba(accent, border_alpha),
                            1.2 if checked else 1.0))
        painter.drawRoundedRect(rect, radius, radius)

        # A tiny status LED on toggles gives the dock its instrument feel.
        if self.isCheckable() and not is_settings and not is_stop:
            led = QPointF(10.0, height / 2.0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(_rgba(accent, 235 if checked and enabled else 70))
            painter.drawEllipse(led, 2.0, 2.0)

        if down:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 44))
            painter.drawRoundedRect(rect, radius, radius)

        painter.setFont(self._font)
        painter.setPen(ink)
        painter.drawText(rect, Qt.AlignCenter, self._display_text())

        if checked and enabled:
            # Hairline specular on the upper edge of an active pill.
            sheen = QLinearGradient(rect.left(), rect.top(),
                                    rect.right(), rect.top())
            sheen.setColorAt(0.0, _rgba(accent, 0))
            sheen.setColorAt(0.36, _rgba(accent, 150))
            sheen.setColorAt(0.64, _rgba(accent, 110))
            sheen.setColorAt(1.0, _rgba(accent, 0))
            painter.setPen(QPen(QBrush(sheen), 1.0))
            painter.drawLine(QPointF(rect.left() + radius, rect.top() + 1),
                             QPointF(rect.right() - radius, rect.top() + 1))
        painter.end()


class DockButton(QPushButton):
    """A circular dock control with restrained hover / pressed / mute states."""

    def __init__(self, text="", diameter=50, kind="mic", parent=None):
        super().__init__(text, parent)
        self._kind = kind
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFlat(True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_Hover, True)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        if width <= 2 or height <= 2:
            painter.end()
            return

        center = QPointF(width / 2.0, height / 2.0)
        radius = min(width, height) / 2.0
        enabled = self.isEnabled()
        checked = self.isChecked()
        hover = self.underMouse()
        down = self.isDown()
        primary = self._kind == "mic"

        if not enabled:
            accent = QColor(100, 116, 139)
        elif checked:
            accent = QColor(251, 113, 133)
        elif primary:
            accent = QColor(0, 229, 255)
        else:
            accent = QColor(148, 163, 184)

        # A very soft lift behind the primary action only.
        if primary and enabled and not checked:
            bloom = QRadialGradient(center, radius)
            bloom.setColorAt(0.0, _rgba(accent, 48 if hover else 30))
            bloom.setColorAt(0.70, _rgba(accent, 10))
            bloom.setColorAt(1.0, _rgba(accent, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(bloom))
            painter.drawEllipse(center, radius, radius)

        body = QRectF(2.0, 2.0, width - 4.0, height - 4.0)
        fill = QRadialGradient(
            QPointF(center.x() - radius * 0.35, center.y() - radius * 0.42),
            radius * 1.55,
        )
        if not enabled:
            fill.setColorAt(0.0, QColor(21, 29, 40, 180))
            fill.setColorAt(1.0, QColor(12, 18, 26, 210))
        elif checked:
            fill.setColorAt(0.0, QColor(69, 26, 36, 235))
            fill.setColorAt(1.0, QColor(21, 16, 24, 240))
        elif primary:
            fill.setColorAt(0.0, QColor(20, 49, 61, 245))
            fill.setColorAt(1.0, QColor(14, 24, 34, 245))
        elif hover:
            fill.setColorAt(0.0, QColor(30, 41, 55, 245))
            fill.setColorAt(1.0, QColor(17, 25, 35, 245))
        else:
            fill.setColorAt(0.0, QColor(24, 34, 46, 245))
            fill.setColorAt(1.0, QColor(15, 22, 31, 245))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(fill))
        painter.drawEllipse(body)

        border_alpha = 80
        if not enabled:
            border_alpha = 40
        elif checked or hover:
            border_alpha = 150
        elif primary:
            border_alpha = 105
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_rgba(accent, border_alpha), 1.0))
        painter.drawEllipse(body.adjusted(0.5, 0.5, -0.5, -0.5))

        if down:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 46))
            painter.drawEllipse(body)

        ink = QColor(241, 245, 249, 245 if enabled else 110)
        if checked:
            ink = QColor(255, 226, 230, 245)
        pen_width = max(1.5, radius * 0.070)

        if self._kind == "mic":
            self._paint_mic(painter, center, radius, ink, pen_width, checked)
        elif self._kind == "stop":
            self._paint_stop(painter, center, radius, ink, enabled)
        else:
            self._paint_settings(painter, center, radius, ink, pen_width)
        painter.end()

    @staticmethod
    def _paint_mic(painter, center, radius, ink, pen_width, muted):
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(ink, pen_width, Qt.SolidLine, Qt.RoundCap,
                            Qt.RoundJoin))
        capsule = QRectF(center.x() - radius * 0.10,
                         center.y() - radius * 0.34,
                         radius * 0.20, radius * 0.40)
        painter.drawRoundedRect(capsule, radius * 0.10, radius * 0.10)
        cradle = QRectF(center.x() - radius * 0.23,
                        center.y() - radius * 0.13,
                        radius * 0.46, radius * 0.38)
        painter.drawArc(cradle, 200 * 16, 140 * 16)
        painter.drawLine(QPointF(center.x(), center.y() + radius * 0.26),
                         QPointF(center.x(), center.y() + radius * 0.37))
        painter.drawLine(QPointF(center.x() - radius * 0.14,
                                 center.y() + radius * 0.37),
                         QPointF(center.x() + radius * 0.14,
                                 center.y() + radius * 0.37))
        if muted:
            slash = QPen(QColor(255, 210, 216, 235),
                         max(2.0, pen_width * 1.15),
                         Qt.SolidLine, Qt.RoundCap)
            painter.setPen(slash)
            painter.drawLine(QPointF(center.x() - radius * 0.27,
                                     center.y() - radius * 0.27),
                             QPointF(center.x() + radius * 0.27,
                                     center.y() + radius * 0.27))

    @staticmethod
    def _paint_stop(painter, center, radius, ink, enabled):
        painter.setPen(Qt.NoPen)
        painter.setBrush(ink if enabled else QColor(148, 163, 184, 140))
        size = radius * 0.42
        painter.drawRoundedRect(
            QRectF(center.x() - size / 2.0, center.y() - size / 2.0,
                   size, size),
            radius * 0.08, radius * 0.08,
        )

    @staticmethod
    def _paint_settings(painter, center, radius, ink, pen_width):
        painter.save()
        painter.translate(center)
        painter.setPen(QPen(ink, pen_width, Qt.SolidLine, Qt.RoundCap))
        for row, knob in ((-0.20, -0.12), (0.0, 0.10), (0.20, -0.04)):
            y = radius * row
            painter.drawLine(QPointF(-radius * 0.30, y),
                             QPointF(radius * 0.30, y))
            painter.setBrush(ink)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(radius * knob, y),
                                radius * 0.050, radius * 0.050)
            painter.setPen(QPen(ink, pen_width, Qt.SolidLine, Qt.RoundCap))
        painter.restore()


# --------------------------------------------------------------------------- #
# title bar
# --------------------------------------------------------------------------- #

def _window_button(text, name, tooltip):
    button = QPushButton(text)
    button.setObjectName(name)
    button.setFixedSize(30, 30)
    button.setCursor(Qt.PointingHandCursor)
    button.setFocusPolicy(Qt.NoFocus)
    button.setToolTip(tooltip)
    button.setStyleSheet(_WINDOW_BUTTON_STYLE)
    return button


class TitleBar(QWidget):
    """Custom frameless header: JARVIS identity, live clock, window controls."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._drag = None
        self._compact = False
        self._full_title = "JARVIS"
        self.setObjectName("titleBar")
        self.setFixedHeight(68)

        row = QHBoxLayout(self)
        row.setContentsMargins(24, 0, 16, 0)
        row.setSpacing(12)

        row.addWidget(BrandMark(30))

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        self.title = QLabel(self._full_title)
        self.title.setObjectName("appName")
        self.title.setStyleSheet(
            "color:#F1F5F9; background:transparent; font-size:16px;"
            "font-weight:600;"
        )
        self.subtitle = QLabel("Personal AI assistant")
        self.subtitle.setObjectName("appSubtitle")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        row.addLayout(titles)
        row.addStretch(1)

        # -- quiet presence + clock ------------------------------------------ #
        self.connection_dot = QLabel("●")
        self.connection_dot.setStyleSheet(
            "color:#00D9C5; background:transparent; font-size:9px;"
        )
        self.connection_label = QLabel("Online")
        self.connection_label.setObjectName("connectionLabel")
        row.addWidget(self.connection_dot)
        row.addWidget(self.connection_label)
        row.addSpacing(8)

        clock_box = QVBoxLayout()
        clock_box.setContentsMargins(0, 0, 0, 0)
        clock_box.setSpacing(0)
        self.clock = QLabel("00:00")
        self.clock.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.clock.setObjectName("clockLabel")
        self.date_label = QLabel("")
        self.date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.date_label.setObjectName("panelFootnote")
        clock_box.addWidget(self.clock)
        clock_box.addWidget(self.date_label)
        row.addLayout(clock_box)
        row.addSpacing(12)

        self.minimize_button = _window_button("−", "minimizeButton", "Minimise")
        self.maximize_button = _window_button("□", "maximizeButton", "Maximise")
        self.close_button = _window_button("✕", "closeButton", "Hide to tray")
        self.minimize_button.clicked.connect(window._minimize)
        self.maximize_button.clicked.connect(window._toggle_maximize)
        self.close_button.clicked.connect(window._close_to_tray)
        row.addWidget(self.minimize_button)
        row.addWidget(self.maximize_button)
        row.addWidget(self.close_button)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    def _update_clock(self):
        now = time.localtime()
        self.clock.setText(time.strftime("%H:%M" if self._compact else "%H:%M:%S", now))
        self.date_label.setText(time.strftime("%a %d %b", now))

    def set_compact(self, compact):
        self._compact = bool(compact)
        self.title.setText("JARVIS")
        self.subtitle.setVisible(not compact)
        self.connection_dot.setVisible(not compact)
        self.connection_label.setVisible(not compact)
        self.clock.setVisible(not compact)
        self.date_label.setVisible(not compact)
        self._update_clock()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(36, 51, 66, 190), 1.0))
        painter.drawLine(20, self.height() - 1,
                         self.width() - 20, self.height() - 1)
        painter.end()

    # -- frameless drag ---------------------------------------------------- #
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if getattr(self._window, "_is_maximized", False):
                self._drag = None
            else:
                self._drag = (event.globalPos()
                              - self._window.frameGeometry().topLeft())
        event.accept()

    def mouseMoveEvent(self, event):
        if (self._drag is not None
                and event.buttons() & Qt.LeftButton
                and not getattr(self._window, "_is_maximized", False)):
            self._window.move(event.globalPos() - self._drag)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._window._toggle_maximize()
        event.accept()


# --------------------------------------------------------------------------- #
# voice worker (backend logic preserved)
# --------------------------------------------------------------------------- #

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
    replied = pyqtSignal(str)
    failed = pyqtSignal(str)
    level = pyqtSignal(float)
    latency = pyqtSignal(float)
    mic_changed = pyqtSignal(bool)
    exit_requested = pyqtSignal()

    def __init__(self, memory, parent=None, chat=False, chat_timeout=CHAT_TIMEOUT):
        super().__init__(parent)
        self.memory = memory
        self.chat = chat
        self.chat_timeout = chat_timeout
        self._barged_in = False
        self._failure = None
        # Set when run() leaves because the mic was muted. The GUI uses this to
        # decide whether unmuting should reopen a listening session: a session
        # that ended normally (silence, Stop, exit word) must not auto-restart.
        self.stopped_for_mute = False

    def run(self):
        # The level sink is a module global in voice; only one worker runs at a
        # time, so emitting our signal from it is safe (Qt queues it to the GUI).
        voice.set_level_sink(self.level.emit)
        first_turn = True                     # the wake click listens normally
        voice._stop_speaking.clear()          # a stale Stop must not end this chat
        self._failure = None
        self.stopped_for_mute = False
        youtube_followup = False
        # A video may have been started by a direct/programmatic call before this
        # worker began (a previous session or a test); discard that stale token so
        # it can never arm the window for an unrelated conversation.
        desktop.consume_youtube_playback()
        try:
            while True:
                # ``listen()`` also refuses while muted, but checking here keeps
                # the UI from flashing "Listening..." before a muted worker exits.
                if voice.is_mic_muted():
                    self.stopped_for_mute = True
                    return
                self.state_changed.emit(LISTENING)
                # Talking back and forth should feel natural: the wait between
                # turns is the chat timeout, so Jarvis stays with you instead of
                # going to sleep after 30 s. If the previous turn was cut short
                # by barge-in, tell listen() so a pure "stop" is not dispatched.
                was_barge = self._barged_in
                self._barged_in = False
                # After YouTube starts, keep the microphone open for exactly 15
                # seconds so "pause", "next" and "full screen" work; then drop
                # back to Idle instead of listening to the music.
                wait = None if first_turn else self.chat_timeout
                if youtube_followup:
                    wait = voice.YOUTUBE_LISTEN_TIMEOUT
                user = listen(wait, barge=was_barge)
                first_turn = False
                if not user:
                    # A YouTube control window that simply expires says "Enjoy!"
                    # and ends the session. An explicit Stop or mute does not.
                    if (youtube_followup and not voice.is_mic_muted()
                            and not voice._stop_listening.is_set()):
                        self.state_changed.emit(SPEAKING)
                        speak("Enjoy!")
                    # You went quiet (or pressed Stop / muted the mic): end the
                    # conversation instead of grabbing the microphone again.
                    if voice.is_mic_muted():
                        self.stopped_for_mute = True
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
                started = time.monotonic()
                reply, _kind, _tokens = handle(user, self.memory)
                self.latency.emit(
                    max(0.0, (time.monotonic() - started) * 1000.0)
                )
                self.replied.emit(reply)
                # A local command can mute/unmute the mic (media playback mutes
                # it), so keep the GUI button and tray in sync.
                self.mic_changed.emit(voice.is_mic_muted())
                # A confirmed YouTube request starts playback inside handle();
                # arm the short control window for the next listen.
                if desktop.consume_youtube_playback():
                    youtube_followup = True

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
            logger.log_command_error("GUI voice worker failed", exc)
            self._failure = str(exc) or type(exc).__name__
            self.state_changed.emit(IDLE)
            self.message.emit(f"⚠️ {exc}")
        finally:
            voice.set_level_sink(None)
            self.level.emit(0.0)
            # A voice/media command may have muted the mic: leave the card in
            # the muted state instead of pretending it is ready to listen.
            self.state_changed.emit(MUTED if voice.is_mic_muted() else IDLE)
            if self._failure:
                self.failed.emit(self._failure)

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
        except Exception as exc:
            logger.log_command_error("Barge-in monitor failed to start", exc)
        try:
            speak(reply)
        finally:
            monitor.stop()
        return self._barged_in


class TextCommandWorker(QThread):
    """Run one typed command through the same dispatcher as the voice loop.

    The command bar is a text conversation surface, so replies are rendered in
    the transcript but not spoken.  A real measured round-trip is emitted for
    the SYSTEM panel instead of a synthetic latency figure.
    """

    state_changed = pyqtSignal(str)
    replied = pyqtSignal(str)
    failed = pyqtSignal(str)
    latency = pyqtSignal(float)
    kind = pyqtSignal(str)

    def __init__(self, memory, text, parent=None):
        super().__init__(parent)
        self.memory = memory
        self.text = str(text or "").strip()

    def run(self):
        self.state_changed.emit(THINKING)
        started = time.monotonic()
        try:
            reply, kind, _tokens = handle(self.text, self.memory)
            self.latency.emit(max(0.0, (time.monotonic() - started) * 1000.0))
            self.kind.emit(str(kind or ""))
            self.replied.emit(reply)
        except Exception as exc:                  # never kill the GUI
            logger.log_command_error("Typed command failed", exc)
            self.latency.emit(max(0.0, (time.monotonic() - started) * 1000.0))
            self.failed.emit(str(exc) or type(exc).__name__)
        finally:
            self.state_changed.emit(
                MUTED if voice.is_mic_muted() else IDLE
            )

    def stop(self):
        """Text commands cannot be cancelled mid-tool; Stop remains voice-only."""
        return


# --------------------------------------------------------------------------- #
# first-run setup wizard
# --------------------------------------------------------------------------- #

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
        self.setWindowTitle("JARVIS // SETUP")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet(
            "QDialog { background:#080C11; }"
            "QLabel { color:#D7E3EE; background:transparent; }"
            "QLineEdit { color:#F9FAFB; background:rgba(255,255,255,10);"
            " border:1px solid rgba(103,220,235,45); border-radius:10px;"
            " padding:10px; font-size:13px; }"
            "QLineEdit:focus { border-color:#22D3EE; }"
            "QPushButton { color:#D7E3EE; background:rgba(255,255,255,14);"
            " border:1px solid rgba(255,255,255,24); border-radius:9px;"
            " padding:8px 14px; font-size:12px; font-weight:600; }"
            "QPushButton:hover { background:rgba(255,255,255,28); }"
            "QPushButton#saveButton { color:#041014; background:#22D3EE;"
            " border-color:#22D3EE; }"
            "QPushButton#saveButton:hover { background:#67E8F9; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(12)

        heading = QLabel("JARVIS")
        heading.setStyleSheet(
            "color:#F9FAFB; font-size:21px; font-weight:800;"
            " letter-spacing:4px;"
        )
        layout.addWidget(heading)

        sub = QLabel("NEURAL INTERFACE  //  FIRST BOOT")
        sub.setStyleSheet(
            "color:#3F6470; font-family:'DejaVu Sans Mono','Consolas';"
            "font-size:8px; letter-spacing:1.6px;"
        )
        layout.addWidget(sub)

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


class SettingsDialog(QDialog):
    """HUD-themed settings panel for the Groq API key.

    Unlike :class:`ApiKeyDialog`, this is not a first-run gate: it is opened
    from the session menu at any time. ``SAVE`` writes the key to the per-user
    ``~/.jarvis/.env`` (mode ``0600``) and calls :func:`config.apply_api_key`,
    so the running process starts using it immediately without a restart.
    """

    CONSOLE_URL = ApiKeyDialog.CONSOLE_URL

    def __init__(self, parent=None):
        super().__init__(parent)
        self.saved_path = None
        self.setWindowTitle("JARVIS // SETTINGS")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setStyleSheet(
            "QDialog { background:#03060A; }"
            "QLabel { color:#D7F6FA; background:transparent; }"
            "QLabel#settingsHeading { color:#F4FEFF; font-size:22px;"
            " font-weight:800; letter-spacing:4px; }"
            "QLabel#settingsSub { color:#2C7F8F;"
            " font-family:'DejaVu Sans Mono','Consolas'; font-size:8px;"
            " letter-spacing:1.6px; }"
            "QLabel#settingsHint { color:#5E8C96; font-size:11px; }"
            "QLabel#settingsError { color:#FCA5A5; font-size:11px; }"
            "QLabel#settingsStatus { color:#5EEAD4; font-size:11px; }"
            "QLineEdit { color:#F4FEFF; background:rgba(0,229,255,8);"
            " border:1px solid rgba(0,229,255,80); border-radius:10px;"
            " padding:10px; font-size:13px;"
            " selection-background-color:rgba(0,229,255,90); }"
            "QLineEdit:focus { border:1px solid #00E5FF;"
            " background:rgba(0,229,255,14); }"
            "QPushButton { color:#A9DCE5; background:rgba(0,229,255,8);"
            " border:1px solid rgba(0,229,255,70); border-radius:9px;"
            " padding:8px 16px; font-size:12px; font-weight:700; }"
            "QPushButton:hover { background:rgba(0,229,255,20);"
            " color:#F4FEFF; }"
            "QPushButton#saveButton { color:#021014; background:#00E5FF;"
            " border-color:#00E5FF; }"
            "QPushButton#saveButton:hover { background:#7DF3FF; }"
            "QPushButton#closeSettingsButton:hover {"
            " background:rgba(251,113,133,20); border-color:#FB7185;"
            " color:#FECDD3; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(12)

        heading = QLabel("SETTINGS")
        heading.setObjectName("settingsHeading")
        layout.addWidget(heading)

        sub = QLabel("NEURAL INTERFACE  //  API ACCESS")
        sub.setObjectName("settingsSub")
        layout.addWidget(sub)

        prompt = QLabel("GROQ API KEY")
        prompt.setStyleSheet(
            "font-size:11px; font-weight:700; letter-spacing:1.4px;"
            " color:#7FE6F5;"
        )
        layout.addWidget(prompt)

        self.key_input = QLineEdit()
        self.key_input.setObjectName("groqApiKeyInput")
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText("gsk_...")
        self.key_input.setClearButtonEnabled(True)
        # Pre-fill the active key (masked) so it can be edited instead of
        # retyped. An empty field at startup is left empty, not guessed.
        if config.API_KEY:
            self.key_input.setText(config.API_KEY)
        layout.addWidget(self.key_input)

        hint = QLabel(
            "Stored privately in ~/.jarvis/.env. The key is applied in-place, "
            "so no restart is needed."
        )
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.status_label = QLabel("")
        self.status_label.setObjectName("settingsStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.error_label = QLabel("")
        self.error_label.setObjectName("settingsError")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        self.console_button = QPushButton("GET A KEY")
        self.console_button.setToolTip(self.CONSOLE_URL)
        self.console_button.clicked.connect(self._open_console)

        self.close_button = QPushButton("CLOSE")
        self.close_button.setObjectName("closeSettingsButton")
        self.close_button.clicked.connect(self.reject)

        self.save_button = QPushButton("SAVE")
        self.save_button.setObjectName("saveButton")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.console_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self.key_input.returnPressed.connect(self._save)
        self.key_input.setFocus()

    def _open_console(self):
        """Open the Groq key page in the user's default browser."""
        QDesktopServices.openUrl(QUrl(self.CONSOLE_URL))

    def api_key(self):
        """Return the trimmed key currently in the field."""
        return self.key_input.text().strip()

    def _save(self):
        """Validate, persist and apply the key; keep the dialog open on error."""
        key = self.api_key()
        if not config.is_api_key_configured(key):
            self.error_label.setText(
                "Enter a valid Groq API key (it starts with gsk_)."
            )
            self.key_input.setFocus()
            return
        try:
            self.saved_path = config.save_api_key(key)
        except (OSError, ValueError) as exc:
            self.error_label.setText(f"Could not save the key: {exc}")
            return
        # save_api_key already calls apply_api_key; calling it explicitly keeps
        # the runtime contract obvious and makes a monkeypatched save harmless.
        config.apply_api_key(key)
        self.error_label.setText("")
        self.status_label.setText("Key saved — JARVIS is ready.")
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


# --------------------------------------------------------------------------- #
# tray
# --------------------------------------------------------------------------- #

class JarvisTray(QSystemTrayIcon):
    """Tray icon so Jarvis can live in the background.

    The tray menu is deliberately minimal: it only shows the window or quits.
    Talk, mute, wake-word, continuous-chat and auto-start toggles live in the
    main window's session menu, so duplicating them here only added clutter.
    """

    def __init__(self, widget):
        super().__init__(app_icon(64), widget)
        self.widget = widget
        self.setToolTip("JARVIS — click to show")

        menu = QMenu()
        menu.setStyleSheet(_MENU_STYLE)
        self.toggle_action = menu.addAction(
            "Hide JARVIS" if widget.isVisible() else "Show JARVIS"
        )
        self.toggle_action.triggered.connect(widget.toggle_visible)
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(widget.quit)
        self.setContextMenu(menu)

        # The tray no longer owns the auto-start toggle (the main window does),
        # but keep this alias for compatibility with callers that sync it here.
        self.autostart_action = getattr(widget, "autostart_action", None)

        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        """Bring the existing window forward when the tray icon is clicked."""
        if reason not in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            return
        widget = self.widget
        if widget.isMinimized():
            widget.showNormal()
        widget.show()
        widget.raise_()
        widget.activateWindow()

    def sync_toggle(self, visible):
        self.toggle_action.setText("Hide JARVIS" if visible else "Show JARVIS")

    def sync_mute(self, _muted):
        """Tray has no mute entry; the main window owns that toggle."""

    def sync_wake(self, _on):
        """Tray has no wake-word entry; the main window owns that toggle."""

    def sync_chat(self, _on):
        """Tray has no chat entry; the main window owns that toggle."""

    def sync_autostart(self, on):
        if self.autostart_action is not None:
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
    """The frameless command center: orb + telemetry + waveform + control dock."""

    # Emitted by the wake-word thread (never touch Qt from that thread).
    wake_heard = pyqtSignal()
    # Emitted by the pynput global-hotkey thread (never touch Qt from it either).
    hotkey_pressed = pyqtSignal()
    # Emitted by the wake-socket thread when `main.py --quit` asks us to exit.
    quit_requested = pyqtSignal()

    _WIDE_BREAKPOINT = 1180

    def __init__(self, compact=True):
        super().__init__()
        # A real top-level window (not Qt.Tool/Qt.Popup) so the desktop
        # environment gives JARVIS a taskbar entry. Clicking that entry brings
        # the already-running card back to the front; it never creates a second
        # instance. The window stays frameless and on top.
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
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
        self.setStyleSheet(_APP_STYLE)
        self.setWindowTitle("JARVIS")
        self.setWindowIcon(app_icon(256))
        # Frameless windows have no system resize border, so the card resizes
        # through the QSizeGrip in the corner and the custom edge dragging in the
        # mouse handlers below.
        self.setMinimumSize(250, 350)
        self._compact_mode = bool(compact)
        if compact:
            self.resize(280, 400)
        else:
            # The reference composition is 1536x1024.  On a smaller desktop the
            # HUD shrinks to the available work area instead of spilling under
            # the taskbar; the responsive pass folds panels away as needed.
            screen = QApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                width = min(1536, max(1000, available.width() - 40))
                height = min(1024, max(700, available.height() - 50))
                self.resize(min(width, available.width() - 20),
                            min(height, available.height() - 20))
            else:
                self.resize(1536, 1024)
        self.setMouseTracking(True)

        self.memory = Memory()
        self.worker = None
        self.text_worker = None
        self._latency_ms = None
        self._prev_state = IDLE
        self._last_net = None
        self._last_net_time = None
        self._drag_offset = None
        # Edge-resize drag state: which edges are grabbed, where the drag began
        # and the geometry it began from.
        self._resize_edges = set()
        self._resize_origin = None
        self._resize_start = None
        # Manual maximise state. Frameless windows cannot rely on
        # showMaximized() on Linux (it may not resize or may cover the taskbar),
        # so the maximised geometry is applied by hand and the pre-maximise
        # geometry is kept here for the restore.
        self._is_maximized = False
        self._normal_geometry = None
        self._quitting = False
        self._chat = CHAT_ON
        self._menu = QMenu(self)
        self._menu.setStyleSheet(_MENU_STYLE)
        self._wake = None
        # The wake-word listener runs on its own thread, so it signals us instead
        # of touching Qt: Qt then delivers it here on the GUI thread.
        self.wake_heard.connect(self._on_wake_heard)
        self._hotkey = None
        self.wake_server = None
        self.hotkey_pressed.connect(self._on_hotkey)
        self.quit_requested.connect(self.quit)

        self._state = IDLE
        self._energy = 0.0
        self._error_hold = False
        # The line under the state text shows the latest heard or spoken line,
        # falling back to the state hint when the conversation is quiet.
        self._live_text = ""
        self._flash_active = False
        # True when the mic was muted in the middle of an active session, so
        # unmuting can hand the microphone straight back to a fresh listen.
        self._mute_interrupted = False
        self._started_at = time.monotonic()
        self._telemetry_ticks = 0
        self._briefing_enabled = True

        self._build_ui()
        self._build_menu()
        self._refresh_briefing()
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
        self.title_bar = hud.HudTitleBar(self)
        self.minimize_button = self.title_bar.minimize_button
        self.maximize_button = self.title_bar.maximize_button
        self.close_button = self.title_bar.close_button

        # The reactor is the visual signature at the centre of the HUD.
        self.orb = Orb(214)
        self.orb.clicked.connect(self.trigger)
        self.orb.menu_requested.connect(self._show_menu)
        # Backwards-compatible alias: older code called the orb the "reactor".
        self.reactor = self.orb

        # Kept for the live-level pipeline, tests and embedders.  The HUD shows
        # the level inside the reactor, so the optional strip stays hidden.
        self.waveform = Waveform(self)
        self.waveform.setFixedHeight(44)
        self.waveform.hide()

        self.status = QLabel(IDLE)
        self.status.setObjectName("statusText")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setMinimumHeight(20)
        self.status.setFont(_hud_font(10.5, QFont.DemiBold, 3.4))
        self.status.setStyleSheet(
            f"color:{_STATE_TEXT[IDLE]}; background:transparent;"
        )

        self.online_label = QLabel("ONLINE")
        self.online_label.setObjectName("onlineLabel")
        self.online_label.setAlignment(Qt.AlignCenter)
        self.online_label.setFont(_hud_font(9.4, QFont.Bold, 8.0))
        self.online_label.setStyleSheet(
            f"color:{_ACCENT}; background:transparent;"
        )

        self.status_hint = QLabel(_STATE_HINT[IDLE])
        self.status_hint.setObjectName("statusHint")
        self.status_hint.setAlignment(Qt.AlignCenter)
        self.status_hint.setWordWrap(True)
        self.status_hint.setFixedHeight(34)
        self.status_hint.setFont(_hud_font(7.6, QFont.Normal, 0.6, _UI_SANS))
        self.status_hint.setStyleSheet(
            f"color:{_TEXT_MUTED}; background:transparent;"
        )
        self.status_hint.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.state_dot = QLabel("●")
        self.state_dot.setObjectName("stateDot")
        self.state_dot.setStyleSheet(
            f"color:{_STATE_TEXT[IDLE]}; background:transparent; font-size:9px;"
        )

        # Hidden compatibility sink used by the existing tests and flash path.
        self.transcript = QLabel("", self)
        self.transcript.setAlignment(Qt.AlignCenter)
        self.transcript.setWordWrap(False)
        self.transcript.hide()

        self.compact_telemetry = CompactTelemetry(self)
        self.compact_telemetry.hide()

        self.left_panel = hud.LeftRail(self)
        self.right_panel = hud.RightRail(self)
        self.command_bar = hud.CommandBar(self)
        self.command_bar.submitted.connect(self._submit_text)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        status_row.addStretch(1)
        status_row.addWidget(self.state_dot)
        status_row.addWidget(self.status)
        status_row.addStretch(1)

        # -- circular control dock: mic / stop / settings ------------------- #
        self.mic_button = DockButton(_MIC_LABEL, 48, "mic", self)
        self.mic_button.setCheckable(True)
        self.mic_button.setToolTip("Microphone on — click to mute")
        self.mic_button.clicked.connect(self.toggle_mute)

        self.stop_button = DockButton("Stop", 46, "stop", self)
        self.stop_button.setEnabled(False)
        self.stop_button.setToolTip("Stop listening or speaking (Space)")
        self.stop_button.clicked.connect(self._stop)

        self.settings_button = DockButton("Settings", 46, "settings", self)
        self.settings_button.setToolTip("Session options")
        self.settings_button.clicked.connect(self._show_settings)

        dock_bar = QFrame()
        dock_bar.setObjectName("controlDock")
        dock = QHBoxLayout(dock_bar)
        dock.setContentsMargins(9, 7, 9, 7)
        dock.setSpacing(5)
        dock.addWidget(self.mic_button)
        dock.addWidget(self.stop_button)
        dock.addWidget(self.settings_button)
        self._dock_layout = dock

        # Session toggles are still live and still drive the same backend, but
        # the reference dock has only three actions.  The settings menu and the
        # right-click menu carry the full set.
        self.wake_button = PillButton(
            "WAKE  ON" if self._wake is not None else "WAKE  OFF",
            "toggle", parent=self,
        )
        self.wake_button.setCheckable(True)
        self.wake_button.setChecked(self._wake is not None)
        self.wake_button.setToolTip("Toggle the Hey JARVIS wake word")
        self.wake_button.clicked.connect(self.toggle_wake)
        self.wake_button.hide()

        self.auto_button = PillButton(
            "AUTO  ON" if self._chat else "AUTO  OFF", "toggle", parent=self
        )
        self.auto_button.setCheckable(True)
        self.auto_button.setChecked(self._chat)
        self.auto_button.setToolTip("Continuous conversation mode")
        self.auto_button.clicked.connect(self.toggle_chat)
        self.auto_button.hide()

        self.briefing_button = PillButton("BRIEF  ON", "toggle", parent=self)
        self.briefing_button.setCheckable(True)
        self.briefing_button.setChecked(True)
        self.briefing_button.setToolTip("Toggle the memory briefing stream")
        self.briefing_button.clicked.connect(self._toggle_briefing)
        self.briefing_button.hide()

        self.voice_combo = QComboBox(self)
        self.voice_combo.setObjectName("voiceCombo")
        self.voice_combo.setStyleSheet(_COMBO_STYLE)
        self.voice_combo.setFixedHeight(30)
        self.voice_combo.setMinimumWidth(142)
        self.voice_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.voice_combo.addItem(
            _voice_label(getattr(config, "TTS_VOICE", None))
        )
        self.voice_combo.setToolTip("Voice profile")
        self.voice_combo.currentTextChanged.connect(self._on_voice_changed)
        self.voice_combo.hide()

        # Compatibility labels for the uptime footer / older integrations.
        self._wave_tag = QLabel("", self)
        self._wave_live = QLabel("", self)
        self._session_label = QLabel("", self)
        self._hotkey_label = QLabel("", self)
        for label in (self._wave_tag, self._wave_live,
                      self._session_label, self._hotkey_label):
            label.hide()

        # -- central reactor stage ------------------------------------------ #
        center = QVBoxLayout()
        center.setContentsMargins(0, 4, 0, 0)
        center.setSpacing(0)
        center.addStretch(1)
        center.addWidget(self.orb, 0, Qt.AlignHCenter)
        center.addSpacing(10)
        center.addWidget(self.online_label)
        center.addSpacing(3)
        center.addLayout(status_row)
        center.addSpacing(0)
        center.addWidget(self.status_hint)
        center.addSpacing(10)
        center.addWidget(dock_bar, 0, Qt.AlignHCenter)
        center.addStretch(1)

        body = QHBoxLayout()
        body.setContentsMargins(24, 8, 24, 8)
        body.setSpacing(16)
        body.addWidget(self.left_panel)
        body.addLayout(center, 1)
        body.addWidget(self.right_panel)

        body_host = QWidget()
        body_host.setObjectName("hudBody")
        body_host.setAttribute(Qt.WA_TranslucentBackground, True)
        body_host.setLayout(body)

        # -- bottom console + the floating "HEY JARVIS" pill ---------------- #
        self.hey_button = PillButton("• HEY JARVIS", "toggle", 28, self)
        self.hey_button.setFixedWidth(144)
        self.hey_button.setToolTip("Start a conversation with Jarvis")
        self.hey_button.clicked.connect(self.trigger)

        self.command_host = QWidget(self)
        self.command_host.setAttribute(Qt.WA_TranslucentBackground, True)
        command_layout = QVBoxLayout(self.command_host)
        command_layout.setContentsMargins(24, 0, 24, 15)
        command_layout.setSpacing(6)
        pill_row = QHBoxLayout()
        pill_row.setContentsMargins(0, 0, 0, 0)
        pill_row.setSpacing(0)
        pill_row.addStretch(1)
        pill_row.addWidget(self.hey_button)
        command_layout.addLayout(pill_row)
        command_layout.addWidget(self.command_bar)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.title_bar)
        root.addWidget(body_host, 1)
        root.addWidget(self.command_host, 0)

        # Frameless window gestures need to start on the surfaces that actually
        # receive the mouse. The title bar, body and command console are
        # transparent containers, so installing this widget as their event
        # filter lets an empty header/body area drag the window and the outer
        # 8 px border resize it, even though the QWidget below the layout would
        # normally swallow the press before it reached JarvisWidget.
        self.body_host = body_host
        for surface in (self.title_bar, body_host, self.command_host):
            surface.setMouseTracking(True)
            surface.installEventFilter(self)

        # Tab inside the command field starts listening, matching the hint.
        self.command_bar.input.tab_pressed.connect(self.trigger)

        # Cmd+Q (macOS) / Ctrl+Q (Linux, Windows) quits the app explicitly.
        self._quit_shortcuts = []
        for sequence in ("Ctrl+Q", "Meta+Q"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self.quit)
            self._quit_shortcuts.append(shortcut)

        # Frameless card: a visible QSizeGrip in the bottom-right corner resizes
        # the window; the custom edge dragging below covers the rest of the
        # border. It is positioned manually so it does not consume layout height.
        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(16, 16)
        self.size_grip.setToolTip("Resize")
        self.size_grip.raise_()

        # Ctrl+K is the documented keyboard route into the command console.
        self._command_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._command_shortcut.activated.connect(self._focus_command)

        # -- adaptive telemetry timer -------------------------------------- #
        self._telemetry_timer = QTimer(self)
        self._telemetry_timer.timeout.connect(self._update_telemetry)
        self._telemetry_timer.start(1000)

        self.left_panel.set_mode(self._chat)
        self.left_panel.set_wake(self._wake is not None)
        self.left_panel.set_voice(
            _voice_label(getattr(config, "TTS_VOICE", None))
        )
        self._sync_briefing()
        self._sync_responsive()
        self._place_grip()
        self._refresh_header_link()
        self._log_activity("CORE reactor online", "state")
        self._log_activity(
            "wake word " + ("armed" if self._wake is not None else "offline"),
            "info",
        )
        self._log_activity(
            "second instance guard + shutdown safeguards active", "info"
        )

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
        self.briefing_action = self._menu.addAction("Conversation history")
        self.briefing_action.setCheckable(True)
        self.briefing_action.setChecked(self._briefing_enabled)
        self.briefing_action.triggered.connect(self._toggle_briefing)
        self.autostart_action = self._menu.addAction("Start on Boot")
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(startup.is_autostart_enabled())
        self.autostart_action.triggered.connect(self.toggle_autostart)
        self.toggle_action = self._menu.addAction("Hide to tray")
        self.toggle_action.triggered.connect(self.toggle_visible)
        self._menu.addSeparator()
        self.settings_action = self._menu.addAction("Settings / API Key")
        self.settings_action.triggered.connect(self._open_settings_dialog)
        self._menu.addSeparator()
        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(self.quit)

    def _open_settings_dialog(self):
        """Open the API-key settings panel from the session/context menu."""
        dialog = SettingsDialog(self)
        self.settings_dialog = dialog
        dialog.exec_()

    def _show_menu(self, global_pos):
        self._menu.exec_(global_pos)

    def _show_settings(self):
        """Open the session options menu next to the dock button."""
        position = self.settings_button.mapToGlobal(
            QPoint(0, -self.settings_button.height() - 6)
        )
        self._menu.exec_(position)

    def _on_voice_changed(self, name):
        """Reflect the selected voice profile in the telemetry rail."""
        panel = getattr(self, "left_panel", None)
        if panel is not None:
            panel.set_voice(name)
        self._flash(f"Voice profile · {name}")

    def _sync_briefing(self):
        button = getattr(self, "briefing_button", None)
        if button is not None:
            button.setChecked(self._briefing_enabled)
            button.setText(
                "BRIEF  ON" if self._briefing_enabled else "BRIEF  OFF"
            )
        action = getattr(self, "briefing_action", None)
        if action is not None:
            action.setChecked(self._briefing_enabled)

    def _toggle_briefing(self, _checked=False):
        """Pause or resume the memory briefing stream (visual rail only)."""
        self._briefing_enabled = not self._briefing_enabled
        self._sync_briefing()
        panel = getattr(self, "right_panel", None)
        if self._briefing_enabled:
            self._refresh_briefing()
            self._flash("Briefing stream online")
        elif panel is not None:
            panel.set_history([])
            self._flash("Briefing stream paused")

    def _center(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.move(available.center() - self.rect().center())

    def _minimize(self):
        """Minimise the card like a normal OS window; the tray keeps Jarvis up.

        The custom title-bar ``−`` must not hide the card to the tray: that
        would make it disappear from the taskbar and break the expected
        minimise/restore gesture. ``showMinimized()`` lets the window manager
        handle the taskbar entry and the restore animation.
        """
        self._reset_pointer_state()
        self.showMinimized()

    def _toggle_maximize(self):
        """Maximise the frameless card or restore its previous size.

        ``showMaximized()`` is unreliable for a frameless window on Linux: the
        window manager may leave the card at its current size or let it spill
        over the taskbar/panels. Toggling the geometry by hand against the
        screen's *available* geometry keeps the maximised card inside the
        desktop work area on every platform.
        """
        # A pending drag/resize from before the toggle would otherwise be
        # applied to the new geometry by the next mouse-move event.
        self._reset_pointer_state()

        if self._is_maximized:
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
            self._is_maximized = False
            self.maximize_button.setText("□")
            self.maximize_button.setToolTip("Maximise")
        else:
            self._normal_geometry = self.geometry()
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                # availableGeometry() excludes docks, panels and the taskbar.
                self.setGeometry(screen.availableGeometry())
            self._is_maximized = True
            self.maximize_button.setText("❐")
            self.maximize_button.setToolTip("Restore")

        # A maximised card must not be resizable, so the size grip goes away
        # with the edge-resize handling.
        grip = getattr(self, "size_grip", None)
        if grip is not None:
            grip.setVisible(not self._is_maximized)
        self.setCursor(Qt.ArrowCursor)
        QTimer.singleShot(0, self._sync_responsive)

    def _close_to_tray(self):
        """X button: hide the card to the tray (the app keeps running).

        Delegates to :meth:`close`, which routes through :meth:`closeEvent` so
        the window-manager close and the ✕ button behave identically.
        """
        self.close()

    # -- responsive command center ---------------------------------------- #
    def _sync_responsive(self):
        """Show both rails on wide layouts and fold to a central card."""
        try:
            width, height = self.width(), self.height()
        except RuntimeError:                  # widget already torn down
            return
        compact = getattr(self, "_compact_mode", False)
        rails = (not compact and width >= self._WIDE_BREAKPOINT
                 and height >= 700)
        expanded = rails and height >= 960
        medium = (not compact and width >= 860 and height >= 600)
        show_command = not compact and height >= 500

        if hasattr(self, "left_panel"):
            self.left_panel.setVisible(rails)
            if rails:
                self.left_panel.set_expanded(expanded)
            self.right_panel.setVisible(medium)
            self.command_host.setVisible(show_command)
            self.title_bar.set_compact(compact or width < 940)
            self.status_hint.setVisible(height >= 380)
            self.status.setVisible(height >= 330)
            self.online_label.setFont(
                _hud_font(7.0 if compact else 9.4, QFont.Bold,
                         5.0 if compact else 8.0)
            )

            if compact:
                orb_size = max(96, min(132, height - 292))
            else:
                left_w = self.left_panel.width() if rails else 0
                right_w = self.right_panel.width() if medium else 0
                command_h = (self.command_host.sizeHint().height()
                             if show_command else 0)
                available_w = width - 48 - 32 - left_w - right_w
                # Reserve the real chrome around the reactor: body margins,
                # ONLINE line, status line, hint and the control dock.
                available_h = (height - self.title_bar.height()
                               - command_h - 250)
                orb_size = int(max(
                    110,
                    min(620, available_w * 0.94, available_h),
                ))
            if self.orb.width() != orb_size:
                self.orb.setFixedSize(orb_size, orb_size)

            self.waveform.hide()
            self.compact_telemetry.hide()
        self._place_grip()

    def _place_grip(self):
        grip = getattr(self, "size_grip", None)
        if grip is not None:
            grip.move(self.width() - grip.width() - 3,
                      self.height() - grip.height() - 3)
            grip.raise_()

    def paintEvent(self, event):
        """Paint the full-screen instrument shell behind the panels."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        # The shell is chamfered, not rounded: the reference is an angular
        # instrument plate.  The four clipped corners also keep the offscreen
        # corner-transparency contract intact.
        path = hud._cut_path(rect, 22.0)

        card = QLinearGradient(rect.topLeft(), rect.bottomRight())
        card.setColorAt(0.0, QColor("#071019"))
        card.setColorAt(0.36, QColor(_BG))
        card.setColorAt(0.72, QColor("#020609"))
        card.setColorAt(1.0, QColor("#010204"))
        painter.fillPath(path, QBrush(card))

        painter.save()
        painter.setClipPath(path)

        # Faint instrument grid: deliberately below the contrast of the text.
        grid_pen = QPen(QColor(0, 229, 255, 8), 1.0)
        painter.setPen(grid_pen)
        step = 42
        x = int(rect.left()) + 22
        while x < rect.right():
            painter.drawLine(x, int(rect.top()) + 1,
                             x, int(rect.bottom()) - 1)
            x += step
        y = int(rect.top()) + 30
        while y < rect.bottom():
            painter.drawLine(int(rect.left()) + 1, y,
                             int(rect.right()) - 1, y)
            y += step

        # Scanlines give the dark glass depth without a visible pattern.
        painter.setPen(QPen(QColor(180, 250, 255, 3), 1.0))
        for line_y in range(int(rect.top()) + 2, int(rect.bottom()), 4):
            painter.drawLine(int(rect.left()) + 1, line_y,
                             int(rect.right()) - 1, line_y)

        # Central reactor bloom; the orb paints its own tighter halo.
        glow = QRadialGradient(
            QPointF(self.width() / 2.0, self.height() * 0.45),
            max(220.0, min(self.width(), self.height()) * 0.44),
        )
        glow.setColorAt(0.0, QColor(0, 229, 255, 22))
        glow.setColorAt(0.34, QColor(0, 229, 255, 8))
        glow.setColorAt(0.68, QColor(0, 229, 255, 3))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(rect, QBrush(glow))

        # Amber bias at the extreme edges, like panel lighting.
        left_glow = QLinearGradient(rect.right() - 90, 0, rect.right(), 0)
        left_glow.setColorAt(0.0, QColor(255, 179, 0, 0))
        left_glow.setColorAt(1.0, QColor(255, 179, 0, 8))
        painter.fillRect(QRectF(rect.right() - 90, rect.top(), 90,
                                rect.height()), QBrush(left_glow))

        depth = QLinearGradient(0.0, self.height() * 0.55,
                                0.0, float(self.height()))
        depth.setColorAt(0.0, QColor(0, 0, 0, 0))
        depth.setColorAt(1.0, QColor(0, 2, 4, 190))
        painter.fillRect(rect, QBrush(depth))
        painter.restore()

        # Border, then the bright HUD corner brackets: cyan at the top-left
        # and top-right edges, with small amber datum marks below.
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(0, 130, 150, 220), 1.0))
        painter.drawPath(path)

        painter.setPen(QPen(QColor(0, 229, 255, 205), 1.4))
        length = 34.0
        inset = 11.0
        painter.drawLine(QPointF(rect.left() + inset, rect.top() + inset),
                         QPointF(rect.left() + inset + length,
                                 rect.top() + inset))
        painter.drawLine(QPointF(rect.left() + inset, rect.top() + inset),
                         QPointF(rect.left() + inset,
                                 rect.top() + inset + 20.0))
        painter.drawLine(QPointF(rect.right() - inset - length,
                                 rect.top() + inset),
                         QPointF(rect.right() - inset, rect.top() + inset))
        painter.drawLine(QPointF(rect.right() - inset, rect.top() + inset),
                         QPointF(rect.right() - inset,
                                 rect.top() + inset + 20.0))

        painter.setPen(QPen(QColor(255, 179, 0, 170), 1.2))
        painter.drawLine(QPointF(rect.left() + inset,
                                 rect.bottom() - inset),
                         QPointF(rect.left() + inset + 18.0,
                                 rect.bottom() - inset))
        painter.drawLine(QPointF(rect.right() - inset - 18.0,
                                 rect.bottom() - inset),
                         QPointF(rect.right() - inset, rect.bottom() - inset))

        highlight = QLinearGradient(rect.left(), rect.top(),
                                    rect.right(), rect.top())
        highlight.setColorAt(0.0, QColor(0, 229, 255, 0))
        highlight.setColorAt(0.30, QColor(0, 229, 255, 38))
        highlight.setColorAt(0.62, QColor(0, 229, 255, 14))
        highlight.setColorAt(0.84, QColor(255, 179, 0, 30))
        highlight.setColorAt(1.0, QColor(255, 179, 0, 0))
        painter.setPen(QPen(QBrush(highlight), 1.0))
        painter.drawLine(int(rect.left() + 34), int(rect.top()),
                         int(rect.right() - 34), int(rect.top()))
        painter.end()

    # -- state ------------------------------------------------------------ #
    def _refresh_hint(self):
        """Show the live line when there is one, otherwise the state hint."""
        if not hasattr(self, "status_hint"):
            return
        if getattr(self, "_flash_active", False):
            return
        text = getattr(self, "_live_text", "")
        self.status_hint.setText(text or _STATE_HINT.get(self._state, ""))

    def set_state(self, state):
        if self._error_hold and state != ERROR:
            return
        previous = getattr(self, "_state", IDLE)
        self._state = state
        if state == LISTENING:
            # A new turn starts with a clean slate under the orb.
            self._live_text = ""
        self.status.setText(state)
        color = QColor(_STATE_TEXT.get(state, _TEXT))
        self.status.setStyleSheet(
            f"color:{color.name()}; background:transparent;"
        )
        self.state_dot.setStyleSheet(
            f"color:{color.name()};"
            "background:transparent; font-size:9px;"
        )
        self._refresh_hint()
        self.orb.set_state(state)
        self.waveform.set_state(state)
        self.left_panel.note_state(state)
        self.right_panel.note_state(state)
        self.compact_telemetry.note_state(state)
        bar = getattr(self, "command_bar", None)
        if bar is not None:
            bar.set_state(state)

        # The title-bar presence indicator mirrors the assistant state without
        # competing with the large status line below the orb.
        connection = getattr(self, "title_bar", None)
        if connection is not None:
            if state == ERROR:
                label, tint = "Attention", _DANGER
            elif state == MUTED:
                label, tint = "Mic off", _TEXT_MUTED
            elif state == THINKING:
                label, tint = "Computing", _AMBER
            else:
                self._refresh_header_link()
                label = tint = None
            if label is not None:
                connection.set_link(label, tint)

        if previous != state:
            self._log_activity(
                f"state → {state}", "state" if state != ERROR else "error"
            )
        self._prev_state = state

        # Stop can cut a reply short *or* abandon a listen that is still waiting
        # for you to speak, so it is live whenever Jarvis is busy.
        self.stop_button.setEnabled(state in (LISTENING, SPEAKING))
        if state == IDLE:
            self._energy = 0.0
            self.orb.set_energy(0.0)
            self.waveform.set_level(0.0)
            # Jarvis is done with the microphone, so let "Hey Jarvis" have it back.
            self._resume_wake()
            self._refresh_briefing()
        if self.tray is not None:
            self.tray.setToolTip(f"JARVIS — {state}")

    def _on_level(self, value):
        """Feed one live loudness sample to the orb, waveform and telemetry."""
        try:
            self._energy = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self._energy = 0.0
        self.orb.set_energy(self._energy)
        self.waveform.set_level(self._energy)
        self.left_panel.note_energy(self._energy)
        self.compact_telemetry.note_energy(self._energy)

    def _update_telemetry(self):
        """Refresh the SANE values actually available on this machine.

        No figure in this method is synthesised.  Missing packages or metrics
        become ``UNAVAILABLE``/``—`` in the SYSTEM panel.
        """
        cpu = ram = disk = net_rate = processes = battery = temp = None
        session_uptime = int(max(0.0, time.monotonic() - self._started_at))
        system_uptime = None
        if psutil is not None:
            try:
                cpu = float(psutil.cpu_percent(interval=None))
                ram = float(psutil.virtual_memory().percent)
            except Exception:
                cpu = ram = None
            try:
                usage = psutil.disk_usage(os.path.expanduser("~"))
                disk = float(usage.percent)
            except Exception:
                disk = None
            try:
                counters = psutil.net_io_counters()
                now = time.monotonic()
                total = float(counters.bytes_sent + counters.bytes_recv)
                if self._last_net is not None and self._last_net_time:
                    elapsed = max(0.001, now - self._last_net_time)
                    net_rate = max(0.0, (total - self._last_net) / elapsed)
                else:
                    net_rate = 0.0
                self._last_net = total
                self._last_net_time = now
            except Exception:
                net_rate = None
            try:
                processes = len(psutil.pids())
            except Exception:
                processes = None
            try:
                battery_info = psutil.sensors_battery()
                if battery_info is not None:
                    battery = f"{battery_info.percent:.0f}%" + (
                        " CHG" if battery_info.power_plugged else ""
                    )
            except Exception:
                battery = None
            try:
                system_uptime = int(time.time() - psutil.boot_time())
            except Exception:
                system_uptime = None
            try:
                temperatures = psutil.sensors_temperatures()
                if temperatures:
                    entries = next(iter(temperatures.values()), [])
                    if entries:
                        temp = float(entries[0].current)
            except Exception:
                temp = None

        state = getattr(self, "_state", IDLE)
        energy = getattr(self, "_energy", 0.0)
        self.left_panel.note_state(state)
        self.left_panel.note_energy(energy)
        self.left_panel.update_system(
            cpu=cpu,
            memory=ram,
            disk=disk,
            net_rate=net_rate,
            uptime=system_uptime if system_uptime is not None
            else session_uptime,
            processes=processes,
            battery=battery,
            latency_ms=self._latency_ms,
            temp_c=temp,
        )
        self.compact_telemetry.update_metrics(
            cpu or 0.0, ram or 0.0, state, energy, session_uptime
        )
        if hasattr(self, "_session_label"):
            minutes, seconds = divmod(session_uptime, 60)
            self._session_label.setText(f"UPTIME {minutes:02d}:{seconds:02d}")
        self._telemetry_ticks += 1
        if self._telemetry_ticks % 10 == 0:
            self._refresh_briefing()

    def _on_latency(self, milliseconds):
        """Record the measured round-trip from a real request."""
        try:
            self._latency_ms = max(0.0, float(milliseconds))
        except (TypeError, ValueError):
            self._latency_ms = None
        self._log_activity(
            "round trip "
            + ("—" if self._latency_ms is None
               else f"{self._latency_ms:.0f} ms"),
            "info",
        )
        self._update_telemetry()

    # -- transcript / briefing -------------------------------------------- #
    def _on_heard(self, text):
        self.transcript.setText(_short(text, 34))
        self.right_panel.append_turn("YOU", text)
        self._live_text = f"You · {_short(text, 54)}"
        self._log_activity(f'STT "{_short(text, 120)}"', "user")
        self._refresh_hint()

    def _on_reply(self, text):
        self.right_panel.append_turn("JARVIS", text)
        self._live_text = f"Jarvis · {_short(text, 54)}"
        self._log_activity(f'CORE "{_short(text, 120)}"', "reply")
        self._refresh_hint()
        self._refresh_briefing()

    def _on_error(self, text):
        """Flash the orb red after a worker failure, then recover to Idle."""
        self._error_hold = True
        self.transcript.setText(_short(f"⚠ {text}", 34))
        self._log_activity(f"ERR {_short(text, 140)}", "error")
        self.set_state(ERROR)
        QTimer.singleShot(2800, self._clear_error)

    def _clear_error(self):
        self._error_hold = False
        if self._state == ERROR:
            self.set_state(MUTED if voice.is_mic_muted() else IDLE)

    def _refresh_briefing(self):
        """Read the existing SQLite memory into the transcript rail."""
        panel = getattr(self, "right_panel", None)
        memory = getattr(self, "memory", None)
        if panel is None or memory is None:
            return
        if not getattr(self, "_briefing_enabled", True):
            return
        entries = []
        try:
            transcript = memory.get_transcript(limit=8)
        except Exception:
            transcript = ""
        for line in (transcript or "").splitlines():
            if line.startswith("User: "):
                entries.append(("YOU", line[6:]))
            elif line.startswith("Jarvis: "):
                entries.append(("JARVIS", line[8:]))
        facts = []
        try:
            facts = memory.get_facts(limit=3)
        except Exception:
            facts = []
        panel.set_history(entries[-4:])
        panel.set_facts(facts)

    def _log_activity(self, text, level="info"):
        """Feed the real internal event stream into the terminal panel."""
        panel = getattr(self, "right_panel", None)
        if panel is not None:
            try:
                panel.log(text, level)
            except Exception:
                pass

    def _refresh_header_link(self):
        """Show the real backend mode: cloud link or local/offline router."""
        title = getattr(self, "title_bar", None)
        if title is None:
            return
        if config.is_api_key_configured():
            title.set_link("Cloud link", _TEAL)
        else:
            title.set_link("Local mode", _AMBER)

    def _flash(self, text):
        """Show a short-lived contextual line under the orb and in the sink."""
        self.transcript.setText(text)
        self._flash_active = True
        self._log_activity(f"SYS {_short(text, 150)}", "warn")
        if hasattr(self, "status_hint"):
            self.status_hint.setText(text)
        token = getattr(self, "_flash_token", 0) + 1
        self._flash_token = token

        def restore():
            if getattr(self, "_flash_token", token) != token:
                return
            self.transcript.setText("")
            self._flash_active = False
            self._refresh_hint()

        QTimer.singleShot(2200, restore)

    # -- typed command console ------------------------------------------- #
    def _focus_command(self):
        bar = getattr(self, "command_bar", None)
        if bar is None or not bar.isVisible():
            self._flash("Command console is hidden at this window size")
            return
        bar.focus_input()

    def _submit_text(self, text):
        """Dispatch one command bar entry through the real ``main.handle`` path."""
        text = " ".join(str(text or "").split())
        if not text:
            return
        bar = getattr(self, "command_bar", None)
        worker = getattr(self, "worker", None)
        if worker is not None and worker.isRunning():
            self._log_activity("BUSY voice session is using the dispatcher", "warn")
            self._flash("Voice session active — press Stop first")
            return
        text_worker = getattr(self, "text_worker", None)
        if text_worker is not None and text_worker.isRunning():
            self._flash("A typed command is already running")
            return

        self._log_activity(f'USR "{_short(text, 160)}"', "user")
        self.right_panel.append_turn("YOU", text)
        if is_shutdown(text):
            # Same explicit shutdown phrase as the voice worker; the exit is
            # intentional, but still goes through the guarded quit path.
            self._live_text = "Shutting down…"
            self._refresh_hint()
            self._flash("Shutting down, sir.")
            QTimer.singleShot(700, self.quit)
            return

        bar.set_busy(True)
        self._log_activity("EXEC routing through the local router / agent", "info")
        worker = TextCommandWorker(self.memory, text, self)
        worker.state_changed.connect(self.set_state)
        worker.replied.connect(self._on_text_reply)
        worker.failed.connect(self._on_text_failed)
        worker.latency.connect(self._on_latency)
        worker.kind.connect(
            lambda kind: self._log_activity(f"ROUTE {kind or 'agent'}", "info")
        )
        worker.finished.connect(self._on_text_done)
        self.text_worker = worker
        worker.start()

    def _on_text_reply(self, text):
        self._on_reply(text)

    def _on_text_failed(self, text):
        self._on_error(text)

    def _on_text_done(self):
        worker = getattr(self, "text_worker", None)
        self.text_worker = None
        if worker is not None:
            worker.deleteLater()
        bar = getattr(self, "command_bar", None)
        if bar is not None:
            bar.set_busy(False)
            bar.focus_input()
        self._log_activity("READY", "state")

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

    # -- global hotkey (Ctrl+Shift+J / Ctrl+Alt+J) ------------------------ #
    def _start_hotkey(self):
        """Start the global hotkey (best effort).

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
            logger.log_command_error("Global hotkey unavailable", exc)
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
        button = getattr(self, "auto_button", None)
        if button is not None:
            button.setChecked(self._chat)
            button.setText("AUTO  ON" if self._chat else "AUTO  OFF")
        panel = getattr(self, "left_panel", None)
        if panel is not None:
            panel.set_mode(self._chat)
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
        """Reflect the wake-word state in the menu, dock pill and telemetry."""
        action = getattr(self, "wake_action", None)
        if action is not None:
            action.setChecked(on)
        button = getattr(self, "wake_button", None)
        if button is not None:
            button.setChecked(on)
            button.setText("WAKE  ON" if on else "WAKE  OFF")
        panel = getattr(self, "left_panel", None)
        if panel is not None:
            panel.set_wake(on)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.sync_wake(on)

    # -- mic mute --------------------------------------------------------- #
    def toggle_mute(self, _checked=False):
        """Mute/unmute the microphone and reflect it everywhere."""
        self.set_mic_muted(not voice.is_mic_muted())

    def _sync_mic_ui(self, muted):
        """Reflect the real voice mute state without changing it (worker signal)."""
        self.mic_button.setChecked(muted)
        self.mic_button.setText(_MUTED_LABEL if muted else _MIC_LABEL)
        self.mic_button.setToolTip(
            "Microphone muted — click to turn it on" if muted
            else "Microphone on — click to mute"
        )
        self.mic_button.update()
        action = getattr(self, "mute_action", None)
        if action is not None:
            action.setChecked(muted)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.sync_mute(muted)

    def set_mic_muted(self, muted):
        """Mute or unmute the microphone without ever blocking the event loop.

        Mute is capture-only: it flips the voice flag, asks the wake listener
        to stop opening the device, and repaints the orb. Every call that could
        wait for the audio device is deliberately non-blocking, because this
        slot runs on the GUI thread. The old ``wake.pause()`` waited up to two
        seconds for arecord to close, which made the whole window look frozen.
        """
        worker = self.worker
        was_running = bool(worker is not None and worker.isRunning())
        if muted:
            # Remember whether this mute interrupted a real listening session so
            # unmuting can resume it instead of leaving Jarvis idle.
            self._mute_interrupted = was_running

        muted = voice.set_mic_muted(muted)
        self._sync_mic_ui(muted)
        if muted:
            # Stop the wake word from reopening the microphone, but do not wait
            # for it here: timeout=0 clears its resume flag and returns at once,
            # and its own thread closes arecord on the next loop tick.
            self._pause_wake_nonblocking()
            # A frozen-looking orb must not keep the last loudness sample alive.
            self._energy = 0.0
            self.orb.set_energy(0.0)
            self.waveform.set_level(0.0)
            self.set_state(MUTED)
            return

        if self.status.text() == MUTED:
            # set_state(IDLE) also resumes the wake listener.
            self.set_state(IDLE)
        else:
            # A cycle already moved the label off "Muted" (e.g. muted mid-listen),
            # so set_state(IDLE) above will not run: hand the mic back here.
            self._resume_wake()
        # If mute cut an active session short, picking the mic up again should
        # actually resume listening rather than just show Idle.
        if self._mute_interrupted:
            self._resume_listening_after_mute()

    def _pause_wake_nonblocking(self):
        """Stop the wake listener from opening the mic without waiting for it.

        ``wake.Listener.pause`` normally blocks until arecord has actually been
        released. Called from the GUI thread that is a visible freeze, so ask it
        for a zero-timeout pause (flag only) and let its own thread do the
        closing. A listener without timeout support is paused on a helper
        thread so the event loop is never held up either way.
        """
        listener = self._wake
        if listener is None:
            return
        try:
            listener.pause(timeout=0.0)
            return
        except TypeError:
            pass
        except Exception as exc:
            logger.log_command_error("Could not pause the wake listener", exc)
            return
        try:
            threading.Thread(
                target=listener.pause,
                name="jarvis-wake-pause",
                daemon=True,
            ).start()
        except Exception as exc:
            logger.log_command_error("Could not pause the wake listener", exc)

    def _resume_listening_after_mute(self):
        """Restart a listening session that was interrupted by muting.

        If the old worker is still running, wait for its ``finished`` signal and
        only restart when it actually left *because* of the mute -- a session
        that ends normally afterwards must not spawn an extra listen. Otherwise
        open a fresh cycle immediately. Nothing happens while the mic is still
        muted or the app is quitting.
        """
        if voice.is_mic_muted() or getattr(self, "_quitting", False):
            return
        worker = self.worker
        if worker is not None and worker.isRunning():
            finished = getattr(worker, "finished", None)
            if finished is not None:
                def _worker_done(_worker=worker):
                    self._finish_mute_resume(_worker)

                try:
                    finished.connect(_worker_done)
                except (TypeError, RuntimeError):
                    pass
            return
        self._mute_interrupted = False
        self.trigger()

    def _finish_mute_resume(self, worker):
        """Handle a worker that finished while an unmute was pending."""
        if worker is not self.worker:
            return
        interrupted = getattr(worker, "stopped_for_mute", False)
        self._mute_interrupted = False
        if interrupted and not voice.is_mic_muted() \
                and not getattr(self, "_quitting", False):
            self.trigger()

    # -- stop ------------------------------------------------------------- #
    def _halt_for_hide(self):
        """Cancel listening/speaking and force the card back to Idle/Muted.

        Hiding to the tray must not leave a worker holding the microphone or a
        reply playing invisibly. The worker's later state signal is detached so
        its final MUTED/IDLE update cannot overwrite the forced state after the
        window is already hidden.
        """
        if hasattr(self, "_error_hold"):
            self._error_hold = False
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
        self.set_state(MUTED if voice.is_mic_muted() else IDLE)

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
    def trigger(self):
        """Start a listen/process/speak session (ignored if already busy).

        With continuous mode on, the session keeps going: Jarvis opens the mic
        again after every reply and only stops when you go quiet, say an exit
        word, press Stop or mute the mic. A manual trigger (orb, tray Talk or
        hotkey) is an explicit request to talk, so it lifts a previous mute
        instead of leaving the app stuck in the muted state.
        """
        if voice.is_mic_muted():
            self.set_mic_muted(False)
        text_worker = getattr(self, "text_worker", None)
        if text_worker is not None and text_worker.isRunning():
            self._flash("A typed command is still running — please wait")
            return
        # A new explicit turn cancels a lingering error hold so the orb can
        # immediately show Listening instead of remaining red.
        self._error_hold = False
        # Give the microphone up first: the wake listener and listen() must never
        # hold it at the same time.
        if self._wake is not None:
            self._wake.pause()
        if self.worker is not None and self.worker.isRunning():
            # A cycle is already running: leave the mic with it.
            return
        if not self.isVisible():
            self.show()
        self._log_activity("MIC opening capture for a voice turn", "state")
        self.worker = JarvisWorker(self.memory, self, chat=self._chat)
        self.worker.state_changed.connect(self.set_state)
        self.worker.message.connect(self._log)
        self.worker.message.connect(self._on_worker_message)
        self.worker.heard.connect(self._on_heard)
        self.worker.level.connect(self._on_level)
        self.worker.mic_changed.connect(self._sync_mic_ui)
        self.worker.exit_requested.connect(self.quit)
        replied = getattr(self.worker, "replied", None)
        if replied is not None:
            replied.connect(self._on_reply)
        failed = getattr(self.worker, "failed", None)
        if failed is not None:
            failed.connect(self._on_error)
        latency = getattr(self.worker, "latency", None)
        if latency is not None:
            latency.connect(self._on_latency)
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
        timer = getattr(self, "_telemetry_timer", None)
        if timer is not None:
            timer.stop()
        self._stop_hotkey()
        if self.wake_server is not None:
            self.wake_server.stop()
            self.wake_server = None
        if self._wake is not None:
            self._wake.stop()
        voice.stop_speaking()
        telemetry = getattr(self, "left_panel", None)
        if telemetry is not None:
            try:
                telemetry.stop()
            except Exception:
                pass
        if self.tray is not None:
            self.tray.hide()
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(5000)
        text_worker = getattr(self, "text_worker", None)
        if text_worker is not None and text_worker.isRunning():
            text_worker.wait(5000)
        QApplication.quit()

    def _on_worker_message(self, text):
        """Mirror worker diagnostics into the visible activity stream."""
        level = "error" if str(text).strip().startswith("⚠") else "info"
        self._log_activity(str(text), level)

    @staticmethod
    def _log(text):
        print(text, flush=True)

    # -- window drag + resize (frameless) --------------------------------- #
    def _watched_surface(self, obj):
        """True for the transparent containers that host the window chrome."""
        return (obj is getattr(self, "title_bar", None)
                or obj is getattr(self, "body_host", None)
                or obj is getattr(self, "command_host", None))

    def eventFilter(self, obj, event):
        """Route drags/resizes that start on the transparent HUD surfaces.

        Child labels and panels keep their own mouse handling; this filter is
        only installed on the containers themselves, so a press reaches it only
        when it landed on empty header/body/console space. Returning ``True``
        for a gesture keeps the container from starting an unrelated action.
        """
        if not self._watched_surface(obj):
            return super().eventFilter(obj, event)

        kind = event.type()
        if kind == QEvent.MouseButtonPress:
            if event.button() == Qt.RightButton:
                # Right-clicking empty card space should still open the session
                # menu even though the container, not JarvisWidget, received it.
                self._show_menu(event.globalPos())
                event.accept()
                return True
            if event.button() == Qt.LeftButton and not self._is_maximized:
                edges = self._edge_at(self.mapFromGlobal(event.globalPos()))
                if edges:
                    self._begin_resize(edges, event.globalPos())
                    event.accept()
                    return True
                if obj is not self.title_bar:
                    # Empty body/console space behaves like the header.
                    self._begin_move(event.globalPos())
                    event.accept()
                    return True
        elif kind == QEvent.MouseMove:
            if self._resize_edges:
                self._apply_resize(event.globalPos())
                event.accept()
                return True
            if (self._drag_offset is not None
                    and event.buttons() & Qt.LeftButton):
                self.move(event.globalPos() - self._drag_offset)
                event.accept()
                return True
            if not event.buttons() and not self._is_maximized:
                self.setCursor(self._cursor_for_edges(
                    self._edge_at(self.mapFromGlobal(event.globalPos()))
                ))
        elif kind == QEvent.MouseButtonRelease:
            if self._resize_edges or self._drag_offset is not None:
                self._reset_pointer_state()
                event.accept()
                return True
        elif kind == QEvent.Leave:
            if not self._resize_edges and self._drag_offset is None:
                self.setCursor(Qt.ArrowCursor)
        return super().eventFilter(obj, event)

    def _begin_move(self, global_pos):
        """Start moving the card from a mouse press at ``global_pos``."""
        self._resize_edges = set()
        self._resize_origin = None
        self._resize_start = None
        self._drag_offset = global_pos - self.frameGeometry().topLeft()
        self.setCursor(Qt.SizeAllCursor)

    def _begin_resize(self, edges, global_pos):
        """Start an edge/corner resize from a mouse press at ``global_pos``."""
        self._drag_offset = None
        self._resize_edges = set(edges)
        self._resize_origin = global_pos
        self._resize_start = self.geometry()
        self.setCursor(self._cursor_for_edges(self._resize_edges))

    def _reset_pointer_state(self):
        """Forget any in-flight move/resize and restore the normal cursor."""
        self._drag_offset = None
        self._resize_edges = set()
        self._resize_origin = None
        self._resize_start = None
        self.setCursor(Qt.ArrowCursor)

    def _edge_at(self, pos):
        """Return the set of window edges/corners under ``pos`` (empty = none).

        Used both to change the cursor on hover and to decide whether a press
        starts a resize instead of a move. A maximised card has no resize
        border, so it never reports an edge.
        """
        if self._is_maximized:
            return set()
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
        if self._is_maximized:
            # Pinned to the work area: neither edge-resize nor move.
            self._reset_pointer_state()
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            edges = self._edge_at(event.pos())
            if edges:
                # Near an edge/corner: resize rather than move.
                self._begin_resize(edges, event.globalPos())
            else:
                self._begin_move(event.globalPos())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._is_maximized:
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        if self._resize_edges:
            self._apply_resize(event.globalPos())
        elif self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
        else:
            # Hovering (no button): show the matching resize cursor.
            self.setCursor(self._cursor_for_edges(self._edge_at(event.pos())))
        event.accept()

    def mouseReleaseEvent(self, event):
        self._reset_pointer_state()
        event.accept()

    def _apply_resize(self, global_pos):
        """Resize the window by the drag delta, honouring the minimum size."""
        if self._is_maximized:
            return
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_responsive()
        self._place_grip()

    def leaveEvent(self, event):
        if not self._resize_edges:
            self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if (event.key() == Qt.Key_K
                and event.modifiers() & Qt.ControlModifier):
            self._focus_command()
        elif event.key() == Qt.Key_Escape:
            self._halt_for_hide()            # same clean Idle state as the ✕
            self.hide()                      # hide to tray, don't quit
        elif (event.key() == Qt.Key_Q
              and event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier)):
            self.quit()
        elif event.key() == Qt.Key_M and not event.modifiers():
            self.toggle_mute()
        elif event.key() == Qt.Key_Space and not event.modifiers():
            self._stop()
        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_responsive()
        # Reopening the card while the mic is muted must show the muted state,
        # not a misleading blue Idle orb.
        if voice.is_mic_muted():
            self.set_state(MUTED)
        else:
            self._refresh_header_link()
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
            telemetry = getattr(self, "left_panel", None)
            if telemetry is not None:
                try:
                    telemetry.stop()
                except Exception:
                    pass
            if self.worker is not None and self.worker.isRunning():
                self.worker.wait(5000)
            text_worker = getattr(self, "text_worker", None)
            if text_worker is not None and text_worker.isRunning():
                text_worker.wait(5000)
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


def main():
    # Crisp text/painting on scaled displays (125 %, 150 %, ...). Must be set
    # before QApplication is constructed; newer Qt versions do this themselves,
    # and getattr keeps the app working on a build that lacks the attribute.
    for attribute in ("AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"):
        value = getattr(Qt, attribute, None)
        if value is not None:
            QApplication.setAttribute(value, True)

    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")
    app.setWindowIcon(app_icon(256))
    # Keep running after the floating window is hidden/closed -> tray lives on.
    app.setQuitOnLastWindowClosed(False)
    configured = run_setup_wizard()
    # The application opens the wide cinematic command center; the compact
    # floating card remains the default for embedders and the offscreen tests.
    widget = JarvisWidget(compact=False)
    if not configured:
        widget._flash("Offline mode — add a Groq API key to enable AI")

    # Let a second launch / `main.py --wake` wake this instance instead of
    # spawning a duplicate tray icon. The socket thread only emits a signal, so
    # Qt runs the real work on the GUI thread. `main.py --quit` (or a platform
    # without AF_UNIX, which terminates by PID) closes the app from the terminal.
    import instance
    server = instance.WakeServer(widget.hotkey_pressed.emit,
                                 on_quit=widget.quit_requested.emit)
    server.start()
    widget.wake_server = server

    widget._center()
    widget.show()
    try:
        return app.exec_()
    finally:
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
